"""
Paying for a consultant's help through the platform (Stripe Connect).

WHAT IS SOLD HERE
-----------------
A "done for you" client pays their assigned consultant for:
  * SETUP — a one-time fee (dim.consulting_prices.setup_usd, $300): getting
    their data connected, cleaned, loaded and the first dashboard built;
  * ONGOING HELP — priced by the SERVICES they need help with, not a level they
    pick (pricing.help_hours: Data Cleaning / Data Engineering 10 hours a week,
    each other service 2.5, AI 0, at most 20), billed monthly or yearly (a
    year = 10 months). Only tools in their package can be picked (the
    consultant works with the client's tools). Data Strategy & Discovery isn't
    one: it's consulting, paid by the hour on Cal.com. The consultant decides
    whether to help beyond those hours.
Hourly sessions booked on the consultant's Cal.com are NOT sold here: those are
still paid to the consultant directly (api/services/consultations.py).

THE RULES (no loopholes)
------------------------
  * Before paying, the client ticks CANCEL_TERMS: cancel any time, but it stays
    on and is paid to the END OF THE PERIOD (month or year); no refunds for
    unused time, except when the consultant becomes unavailable. Stored as
    terms_accepted_at.
  * ONE ongoing help at a time. To change services or hours: end it, and choose
    again once it has ended (so changes happen at a period boundary).
  * SWITCHING CONSULTANT while ongoing help is running is refused (can_switch):
    end it first; switch once it has ended. (Need it sooner? An admin can end it
    in the Stripe dashboard — the webhook marks it ended.)
  * A consultant who is DEACTIVATED or DELETES their account: their clients'
    ongoing help ends AT ONCE (end_for_consultant, ended_reason saved); the
    admin is told who, to refund the unused time in the Stripe dashboard —
    Payments -> the payment -> Refund, with "Reverse transfer" ticked so it
    comes back from the consultant's share.
  * A REFUND made in the Stripe dashboard marks a setup "refunded" (webhook
    charge.refunded).

HOW THE MONEY MOVES (Stripe Connect "destination charges")
----------------------------------------------------------
  1. ONBOARDING: each consultant connects a Stripe EXPRESS account once
     (Settings -> Consultant -> Payouts). Stripe collects their identity, bank
     and tax details — never this app.
  2. CHECKOUT: the client pays on the platform's Stripe Checkout page, at the
     price worked out here (Stripe `price_data` on the products made by
     scripts/sync_stripe.py — so a price change in Admin -> Prices needs no
     Stripe sync). Stripe sends (100 - platform_fee_percent)% to the
     consultant's account; the rest stays with the platform.
  3. WEBHOOK: billing.handle_webhook routes "consulting" events here.
An ADMIN consultant (you) needs no Connect account: the whole payment stays
with the platform, so no fee and no transfer.

TEST MODE (no STRIPE_SECRET_KEY) — and TESTERS
----------------------------------------------
A purchase switches on at once with no charge, and "connect payouts" is done
at once, so the whole flow can be tried locally.

Rows live in dim.consultant_engagements (db/schema.sql). They never unlock tools.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store, billing, entitlements, pricing, stripe_catalog

LIVE = ("active", "trialing", "past_due")   # ongoing help that is still running
NO_CONSULTANT = ("Choose a consultant first (\"I'd like a consultant to do this for me\" in Settings -> "
                 "Account), then come back here.")
NOT_READY = ("Your consultant is still setting up how they get paid. Please try again soon — they've been "
             "reminded in their workspace.")
CANCEL_TERMS = ("You can cancel ongoing help any time. It stays on — and is paid — until the end of the "
                "current period (the month, or the year if you pay yearly); there are no refunds for unused "
                "time, except when your consultant becomes unavailable. The setup fee is paid once and isn't "
                "refundable once the work has started. To change the services, end ongoing help and choose "
                "again when it ends. Your consultant may help beyond the hours shown at their discretion.")
TERMS_NEEDED = "Please read and accept the billing terms first."
SETUP_PRODUCT, RETAINER_PRODUCT = "consult_setup", "consult_retainer"   # stripe_catalog.sync_consulting


def _admin_owned(consultant_account: dict | None) -> bool:
    """An admin's consulting is the platform's own: no Connect account, no fee, no transfer."""
    return bool(consultant_account and consultant_account.get("is_admin"))


def _site() -> str:
    """The website's address, for Stripe's "back to the site" links."""
    return get_settings().site_url.rstrip("/")


def _iso(d):
    """A date-time as ISO text (anything else unchanged)."""
    return d.isoformat() if isinstance(d, datetime) else d


def _us_date(d) -> str:
    """A date-time as MM/DD/YYYY, for messages."""
    if isinstance(d, str):
        d = datetime.fromisoformat(d)
    return d.strftime("%m/%d/%Y") if d else "the end of the period"


# ── Consultants: payouts (onboarding) ─────────────────────────────────────────

async def payouts_status(consultant: dict, account: dict) -> dict:
    """
    Can this consultant be paid through the platform?

    With Stripe on and an account connected but not ready yet, Stripe is asked
    again (so finishing Stripe's form shows up on the next page load).

    Args:
        consultant: dict, their consultants row.
        account: dict, their accounts row.

    Returns:
        dict: {"needed": bool (False for admins), "connected": bool, "ready": bool,
        "test_mode": bool}.
    """
    if _admin_owned(account):
        return {"needed": False, "connected": False, "ready": True, "test_mode": not billing.stripe_enabled()}
    ready = bool(consultant.get("payouts_ready_at"))
    acct = consultant.get("stripe_account_id")
    if billing.stripe_enabled() and acct and not ready:
        info = billing._as_dict(billing._stripe().Account.retrieve(acct))
        transfers = (info.get("capabilities") or {}).get("transfers")
        if info.get("details_submitted") and transfers == "active":
            await account_store.set_consultant_payouts(str(consultant["id"]), ready=True)
            ready = True
    return {"needed": True, "connected": bool(acct) or ready, "ready": ready,
            "test_mode": not billing.stripe_enabled()}


async def start_payouts(consultant: dict, account: dict) -> dict:
    """
    Send a consultant to Stripe to connect (or finish) their payout account.

    Test mode: marked ready at once (nothing to connect).

    Returns:
        dict: {"url": Stripe's onboarding page (None in test mode), "ready": bool}.

    Raises:
        HTTPException: 400 for an admin (their payments stay with the platform).
    """
    if _admin_owned(account):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="As an admin, what clients pay you stays with the platform — nothing to connect.")
    if not billing.stripe_enabled():
        await account_store.set_consultant_payouts(str(consultant["id"]), ready=True)
        return {"url": None, "ready": True}
    stripe = billing._stripe()
    acct = consultant.get("stripe_account_id")
    if not acct:
        created = stripe.Account.create(
            type="express", country="US", email=account["email"], business_type="individual",
            capabilities={"transfers": {"requested": True}},
            metadata={"consultant_id": str(consultant["id"])})
        acct = billing._as_dict(created)["id"]
        await account_store.set_consultant_payouts(str(consultant["id"]), stripe_account_id=acct)
    back = f"{_site()}/workspace/settings/consultant"
    link = stripe.AccountLink.create(account=acct, refresh_url=f"{back}?payouts=retry",
                                     return_url=f"{back}?payouts=done", type="account_onboarding")
    return {"url": billing._as_dict(link)["url"], "ready": False}


async def payouts_dashboard(consultant: dict) -> dict:
    """
    A one-time link to the consultant's Stripe Express dashboard (payouts, tax forms).

    Raises:
        HTTPException: 409 when no payout account is connected (or in test mode).
    """
    acct = consultant.get("stripe_account_id")
    if not billing.stripe_enabled() or not acct:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Connect your payout account first.")
    link = billing._stripe().Account.create_login_link(acct)
    return {"url": billing._as_dict(link)["url"]}


# ── Clients: what they can buy, and buying it ─────────────────────────────────

async def _their_consultant(account: dict) -> tuple[dict, dict]:
    """
    The client's assigned consultant and that consultant's account, or 400.

    Raises:
        HTTPException: 400 NO_CONSULTANT when none is assigned (or they were deactivated).
    """
    consultant = (await account_store.get_consultant(str(account["consultant_id"]))
                  if account.get("consultant_id") else None)
    if consultant is None or not consultant.get("active", True):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=NO_CONSULTANT)
    owner = await account_store.get_account(str(consultant["account_id"])) or {}
    return consultant, owner


async def help_services(account: dict) -> list[str]:
    """
    The services this client can get ongoing help with: the tools in their package
    (the consultant uses the client's tools). Data Strategy & Discovery is
    consulting only (hourly, on Cal.com), so it's never one of them.

    Args:
        account: dict, the client (plan holder).

    Returns:
        list[str]: service ids, Data Cleaning / Data Engineering first.
    """
    active = set(await entitlements.active_services(account))
    return [s for s in (*pricing.CORE_HELP_SERVICES, *pricing.LIGHT_HELP_SERVICES) if s in active]


def _engagement_out(e: dict, names: dict[str, str] | None = None) -> dict:
    """An engagement as the website shows it (no Stripe ids)."""
    return {"id": e["id"], "kind": e["kind"], "hours_per_week": e.get("hours_per_week"),
            "services": e.get("services") or [], "billing_interval": e.get("billing_interval"),
            "amount_usd": e["amount_usd"], "status": e["status"], "ended_reason": e.get("ended_reason"),
            "cancel_at_period_end": bool(e.get("cancel_at_period_end")),
            "current_period_end": _iso(e.get("current_period_end")), "created_at": _iso(e.get("created_at")),
            "consultant_name": (names or {}).get(str(e.get("consultant_id")))}


async def live_retainer(account_id: str) -> dict | None:
    """The client's running ongoing help, if any (also when it's set to end at the period's end)."""
    return next((e for e in await account_store.list_engagements(account_id)
                 if e["kind"] == "retainer" and e["status"] in LIVE), None)


async def overview(account: dict) -> dict:
    """
    Plan & billing -> "Your consultant": prices, their consultant, what they've bought.

    Args:
        account: dict, the client (plan holder).

    Returns:
        dict: {"catalog", "services" ([{"id", "name", "hours"}]: what they can pick for
        ongoing help, and the weekly hours each adds — the core block counts once), "consultant",
        "can_buy", "reason", "engagements", "retainer", "ended" (ongoing help their
        consultant's leaving ended early) | None, "cancel_terms", "test_mode"}.
    """
    reason, consultant_out = None, None
    try:
        consultant, owner = await _their_consultant(account)
        consultant_out = {"name": consultant["display_name"]}
        if not _admin_owned(owner) and not (await payouts_status(consultant, owner))["ready"]:
            reason = NOT_READY
    except HTTPException as exc:
        reason = exc.detail
    rows = await account_store.list_engagements(str(account["id"]))
    names = {}
    for e in rows:
        cid = str(e.get("consultant_id"))
        if e.get("consultant_id") and cid not in names:
            c = await account_store.get_consultant(cid)
            names[cid] = c["display_name"] if c else None
    retainer = next((e for e in rows if e["kind"] == "retainer" and e["status"] in LIVE), None)
    ended = next((e for e in rows if e["kind"] == "retainer" and e.get("ended_reason") in
                  ("consultant_deactivated", "consultant_left")), None)
    picks = [{"id": sid, "name": pricing.SERVICES_BY_ID[sid].name,
              "hours": pricing.CONSULTING["core_hours"] if sid in pricing.CORE_HELP_SERVICES
              else pricing.CONSULTING["service_hours"]} for sid in await help_services(account)]
    return {"catalog": pricing.consulting_catalog(), "services": picks,
            "consultant": consultant_out, "can_buy": reason is None, "reason": reason,
            "engagements": [_engagement_out(e, names) for e in rows if e["status"] != "pending"],
            "retainer": _engagement_out(retainer, names) if retainer else None,
            "ended": _engagement_out(ended, names) if ended and not retainer else None,
            "cancel_terms": CANCEL_TERMS, "test_mode": not billing.charges_real_money(account)}


async def start_checkout(account: dict, kind: str, services: list[str] | None, interval: str | None,
                         accept_terms: bool) -> dict:
    """
    Pay for a consultant's SETUP (once) or ONGOING HELP (for the services picked).

    Args:
        account: dict, the client (plan holder) — never a consultant acting for them.
        kind: str, "setup" or "retainer".
        services: list[str] | None, retainer: the services they need help with.
        interval: str | None, retainer: "month" or "year".
        accept_terms: bool, ticked CANCEL_TERMS (required).

    Returns:
        dict: {"url": Stripe Checkout page (None in test mode), "activated": bool,
        "test_mode": bool, "hours_per_week", "amount_usd"}.

    Raises:
        HTTPException: 400 for an admin, a bad choice, no consultant or terms not
            accepted; 409 when the consultant can't be paid yet, or ongoing help
            is already running.

    Example:
        >>> await start_checkout(client, "retainer", ["data_cleaning", "data_visualization"], "month", True)
        {'url': None, 'activated': True, 'test_mode': True, 'hours_per_week': 12.5, 'amount_usd': 1083.33}
    """
    if account.get("is_admin"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=billing.ADMIN_FREE)
    if not accept_terms:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=TERMS_NEEDED)
    hours, picked = None, []
    if kind == "retainer":
        if interval not in pricing.INTERVALS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose monthly or yearly.")
        allowed = await help_services(account)
        picked = [s for s in allowed if s in set(services or [])]
        wrong = [s for s in (services or []) if s not in allowed and s != "ai"]
        if wrong:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="Add those tools to your package first — your consultant works with your tools.")
        hours = pricing.help_hours(picked)
        if not hours:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick at least one service you need help with.")
    elif kind != "setup":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose setup or ongoing help.")
    consultant, owner = await _their_consultant(account)
    platform_owned = _admin_owned(owner)
    if not platform_owned and not (await payouts_status(consultant, owner))["ready"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=NOT_READY)
    if kind == "retainer" and (running := await live_retainer(str(account["id"]))):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=(
            "You already have ongoing help. To change it, end it — it stays on until "
            f"{_us_date(running.get('current_period_end'))} — and choose again when it ends."))
    fee = 0.0 if platform_owned else pricing.CONSULTING["platform_fee_percent"]
    amount = pricing.setup_usd() if kind == "setup" else pricing.retainer_price_usd(hours, interval)
    base = {"account_id": str(account["id"]), "consultant_id": str(consultant["id"]), "kind": kind,
            "amount_usd": amount, "platform_fee_percent": fee, "hours_per_week": hours, "services": picked,
            "billing_interval": interval if kind == "retainer" else None, "terms_accepted": True}
    out = {"hours_per_week": hours, "amount_usd": amount}

    if not billing.charges_real_money(account):   # test mode, or a tester: on at once, no charge
        await account_store.create_engagement(**base, status="paid" if kind == "setup" else "active")
        return {"url": None, "activated": True, "test_mode": True, **out}

    stripe = billing._stripe()
    engagement = await account_store.create_engagement(**base, status="pending")
    meta = {"kind": "consulting", "engagement_id": engagement["id"], "account_id": str(account["id"]),
            "consultant_id": str(consultant["id"])}
    # Destination charge: the consultant's share goes to their Stripe account; the fee stays here.
    to_consultant = {} if platform_owned else {"transfer_data": {"destination": consultant["stripe_account_id"]}}
    back = f"{_site()}/workspace/settings/billing"
    common = {"customer": await billing._customer_id(account), "client_reference_id": str(account["id"]),
              "metadata": meta, "success_url": f"{back}?consulting=success",
              "cancel_url": f"{back}?consulting=canceled"}
    cents = round(amount * 100)
    if kind == "setup":
        session = stripe.checkout.Session.create(
            mode="payment",
            line_items=[{"price_data": {"currency": "usd", "unit_amount": cents,
                                        "product": stripe_catalog.product_id(SETUP_PRODUCT)}, "quantity": 1}],
            payment_intent_data={"metadata": meta, **to_consultant,
                                 **({"application_fee_amount": round(cents * fee / 100)} if to_consultant else {})},
            **common)
    else:
        session = stripe.checkout.Session.create(
            mode="subscription",
            line_items=[{"price_data": {"currency": "usd", "unit_amount": cents, "recurring": {"interval": interval},
                                        "product": stripe_catalog.product_id(RETAINER_PRODUCT)}, "quantity": 1}],
            subscription_data={"metadata": meta, **to_consultant,
                               **({"application_fee_percent": fee} if to_consultant else {}),
                               "description": f"Ongoing help: {hours:g} hours a week"},
            **common)
    session = billing._as_dict(session)
    await account_store.update_engagement(engagement["id"], stripe_checkout_id=session.get("id"))
    return {"url": session.get("url"), "activated": False, "test_mode": False, **out}


