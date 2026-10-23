"""
Billing endpoints (Stripe). The logic lives in api/services/billing.py.

    GET  /api/billing/plans              the tools and the bundle (monthly + annual prices)
    GET  /api/billing/sales              are purchases open? (and may I buy anyway: admin / tester)
    POST /api/billing/price              total for a "build your own" selection
    POST /api/billing/checkout           subscribe to (or change) a package -> Stripe Checkout URL
                                         (AI only once the account has stored data)
    POST /api/billing/portal             Stripe's billing portal (card, invoices, CANCEL ANYTIME)
    POST /api/billing/cancel             test mode only: switch the subscription off
    GET  /api/billing/consulting         "Your consultant": prices, their consultant, what was bought
    POST /api/billing/consulting/checkout   pay for SETUP (once) or ONGOING HELP (a retainer)
    POST /api/billing/consulting/cancel  end ongoing help (at the end of the paid period)
    POST /api/billing/webhook            Stripe -> us (signed); keeps the tables in sync

The signed-in ones accept X-Acting-As, so a consultant can set up billing for
a client they manage (it's billed to the client).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from api.core.security import get_acting_context, get_optional_acting_context
from api.models.accounts import CheckoutIn, ConsultingCheckoutIn, PriceIn
from api.services import billing, consulting_billing, entitlements, offers, pricing, sales
from api.services.pricing import subscription_catalog

router = APIRouter(prefix="/billing")


async def _plan_holder(ctx: dict) -> dict:
    """
    Only the account that holds the plan (individual or team owner) can buy or cancel.

    A business's plan belongs to its team owner; colleagues share it but may
    not change it. A consultant acting for a client passes, because the
    acting context already points at the client's account.

    Args:
        ctx: dict, the acting context from `get_acting_context`
            ({"account", "consultant", "acting"}).

    Returns:
        dict: the plan holder's account row, to bill.

    Raises:
        HTTPException: 403 "Only the team owner can change billing."

    Example:
        account = await _plan_holder(ctx)
        await billing.billing_portal(account)
    """
    acc = ctx["account"]
    if await entitlements.plan_holder_id(acc) != str(acc["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can change billing.")
    return acc


@router.get("/plans")
async def plans():
    """
    GET /api/billing/plans — the tools and the bundle, with monthly and annual prices.

    Public (no sign-in). `stripe` False means test mode: choosing a plan turns
    it on with no charge. `sale` is the seasonal sale running today (a percent
    off a first package's first payment), or None; each plan has "trial_days".

    Returns:
        dict: {"plans": [...subscription plans...], "stripe": bool, "sale": dict | None}.

    Example:
        GET /api/billing/plans
        -> 200 {"plans": [{"id": "all_tools", ...}, {"id": "data_cleaning", ...}], "stripe": false}
    """
    return {"plans": subscription_catalog(), "stripe": billing.stripe_enabled(), "sale": await offers.current_sale()}


@router.get("/sales")
async def sales_status(ctx: Annotated[dict | None, Depends(get_optional_acting_context)]):
    """
    GET /api/billing/sales — can customers buy yet? (api/services/sales.py)

    Returns:
        dict: {"open": bool, "can_buy": bool (open, or an admin / tester), "message": str
        (shown instead of the checkout buttons while closed)}.
    """
    return {"open": await sales.is_open(), "can_buy": await sales.can_buy(ctx["account"] if ctx else None),
            "message": sales.SALES_CLOSED}


@router.post("/price")
async def price(body: PriceIn):
    """
    POST /api/billing/price — total for a "build your own" selection (the selector's running total).

    Public (no sign-in). Each tool's fixed price is added together (tools not
    billable yet count $0); the reply also says whether the bundle is cheaper.

    Args:
        body: PriceIn, the picked plan ids and "month" or "year".

    Returns:
        dict: {"interval", "plan_ids", "lines", "total_usd", "bundle_usd",
        "bundle_cheaper"} from `pricing.selection_price`.

    Raises:
        HTTPException: 400 when the selection is invalid (ValueError from pricing).

    Example:
        POST /api/billing/price
        {"plan_ids": ["data_cleaning"], "interval": "month"}
        -> 200 {"interval": "month", "plan_ids": ["data_cleaning"],
                "lines": [{"id": "data_cleaning", "name": "Data Cleaning", "amount_usd": 19.0, "note": ""}],
                "total_usd": 19.0, "bundle_usd": 99.0, "bundle_cheaper": false}
    """
    try:
        return pricing.selection_price(body.plan_ids, body.interval)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))


@router.post("/checkout")
async def checkout(body: CheckoutIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/billing/checkout — subscribe to (or change) a package.

    With no live subscription this returns a Stripe Checkout URL to send the
    browser to; an existing subscription is changed in place (prorated). In
    test mode (no Stripe key) the package is switched on at once. Billed to
    the client when a consultant sends X-Acting-As.

    Args:
        body: CheckoutIn, the picked plan ids and "month" or "year".
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: {"url": str | None, "activated": bool, "updated": bool, "test_mode": bool}.

    Raises:
        HTTPException: 403 "Only the team owner can change billing." when the
            account is a team colleague; plus 401/403/404 from `get_acting_context`;
            409 "That's already your package."; 400 for an invalid selection.

    Example:
        POST /api/billing/checkout
        Authorization: Bearer eyJ...
        {"plan_ids": ["data_cleaning"], "interval": "month"}
        -> 200 {"url": "https://checkout.stripe.com/...", "activated": false,
                "updated": false, "test_mode": false}
    """
    return await billing.start_checkout(await _plan_holder(ctx), body.plan_ids, body.interval)


@router.post("/portal")
async def portal(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/billing/portal — open Stripe's billing portal (card, invoices, cancel anytime).

    Args:
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: {"url": "<Stripe portal address>"}; the portal links back to /workspace/settings/billing.

    Raises:
        HTTPException: 403 "Only the team owner can change billing." when the
            account is a team colleague; plus 401/403/404 from `get_acting_context`;
            400 in test mode (cancel from the workspace instead).

    Example:
        POST /api/billing/portal
        Authorization: Bearer eyJ...
        -> 200 {"url": "https://billing.stripe.com/p/session/..."}
    """
    return await billing.billing_portal(await _plan_holder(ctx))


@router.post("/cancel")
async def cancel(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/billing/cancel — test mode only: switch the subscription off.

    With Stripe on, canceling happens in the billing portal instead.

    Args:
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: {"ok": True}.

    Raises:
        HTTPException: 403 "Only the team owner can change billing." when the
            account is a team colleague; plus 401/403/404 from `get_acting_context`;
            400 when Stripe is on, or when there's no active subscription.

    Example:
        POST /api/billing/cancel
        Authorization: Bearer eyJ...
        -> 200 {"ok": true}
    """
    await billing.cancel_placeholder(await _plan_holder(ctx))
    return {"ok": True}


async def _client_only(ctx: dict) -> dict:
    """
    The plan holder, paying for THEIR consultant — never a consultant buying their own time for a client.

    Raises:
        HTTPException: 403 when a consultant is acting for the client; plus _plan_holder's 403.
    """
    if ctx.get("acting"):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Your client chooses and pays for your help themselves (Plan & billing).")
    return await _plan_holder(ctx)


@router.get("/consulting")
async def consulting(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/billing/consulting — what the client's consultant's help costs, and what they've bought.

    Args:
        ctx: dict, the acting context (a consultant may look at their client's).

    Returns:
        dict: consulting_billing.overview — {"catalog", "consultant", "can_buy", "reason",
        "engagements", "retainer", "test_mode"}.

    Example:
        GET /api/billing/consulting
        -> 200 {"catalog": {"setup_usd": 300.0, "retainers": [...]}, "consultant": {"name": "Kim"}, ...}
    """
    return await consulting_billing.overview(ctx["account"])


@router.post("/consulting/checkout")
async def consulting_checkout(body: ConsultingCheckoutIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/billing/consulting/checkout — pay for setup or ongoing help -> Stripe Checkout URL.

    The consultant's share goes to their Stripe account automatically (Stripe
    Connect); in test mode it switches on at once with no charge.

    Args:
        body: ConsultingCheckoutIn, {"kind", "services", "interval", "accept_terms"}.
        ctx: dict, the acting context (the client themselves; 403 for a consultant acting).

    Returns:
        dict: {"url", "activated", "test_mode"}.

    Raises:
        HTTPException: 400 no consultant / bad choice / terms not accepted / admin; 403 for a consultant acting or a
            team colleague; 409 consultant not ready to be paid, or ongoing help already running.

    Example:
        POST /api/billing/consulting/checkout
        {"kind": "retainer", "services": ["data_cleaning", "data_visualization"], "interval": "month",
         "accept_terms": true}
        -> 200 {"url": "https://checkout.stripe.com/...", "hours_per_week": 12.5, "amount_usd": 1083.33, ...}
    """
    return await consulting_billing.start_checkout(await _client_only(ctx), body.kind, body.services,
                                                   body.interval, body.accept_terms)


@router.post("/consulting/cancel")
async def consulting_cancel(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/billing/consulting/cancel — end ongoing help when the paid period ends (test mode: now).

    Returns:
        dict: the retainer, with "cancel_at_period_end" (Stripe) or status "canceled" (test mode).

    Raises:
        HTTPException: 404 when no ongoing help is running; 403 as for checkout.
    """
    return await consulting_billing.cancel_retainer(await _client_only(ctx))


@router.post("/webhook")
async def webhook(request: Request, stripe_signature: Annotated[str | None, Header()] = None):
    """
    POST /api/billing/webhook — Stripe tells us about payments; keeps the tables in sync.

    No login token: trust comes from the `Stripe-Signature` header, which is
    verified against STRIPE_WEBHOOK_SECRET over the exact raw request bytes.

    Args:
        request: Request, the incoming HTTP request (its raw body is read).
        stripe_signature: str | None, the `Stripe-Signature` header, or None if missing.

    Returns:
        dict: {"received": "<Stripe event type>"}, e.g. "checkout.session.completed".

    Raises:
        HTTPException: 400 "Invalid Stripe signature."; 503 when
            STRIPE_WEBHOOK_SECRET is not set.

    Example:
        POST /api/billing/webhook
        Stripe-Signature: t=...,v1=...
        {"type": "customer.subscription.updated", "data": {...}}
        -> 200 {"received": "customer.subscription.updated"}
    """
    # The raw body is needed: the signature is computed over the exact bytes Stripe sent.
    kind = await billing.handle_webhook(await request.body(), stripe_signature)
    return {"received": kind}
