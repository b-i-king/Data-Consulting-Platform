"""
Payments with Stripe: subscriptions (monthly or annual) and prepaid consulting hours.

WHAT THIS FILE DOES
-------------------
  start_checkout(account, plan, interval)  -> a Stripe Checkout page for a plan
  buy_consulting_hours(account, hours)     -> a Stripe Checkout page for hours
  billing_portal(account)                  -> Stripe's page to change card / CANCEL ANYTIME
  handle_webhook(payload, signature)       -> Stripe tells us what happened; we update
                                              the `subscriptions` / `consulting_purchases`
                                              tables (db/schema.sql)
  live_plan_services(account)              -> services unlocked by live subscriptions

HOW THE PIECES FIT
------------------
1. scripts/sync_stripe.py creates one Stripe PRODUCT per plan in
   api/services/pricing.py, each with two PRICES found by "lookup key":
   dcp_<plan>_month and dcp_<plan>_year (plus dcp_consulting_hour).
2. The site asks for a Checkout page; the client pays on Stripe's own page
   (card details never touch our server).
3. Stripe calls POST /api/billing/webhook. We check Stripe's signature
   (STRIPE_WEBHOOK_SECRET) so nobody can fake "paid", then save the result.
   The webhook is the ONLY thing that turns a paid plan on or off.

PLACEHOLDER MODE (no STRIPE_SECRET_KEY)
---------------------------------------
For testing without Stripe: choosing a plan switches it on at once with no
charge, "cancel" switches it off, and consulting hours are recorded as bought.
The site says "test mode" so nobody thinks they paid.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store, pricing

log = logging.getLogger(__name__)

MAX_CONSULTING_HOURS = 40


def stripe_enabled() -> bool:
    return bool(get_settings().stripe_secret_key)


def _stripe():
    """The Stripe library, set up with our secret key (imported only when used)."""
    import stripe

    stripe.api_key = get_settings().stripe_secret_key
    return stripe


def _price_id(lookup_key: str) -> str:
    """Find a Stripe price by its lookup key (created by scripts/sync_stripe.py)."""
    prices = _stripe().Price.list(lookup_keys=[lookup_key], active=True, limit=1)
    if not prices.data:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="That price isn't set up in Stripe yet — run scripts/sync_stripe.py.")
    return prices.data[0].id


async def _customer_id(account: dict) -> str:
    """The account's Stripe customer (created the first time they pay)."""
    if account.get("stripe_customer_id"):
        return account["stripe_customer_id"]
    name = account.get("company_name") or f"{account['first_name']} {account['last_name']}"
    customer = _stripe().Customer.create(email=account["email"], name=name,
                                         metadata={"account_id": str(account["id"])})
    await account_store.set_stripe_customer(str(account["id"]), customer.id)
    account["stripe_customer_id"] = customer.id
    return customer.id


async def ensure_plans() -> None:
    """Make sure every plan in pricing.py has a subscription_plans row (subscriptions point at it).
    Missing rows are added without Stripe ids; scripts/sync_stripe.py fills those in."""
    await account_store.insert_missing_plans([
        {"id": plan.id, "name": plan.name, "description": plan.description, "service_ids": list(plan.service_ids),
         "monthly_price_usd": plan.monthly_usd, "annual_price_usd": plan.annual_usd, "sort_order": order}
        for order, plan in enumerate(pricing.SUBSCRIPTIONS)
    ])


def _check_plan(plan_id: str, interval: str) -> pricing.Subscription:
    plan = pricing.SUBSCRIPTIONS_BY_ID.get(plan_id)
    if plan is None or interval not in pricing.INTERVALS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown plan or billing interval.")
    return plan


async def start_checkout(account: dict, plan_id: str, interval: str) -> dict:
    """
    Start a subscription. Returns {"url": <Stripe page>} with Stripe, or
    {"url": None, "activated": True} in placeholder mode.
    """
    plan = _check_plan(plan_id, interval)
    live = await account_store.list_subscriptions(str(account["id"]))
    if any(s["plan_id"] == plan.id for s in live):
        raise HTTPException(status.HTTP_409_CONFLICT, detail=f"You already have the {plan.name} plan.")
    if not stripe_enabled():
        await ensure_plans()
        await account_store.upsert_subscription(account_id=str(account["id"]), plan_id=plan.id,
                                                billing_interval=interval, status="active")
        return {"url": None, "activated": True, "test_mode": True}
    site = get_settings().site_url.rstrip("/")
    session = _stripe().checkout.Session.create(
        mode="subscription",
        customer=await _customer_id(account),
        line_items=[{"price": _price_id(pricing.lookup_key(plan.id, interval)), "quantity": 1}],
        client_reference_id=str(account["id"]),
        # Copied onto the subscription, so the webhook knows whose plan it is.
        subscription_data={"metadata": {"account_id": str(account["id"]), "plan_id": plan.id}},
        allow_promotion_codes=True,
        success_url=f"{site}/app?checkout=success",
        cancel_url=f"{site}/app?checkout=canceled",
    )
    return {"url": session.url, "activated": False, "test_mode": False}