async def cancel_retainer(account: dict) -> dict:
    """
    End ongoing help at the end of the period already paid (test mode: at once).

    Returns:
        dict: the engagement as the website shows it.

    Raises:
        HTTPException: 404 when no ongoing help is running.
    """
    running = await live_retainer(str(account["id"]))
    if running is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="You don't have ongoing help to end.")
    if billing.charges_real_money(account) and running.get("stripe_subscription_id"):
        billing._stripe().Subscription.modify(running["stripe_subscription_id"], cancel_at_period_end=True)
        row = await account_store.update_engagement(running["id"], cancel_at_period_end=True, ended_reason="client")
    else:
        row = await account_store.update_engagement(running["id"], status="canceled", ended_reason="client")
    return _engagement_out(row)


async def _end_now(e: dict, reason: str) -> None:
    """Stop one ongoing help immediately (in Stripe too) and say why."""
    if billing.stripe_enabled() and e.get("stripe_subscription_id"):
        billing._stripe().Subscription.cancel(e["stripe_subscription_id"])
    await account_store.update_engagement(e["id"], status="canceled", ended_reason=reason)


async def cancel_all(account: dict) -> None:
    """Stop every running ongoing help at once ("Delete my account")."""
    for e in await account_store.list_engagements(str(account["id"])):
        if e["kind"] == "retainer" and e["status"] in LIVE:
            await _end_now(e, "account_deleted")


