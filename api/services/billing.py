"""
Payments with Stripe: subscriptions to tools (monthly or annual).

A consultant's SETUP fee and ONGOING HELP are sold in
api/services/consulting_billing.py (Stripe Connect: the consultant gets their
share); this file routes their webhook events there. Hourly consulting sessions
are paid on Cal.com (the free first call, then PAID_BOOKING_URL) — see
api/services/consultations.py.

WHAT THIS FILE DOES
-------------------
  start_checkout(account, tools, interval) -> a Stripe Checkout page for a "build your
                                              own" package (or, if they already have a
                                              subscription, change its tools in place)
  billing_portal(account)                  -> Stripe's page to change card / CANCEL ANYTIME
  handle_webhook(payload, signature)       -> Stripe tells us what happened; we update
                                              dim.subscriptions (db/schema.sql)
  live_plan_services(account)              -> services unlocked by live subscriptions
  set_tester(account, on)                  -> an admin makes someone a tester (no charge)

THE AI RULE
-----------
AI answers questions about the client's STORED data, so the AI tool can't be
picked until the account has data in fact.account_data (Data Engineering ->
"Load into database"). `ai_ready(account)` says whether it has.

HOW THE PIECES FIT
------------------
1. scripts/sync_stripe.py creates one Stripe PRODUCT per tool (and the All
   tools bundle) in api/services/pricing.py, each with two PRICES found by
   "lookup key": dcp_<tool>_month and dcp_<tool>_year.
   A client's subscription has one ITEM per tool they picked; the total is the
   sum of those prices.
2. The site asks for a Checkout page; the client pays on Stripe's own page
   (card details never touch our server).
3. Stripe calls POST /api/billing/webhook. We check Stripe's signature
   (STRIPE_WEBHOOK_SECRET) so nobody can fake "paid", then save the result.
   The webhook is the ONLY thing that turns a paid plan on or off.

PLACEHOLDER MODE (no STRIPE_SECRET_KEY) — and TESTERS
-----------------------------------------------------
For testing without Stripe: choosing a plan switches it on at once with no
charge, and "cancel" switches it off.
The site says "test mode" so nobody thinks they paid.
A TESTER (dim.accounts.is_tester, set in the Admin panel) always gets this
mode, even when Stripe is live — so people can try the paid tools for free
while real clients pay (`charges_real_money`).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store, offers, pricing, stripe_catalog

log = logging.getLogger(__name__)

ADMIN_FREE = "Admins have every tool for free — there's nothing to buy."
AI_NEEDS_DATA = ("AI answers questions about your stored data, so load your data first: "
                 "Data Engineering -> Load into database. Then add AI.")


def stripe_enabled() -> bool:
    """
    Tell whether real Stripe payments are switched on.

    Payments are live when STRIPE_SECRET_KEY is set; otherwise the app runs in
    PLACEHOLDER (test) mode, where plans switch on at once with no charge.

    Returns:
        bool: True if STRIPE_SECRET_KEY is set.

    Example:
        >>> stripe_enabled()   # local development, no key
        False
    """
    return bool(get_settings().stripe_secret_key)


def charges_real_money(account: dict) -> bool:
    """
    Tell whether THIS account pays through Stripe: Stripe is on and they aren't a tester.

    Testers (accounts.is_tester, set by an admin) get test mode even when
    Stripe is live: packages switch on at once and nothing is charged.

    Args:
        account: dict, the plan holder's accounts row.

    Returns:
        bool: True when checkout goes to Stripe for this account.

    Example:
        >>> charges_real_money({"is_tester": True})   # even with STRIPE_SECRET_KEY set
        False
    """
    return stripe_enabled() and not account.get("is_tester")


def _stripe():
    """
    Return the Stripe library, set up with our secret key (imported only when used).

    Importing lazily means the app (and tests) run without the `stripe` package
    being loaded in placeholder mode.

    Returns:
        module: the `stripe` module with `stripe.api_key` set.

    Example:
        >>> _stripe().Price.list(lookup_keys=["dcp_all_tools_month"], limit=1)
    """
    import stripe

    stripe.api_key = get_settings().stripe_secret_key
    return stripe


def _price_id(lookup_key: str) -> str:
    """
    Find a Stripe price by its lookup key (created by scripts/sync_stripe.py).

    Only active prices are searched; the first match is used.

    Args:
        lookup_key: str, e.g. "dcp_data_cleaning_month" (see pricing.lookup_key).

    Returns:
        str: the Stripe price id (e.g. "price_1Abc...").

    Raises:
        HTTPException: 503 if no active price has that lookup key yet.

    Example:
        >>> _price_id(pricing.lookup_key("all_tools", "year"))
        'price_1Abc...'
    """
    prices = _stripe().Price.list(lookup_keys=[lookup_key], active=True, limit=1)
    if not prices.data:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="That price isn't set up in Stripe yet — run scripts/sync_stripe.py.")
    return prices.data[0].id


async def _customer_id(account: dict) -> str:
    """
    Return the account's Stripe customer id, creating the customer the first time they pay.

    A new customer is named after the company (or the person's full name), tagged
    with our account id in its metadata, and its id is saved on the account row
    (and on the `account` dict passed in) so it is only created once.

    Args:
        account: dict, the accounts row of the client paying.

    Returns:
        str: the Stripe customer id (e.g. "cus_...").

    Example:
        >>> await _customer_id(account)
        'cus_Q1w2e3...'
    """
    if account.get("stripe_customer_id"):
        return account["stripe_customer_id"]
    name = account.get("company_name") or f"{account['first_name']} {account['last_name']}"
    customer = _stripe().Customer.create(email=account["email"], name=name,
                                         metadata={"account_id": str(account["id"])})
    await account_store.set_stripe_customer(str(account["id"]), customer.id)
    account["stripe_customer_id"] = customer.id
    return customer.id


async def ai_ready(account: dict) -> bool:
    """
    Tell whether this account may add the AI tool: it has data stored in fact.account_data.

    Args:
        account: dict, the plan holder's accounts row.

    Returns:
        bool: True once at least one row is stored for the account.

    Example:
        >>> await ai_ready(new_client)
        False
    """
    from api.services import warehouse_store    # imported here: warehouse_store is the bigger module

    return await warehouse_store.has_data(account)


def _selection(plan_ids: list[str], interval: str) -> list[str]:
    """
    Validate a package (400 with a plain-English message if it doesn't make sense).

    Args:
        plan_ids: list[str], the Subscription ids the client picked.
        interval: str, "month" or "year".

    Returns:
        list[str]: the normalized selection (see pricing.normalize_selection).

    Raises:
        HTTPException: 400 if the interval is not "month"/"year", or the selection
            is invalid (unknown tool, missing requirement).

    Example:
        >>> _selection(["data_visualization"], "month")
        ['data_cleaning', 'data_visualization']
    """
    if interval not in pricing.INTERVALS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose monthly or yearly billing.")
    try:
        return pricing.normalize_selection(plan_ids)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


def _as_dict(obj):
    """
    Convert Stripe objects to plain dicts (newer stripe libraries return non-dict objects).

    Args:
        obj: a Stripe object (anything with `to_dict()`) or an already-plain value.

    Returns:
        dict | Any: `obj.to_dict()` when available, otherwise `obj` unchanged.

    Example:
        >>> _as_dict({"id": "sub_1"})
        {'id': 'sub_1'}
    """
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


async def start_checkout(account: dict, plan_ids: list[str], interval: str) -> dict:
    """
    Subscribe to a package of tools, billed monthly or yearly.

      * No live subscription -> Stripe Checkout with one line per billable tool
        (test mode, or a tester: switched on at once, nothing charged).
      * Already subscribed   -> the subscription's tools (and interval) are
        changed in place; Stripe prorates the difference on the next invoice.

    When the interval changes, every old item is removed and new prices are
    added. Items not billable yet (AI before launch) are not sent to Stripe.

    Args:
        account: dict, the accounts row of the plan holder.
        plan_ids: list[str], the Subscription ids picked.
        interval: str, "month" or "year".

    A FIRST package gets one offer (api/services/offers.py): a free trial when
    they qualify (Stripe's trial_period_days; in test mode the package starts as
    "trialing"), otherwise the seasonal sale running today (a Stripe coupon off
    the first payment). Changing an existing package gets neither.

    Returns:
        dict: {"url", "activated", "updated", "test_mode", "trial_days", "sale"} —
            "url" is the Stripe Checkout page to send the client to (None when
            nothing needs paying there), "activated" is True when the plan
            switched on at once (test mode), "updated" is True when an existing
            subscription was changed; "trial_days" / "sale" = the offer applied.

    Raises:
        HTTPException: 400 for an admin (ADMIN_FREE: everything is free for
            them), an invalid package or interval, or AI without
            stored data (AI_NEEDS_DATA); 409 if the package
            and interval are already the current ones; 503 if a Stripe price is
            missing.

    Example:
        >>> await start_checkout(account, ["data_visualization"], "month")   # test mode
        {'url': None, 'activated': True, 'updated': False, 'test_mode': True}
    """
    if account.get("is_admin") or account["email"].lower() in get_settings().admin_email_list:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ADMIN_FREE)
    picked = _selection(plan_ids, interval)
    if "ai" in picked and not await ai_ready(account):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=AI_NEEDS_DATA)
    billable = [p for p in picked if pricing.billable(p)]   # AI is free until it launches
    live = await account_store.list_subscriptions(str(account["id"]))
    current = live[0] if live else None
    if current and sorted(i["plan_id"] for i in current["items"]) == sorted(picked) \
            and current["billing_interval"] == interval:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That's already your package.")

    offer = (await offers.offer_for(account, billable) if not current
             else {"applies": None, "trial_days": 0, "sale": None})
    trial = offer["trial_days"] if offer["applies"] == "trial" else 0
    sale = offer["sale"] if offer["applies"] == "sale" else None

    if not charges_real_money(account):   # test mode, or a tester: on at once, no charge
        await account_store.upsert_subscription(
            account_id=str(account["id"]), billing_interval=interval, status="trialing" if trial else "active",
            items=[{"plan_id": p} for p in picked], subscription_id=str(current["id"]) if current else None,
            current_period_end=datetime.now(timezone.utc) + timedelta(days=trial) if trial else None)
        if trial:
            await account_store.mark_trial_used(str(account["id"]))
        return {"url": None, "activated": True, "updated": bool(current), "test_mode": True,
                "trial_days": trial, "sale": sale}

    stripe = _stripe()
    # Tools picked but not charged yet (AI before launch) never reach Stripe, so
    # they ride along in the subscription's metadata and come back with the webhook.
    meta = {"account_id": str(account["id"]), "free_plans": ",".join(p for p in picked if p not in billable)}
    if current and current.get("stripe_subscription_id"):
        # Change the existing subscription IN PLACE (one subscription, one receipt):
        # remove Stripe items no longer wanted (all of them when the interval changes),
        # add the new ones. Switching to All tools = every tool removed, the bundle added.
        keep = {i["plan_id"]: i for i in current["items"]} if current["billing_interval"] == interval else {}
        changes = [{"id": i["stripe_subscription_item_id"], "deleted": True}
                   for pid, i in {x["plan_id"]: x for x in current["items"]}.items()
                   if i.get("stripe_subscription_item_id") and (pid not in keep or pid not in billable)]
        changes += [{"price": _price_id(pricing.lookup_key(pid, interval))} for pid in billable if pid not in keep]
        sub = stripe.Subscription.modify(
            current["stripe_subscription_id"], **({"items": changes} if changes else {}),
            # Bill (or credit) the difference NOW: an annual client adding a tool in
            # month 2 pays for the rest of that year today, not at next year's renewal.
            proration_behavior="always_invoice",
            # Changing the package undoes a "cancel at the end of the period".
            cancel_at_period_end=False,
            metadata=meta)
        await _save_subscription(_as_dict(sub))   # the webhook will confirm the same thing
        return {"url": None, "activated": False, "updated": True, "test_mode": False, "trial_days": 0, "sale": None}

    site = get_settings().site_url.rstrip("/")
    sub_data = {"metadata": meta}   # copied onto the subscription, so the webhook knows whose package it is
    if trial:
        sub_data["trial_period_days"] = trial                        # Stripe charges nothing until it ends
    # Stripe takes EITHER a coupon OR the "add promotion code" box, not both.
    discount = ({"discounts": [{"coupon": stripe_catalog.ensure_coupon(stripe, sale)}]} if sale
                else {"allow_promotion_codes": True})
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=await _customer_id(account),
        line_items=[{"price": _price_id(pricing.lookup_key(pid, interval)), "quantity": 1} for pid in billable],
        client_reference_id=str(account["id"]),
        subscription_data=sub_data,
        **discount,
        success_url=f"{site}/workspace/settings/billing?checkout=success",
        cancel_url=f"{site}/workspace/settings/billing?checkout=canceled",
    )
    return {"url": session.url, "activated": False, "updated": False, "test_mode": False,
            "trial_days": trial, "sale": sale}


async def billing_portal(account: dict) -> dict:
    """
    Open Stripe's customer portal: update the card, see invoices, cancel anytime.

    Args:
        account: dict, the accounts row of the client.

    Returns:
        dict: {"url": str}, the portal page; it returns to <SITE_URL>/workspace/settings/billing.

    Raises:
        HTTPException: 400 in test mode or for a tester (cancel from the workspace instead).

    Example:
        >>> await billing_portal(account)
        {'url': 'https://billing.stripe.com/p/session/...'}
    """
    if not charges_real_money(account):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Test mode: cancel the plan from your workspace.")
    session = _stripe().billing_portal.Session.create(
        customer=await _customer_id(account), return_url=f"{get_settings().site_url.rstrip('/')}/workspace/settings/billing")
    return {"url": session.url}


async def cancel_placeholder(account: dict) -> None:
    """
    Switch the subscription off in test mode, or for a tester (with Stripe, the portal does this).

    Every live subscription of the account is marked "canceled".

    Args:
        account: dict, the accounts row of the plan holder.

    Returns:
        None.

    Raises:
        HTTPException: 400 when this account pays through Stripe (use "Manage
            billing"), or when there is no live subscription to cancel.

    Example:
        >>> await cancel_placeholder(account)   # test mode, after start_checkout
    """
    if charges_real_money(account):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Use “Manage billing” to cancel.")
    live = await account_store.list_subscriptions(str(account["id"]))
    if not live:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="There's no active subscription to cancel.")
    for s in live:
        await account_store.set_subscription_status(str(s["id"]), "canceled")


async def cancel_everything(account: dict) -> int:
    """
    Stop all billing for an account that is being deleted ("Delete my account").

    With Stripe on, each live subscription is canceled IN STRIPE right away (so
    the card is never charged again), and the Stripe customer is deleted. In
    test mode the rows are just marked canceled. The account's rows are then
    removed by account_store.delete_account.

    Args:
        account: dict, the plan holder's accounts row.

    Returns:
        int: how many subscriptions were canceled.

    Example:
        >>> await cancel_everything(owner)
        1
    """
    from api.services import consulting_billing   # imported here: it imports this file

    await consulting_billing.cancel_all(account)   # ongoing help from a consultant, too
    live = await account_store.list_subscriptions(str(account["id"]))
    if stripe_enabled():
        stripe = _stripe()
        for s in live:
            if s.get("stripe_subscription_id"):
                stripe.Subscription.cancel(s["stripe_subscription_id"])
        if account.get("stripe_customer_id"):
            stripe.Customer.delete(account["stripe_customer_id"])
    for s in live:
        await account_store.set_subscription_status(str(s["id"]), "canceled")
    return len(live)


async def live_plan_services(account_id: str) -> tuple[list[str], list[dict]]:
    """
    Return the service ids unlocked by live subscriptions' tools, and the live subscriptions.

    Live means status "active", "trialing" or "past_due" (account_store.LIVE_STATUSES).
    Service ids are de-duplicated, keeping first-seen order.

    Args:
        account_id: str, the plan holder's account id.

    Returns:
        tuple[list[str], list[dict]]: (service ids, the live subscription rows,
            each with its "items").

    Example:
        >>> services, subs = await live_plan_services(account_id)
        >>> services
        ['data_cleaning', 'data_visualization']
    """
    subs = await account_store.list_subscriptions(account_id)
    services: list[str] = []
    for s in subs:
        for item in s["items"]:
            services += pricing.plan_services(item["plan_id"])
    return list(dict.fromkeys(services)), subs


# ── Webhook ───────────────────────────────────────────────────────────────────

def _period_end(sub) -> datetime | None:
    # Newer Stripe API versions keep the period end on each subscription item.
    """
    Read when a Stripe subscription's current billing period ends.

    Newer Stripe API versions keep the period end on each subscription item, so
    the first item is checked when the subscription itself has none.

    Args:
        sub: dict, a Stripe subscription as a plain dict.

    Returns:
        datetime | None: the period end in UTC, or None if Stripe did not send one.

    Example:
        >>> _period_end({"current_period_end": 1767225600})
        datetime.datetime(2026, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)
    """
    end = sub.get("current_period_end")
    if end is None:
        items = (sub.get("items") or {}).get("data") or []
        end = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(end, tz=timezone.utc) if end else None


async def _save_subscription(sub) -> None:
    """
    Save a Stripe subscription (and its items) to our subscriptions tables.

    The account is found from the subscription's metadata ("account_id"), or else
    by its Stripe customer id. Each item's price lookup key says which tool and
    interval it is (pricing.plan_from_lookup_key); unknown prices are skipped with
    a warning. A subscription with no matching account or no known tools is
    ignored (logged, not raised), so a stray webhook never crashes the server.

    Args:
        sub: dict, a Stripe subscription as a plain dict (from the webhook or
            Subscription.modify).

    Returns:
        None. Upserts the dim.subscriptions row (its tools in `items`).

    Example:
        >>> await _save_subscription(event["data"]["object"])
    """
    meta = sub.get("metadata") or {}
    account_id = meta.get("account_id")
    if not account_id:
        acc = await account_store.get_account_by_stripe_customer(sub.get("customer"))
        account_id = str(acc["id"]) if acc else None
    if not account_id:
        log.warning("Stripe subscription %s has no matching account; ignored.", sub.get("id"))
        return
    # One item per tool; each price's lookup key says which tool and interval.
    items, interval = [], None
    for item in (sub.get("items") or {}).get("data") or []:
        price = item.get("price") or {}
        found = pricing.plan_from_lookup_key(price.get("lookup_key") or "")
        if not found:
            log.warning("Stripe subscription %s has an unknown price (%s); item ignored.",
                        sub.get("id"), price.get("lookup_key"))
            continue
        items.append({"plan_id": found[0], "stripe_subscription_item_id": item.get("id")})
        interval = found[1]
    if not items or interval is None:
        log.warning("Stripe subscription %s has no known tools; ignored.", sub.get("id"))
        return
    # Tools picked but not charged yet (AI before launch) came along in the metadata.
    for pid in (meta.get("free_plans") or "").split(","):
        if pid in pricing.SUBSCRIPTIONS_BY_ID and all(i["plan_id"] != pid for i in items):
            items.append({"plan_id": pid, "stripe_subscription_item_id": None})
    if sub.get("status") == "trialing":
        await account_store.mark_trial_used(account_id)   # one free trial per account
    await account_store.upsert_subscription(
        account_id=account_id, billing_interval=interval, status=sub.get("status"), items=items,
        stripe_subscription_id=sub.get("id"), stripe_customer_id=sub.get("customer"),
        cancel_at_period_end=bool(sub.get("cancel_at_period_end")), current_period_end=_period_end(sub),
    )
    # Why they canceled (Stripe's customer portal asks, when "Collect cancellation reason" is on).
    details = sub.get("cancellation_details") or {}
    if details.get("feedback") or details.get("comment") or sub.get("canceled_at"):
        when = sub.get("canceled_at")
        await account_store.set_cancellation(sub.get("id"), details.get("feedback"), details.get("comment"),
                                             datetime.fromtimestamp(when, tz=timezone.utc) if when else None)


async def handle_webhook(payload: bytes, signature: str | None) -> str:
    """
    Verify Stripe's signature, then apply the event. Returns the event type.

    Handled events:
    - customer.subscription.created / updated / deleted -> save the subscription
      (this is what switches a paid plan on or off) — or, for a consultant's
      ongoing help (metadata kind "consulting"), its engagement.
    - checkout.session.completed for a consultant purchase -> the setup is paid,
      or the retainer gets its subscription id (consulting_billing).
    - charge.refunded (a refund made in the Stripe dashboard) -> a consultant
      setup becomes "refunded".
    Other event types are accepted and ignored.

    Args:
        payload: bytes, the raw request body exactly as Stripe sent it.
        signature: str | None, the `Stripe-Signature` header.

    Returns:
        str: the Stripe event type, e.g. "customer.subscription.updated".

    Raises:
        HTTPException: 503 if STRIPE_WEBHOOK_SECRET is not set; 400 if the
            signature is missing or invalid.

    Example:
        >>> await handle_webhook(request_body, request.headers.get("stripe-signature"))
        'checkout.session.completed'
    """
    secret = get_settings().stripe_webhook_secret
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="STRIPE_WEBHOOK_SECRET is not set.")
    stripe = _stripe()
    try:
        stripe.Webhook.construct_event(payload, signature or "", secret)   # raises unless Stripe signed it
    except (ValueError, stripe.SignatureVerificationError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid Stripe signature.")
    # Signature checked: read the same bytes as plain JSON (newer stripe
    # libraries return objects that aren't dictionaries).
    event = json.loads(payload)
    obj = event["data"]["object"]
    kind = event["type"]
    from api.services import consulting_billing   # imported here: it imports this file

    if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        if consulting_billing.is_consulting(obj):
            await consulting_billing.save_subscription(obj)
        else:
            await _save_subscription(obj)
    elif kind == "checkout.session.completed" and consulting_billing.is_consulting(obj):
        await consulting_billing.checkout_completed(obj)
    elif kind == "charge.refunded":
        await consulting_billing.charge_refunded(obj)
    return kind


async def set_tester(account: dict, is_tester: bool) -> dict:
    """
    Make an account a tester (paid tools free, even with Stripe live), or a paying client again.

    Rules:
      * Only a plan holder (individual or team owner): colleagues share the
        owner's plan, so the owner is the one to switch.
      * Not an admin (admins already have everything free).
      * Not while they pay through Stripe: cancel their paid package first, so
        nobody keeps being charged for a plan we're also giving away.
      * Switching a tester OFF ends any package they turned on for free (test
        mode subscriptions with no Stripe id), so tools lock again until they pay.

    Args:
        account: dict, the accounts row.
        is_tester: bool, True = tester.

    Returns:
        dict: the updated accounts row.

    Raises:
        HTTPException: 400 for a colleague or an admin; 409 while they have a
            paid Stripe subscription.

    Example:
        >>> (await set_tester(ana, True))["is_tester"]
        True
    """
    from api.services.entitlements import is_admin   # (entitlements imports billing)

    if account.get("team_role") in ("developer", "member"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Colleagues share their business owner's plan — make the owner a tester instead.")
    if is_admin(account):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=ADMIN_FREE)
    live = await account_store.list_subscriptions(str(account["id"]))
    if is_tester and any(s.get("stripe_subscription_id") for s in live):
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="They're paying through Stripe right now. Cancel that package first, then make them a tester.")
    if not is_tester:
        for s in live:
            if not s.get("stripe_subscription_id"):   # a free (tester / test mode) package
                await account_store.set_subscription_status(str(s["id"]), "canceled")
    return await account_store.set_tester(str(account["id"]), is_tester) or account