async def buy_consulting_hours(account: dict, hours: int, service_id: str | None = None) -> dict:
    """Prepay consulting hours at the hourly rate (one-time Stripe Checkout)."""
    if not 1 <= hours <= MAX_CONSULTING_HOURS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Choose 1 to {MAX_CONSULTING_HOURS} hours.")
    if service_id is not None and service_id not in pricing.SERVICES_BY_ID:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Unknown service.")
    rate = pricing.consulting_rate()
    if not stripe_enabled():
        await account_store.record_consulting_purchase(account_id=str(account["id"]), hours=hours,
                                                       rate_per_hour_usd=rate, amount_usd=round(hours * rate, 2),
                                                       service_id=service_id)
        return {"url": None, "activated": True, "test_mode": True}
    site = get_settings().site_url.rstrip("/")
    session = _stripe().checkout.Session.create(
        mode="payment",
        customer=await _customer_id(account),
        line_items=[{"price": _price_id(pricing.CONSULTING_LOOKUP_KEY), "quantity": hours}],
        client_reference_id=str(account["id"]),
        metadata={"account_id": str(account["id"]), "kind": "consulting_hours", "hours": str(hours),
                  "service_id": service_id or ""},
        success_url=f"{site}/app?consulting=success",
        cancel_url=f"{site}/app?consulting=canceled",
    )
    return {"url": session.url, "activated": False, "test_mode": False}


async def billing_portal(account: dict) -> dict:
    """Stripe's customer portal: update the card, see invoices, cancel anytime."""
    if not stripe_enabled():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Test mode: cancel the plan from your workspace.")
    session = _stripe().billing_portal.Session.create(
        customer=await _customer_id(account), return_url=f"{get_settings().site_url.rstrip('/')}/app")
    return {"url": session.url}


async def cancel_placeholder(account: dict, plan_id: str) -> None:
    """Test mode only: switch a plan off (with Stripe, the portal does this)."""
    if stripe_enabled():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Use “Manage billing” to cancel.")
    match = [s for s in await account_store.list_subscriptions(str(account["id"])) if s["plan_id"] == plan_id]
    if not match:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="There's no active plan to cancel.")
    for s in match:
        await account_store.set_subscription_status(str(s["id"]), "canceled")


async def live_plan_services(account_id: str) -> tuple[list[str], list[dict]]:
    """(service ids unlocked by live subscriptions, the live subscriptions)."""
    subs = await account_store.list_subscriptions(account_id)
    services: list[str] = []
    for s in subs:
        services += pricing.plan_services(s["plan_id"])
    return list(dict.fromkeys(services)), subs


# ── Webhook ───────────────────────────────────────────────────────────────────

def _period_end(sub) -> datetime | None:
    # Newer Stripe API versions keep the period end on each subscription item.
    end = sub.get("current_period_end")
    if end is None:
        items = (sub.get("items") or {}).get("data") or []
        end = items[0].get("current_period_end") if items else None
    return datetime.fromtimestamp(end, tz=timezone.utc) if end else None


async def _save_subscription(sub) -> None:
    meta = sub.get("metadata") or {}
    account_id = meta.get("account_id")
    if not account_id:
        acc = await account_store.get_account_by_stripe_customer(sub.get("customer"))
        account_id = str(acc["id"]) if acc else None
    if not account_id:
        log.warning("Stripe subscription %s has no matching account; ignored.", sub.get("id"))
        return
    item = ((sub.get("items") or {}).get("data") or [{}])[0]
    price = item.get("price") or {}
    key = price.get("lookup_key") or ""           # dcp_<plan>_<interval>
    interval = (price.get("recurring") or {}).get("interval") or key.rsplit("_", 1)[-1]
    plan_id = meta.get("plan_id") or key.removeprefix("dcp_").rsplit("_", 1)[0]
    if plan_id not in pricing.SUBSCRIPTIONS_BY_ID or interval not in pricing.INTERVALS:
        log.warning("Stripe subscription %s has an unknown plan (%s); ignored.", sub.get("id"), key)
        return
    await ensure_plans()
    await account_store.upsert_subscription(
        account_id=account_id, plan_id=plan_id, billing_interval=interval, status=sub.get("status"),
        stripe_subscription_id=sub.get("id"), stripe_customer_id=sub.get("customer"),
        cancel_at_period_end=bool(sub.get("cancel_at_period_end")), current_period_end=_period_end(sub),
    )


async def handle_webhook(payload: bytes, signature: str | None) -> str:
    """Verify Stripe's signature, then apply the event. Returns the event type."""
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
    if kind in ("customer.subscription.created", "customer.subscription.updated", "customer.subscription.deleted"):
        await _save_subscription(obj)
    elif kind == "checkout.session.completed" and (obj.get("metadata") or {}).get("kind") == "consulting_hours":
        if obj.get("payment_status") == "paid":
            meta = obj["metadata"]
            hours = float(meta.get("hours") or 0)
            await account_store.record_consulting_purchase(
                account_id=meta["account_id"], hours=hours, rate_per_hour_usd=pricing.consulting_rate(),
                amount_usd=round((obj.get("amount_total") or 0) / 100, 2), service_id=meta.get("service_id") or None,
                stripe_checkout_session_id=obj.get("id"),
            )
    return kind