async def end_for_consultant(consultant_id: str, reason: str) -> list[dict]:
    """
    A consultant is deactivated, or deletes their account: their clients' ongoing help ends NOW.

    The clients paid for help this consultant can no longer give, so it stops
    at once (not at the period's end). The admin refunds the unused time in the
    Stripe dashboard ("Reverse transfer" ticked, so it comes back from the
    consultant's share); the client sees why it ended in Plan & billing.

    Args:
        consultant_id: str, the consultants row id.
        reason: str, "consultant_deactivated" or "consultant_left".

    Returns:
        list[dict]: one per ended ongoing help: {"client", "email", "amount_usd",
        "billing_interval"} — for the admin's refund to-do.
    """
    ended = []
    for e in await account_store.list_consultant_engagements(consultant_id):
        if e["kind"] == "retainer" and e["status"] in LIVE:
            await _end_now(e, reason)
            client = await account_store.get_account(e["account_id"]) or {}
            ended.append({"client": client.get("company_name") or f"{client.get('first_name', '')} "
                                                                f"{client.get('last_name', '')}".strip(),
                          "email": client.get("email"), "amount_usd": e["amount_usd"],
                          "billing_interval": e.get("billing_interval")})
    return ended


async def can_switch(account: dict, new_consultant_id: str | None) -> None:
    """
    Refuse switching consultant while ongoing help with the current one is running.

    Args:
        account: dict, the client (plan holder).
        new_consultant_id: str | None, the consultant they want (None = whoever has room).

    Raises:
        HTTPException: 409 with the date it ends, when ongoing help is running with someone else.
    """
    running = await live_retainer(str(account["id"]))
    if running and running.get("consultant_id") and running["consultant_id"] != new_consultant_id:
        when = _us_date(running.get("current_period_end")) if running.get("cancel_at_period_end") else None
        raise HTTPException(status.HTTP_409_CONFLICT, detail=(
            f"Your ongoing help is set to end on {when}; you can switch consultants then."
            if when else
            "You have ongoing help with your current consultant. End it first (Plan & billing) — it stays on "
            "until the end of the period you've paid for — and switch consultants when it ends. "
            "Need to switch sooner? Contact us."))


# ── Stripe webhook (routed here by billing.handle_webhook) ────────────────────

def is_consulting(obj: dict) -> bool:
    """Does this Stripe object belong to a consultant purchase (metadata kind "consulting")?"""
    return (obj.get("metadata") or {}).get("kind") == "consulting"


async def _engagement_for(obj: dict) -> dict | None:
    """The engagement a Stripe object is about: by our id in the metadata, else by its Stripe ids."""
    meta = obj.get("metadata") or {}
    if meta.get("engagement_id"):
        found = await account_store.find_engagement(engagement_id=meta["engagement_id"])
        if found:
            return found
    if obj.get("object") == "subscription":
        return await account_store.find_engagement(stripe_subscription_id=obj.get("id"))
    return await account_store.find_engagement(stripe_checkout_id=obj.get("id"))


async def checkout_completed(session: dict) -> None:
    """checkout.session.completed: a setup is PAID, or ongoing help has its subscription id."""
    e = await _engagement_for(session)
    if e is None:
        return
    if session.get("mode") == "payment" and session.get("payment_status") == "paid":
        await account_store.update_engagement(e["id"], status="paid",
                                              stripe_payment_intent_id=session.get("payment_intent"))
    elif session.get("subscription"):
        await account_store.update_engagement(e["id"], stripe_subscription_id=session["subscription"])


async def charge_refunded(charge: dict) -> bool:
    """
    charge.refunded (a refund made in the Stripe dashboard): a fully refunded setup becomes "refunded".

    Returns:
        bool: True when it was a consultant setup payment.
    """
    if not charge.get("payment_intent"):
        return False
    e = await account_store.find_engagement(stripe_payment_intent_id=charge["payment_intent"])
    if e is None:
        return False
    if charge.get("refunded"):   # the whole amount (a partial refund leaves it "paid")
        await account_store.update_engagement(e["id"], status="refunded")
    return True


async def save_subscription(sub: dict) -> None:
    """customer.subscription.*: keep ongoing help's status, period end and cancel flag in step with Stripe."""
    e = await _engagement_for(sub)
    if e is None:
        return
    end = billing._period_end(sub)
    fields = {"status": sub.get("status") or e["status"], "stripe_subscription_id": sub.get("id"),
              "cancel_at_period_end": bool(sub.get("cancel_at_period_end")),
              "current_period_end": end or (datetime.fromtimestamp(sub["ended_at"], tz=timezone.utc)
                                            if sub.get("ended_at") else None)}
    if fields["status"] == "canceled" and not e.get("ended_reason"):
        fields["ended_reason"] = "client"   # ended in Stripe (the portal, or an admin in the dashboard)
    await account_store.update_engagement(e["id"], **fields)
