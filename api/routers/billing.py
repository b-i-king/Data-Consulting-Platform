"""
Billing endpoints (Stripe). The logic lives in api/services/billing.py.

    GET  /api/billing/plans              the plans on sale (monthly + annual prices)
    POST /api/billing/checkout           start a subscription -> Stripe Checkout URL
    POST /api/billing/consulting-hours   prepay consulting hours -> Stripe Checkout URL
    POST /api/billing/portal             Stripe's billing portal (card, invoices, CANCEL ANYTIME)
    POST /api/billing/cancel             test mode only: switch a plan off
    POST /api/billing/webhook            Stripe -> us (signed); keeps the tables in sync

The signed-in ones accept X-Acting-As, so a consultant can set up billing for
a client they manage (it's billed to the client).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from api.core.security import get_acting_context
from api.models.accounts import CancelPlanIn, CheckoutIn, ConsultingHoursIn
from api.services import billing, entitlements
from api.services.pricing import subscription_catalog

router = APIRouter(prefix="/billing")


async def _plan_holder(ctx: dict) -> dict:
    """Only the account that holds the plan (individual or team owner) can buy or cancel."""
    acc = ctx["account"]
    if await entitlements.plan_holder_id(acc) != str(acc["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can change billing.")
    return acc


@router.get("/plans")
async def plans():
    return {"plans": subscription_catalog(), "stripe": billing.stripe_enabled()}


@router.post("/checkout")
async def checkout(body: CheckoutIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    return await billing.start_checkout(await _plan_holder(ctx), body.plan_id, body.interval)


@router.post("/consulting-hours")
async def consulting_hours(body: ConsultingHoursIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    return await billing.buy_consulting_hours(await _plan_holder(ctx), body.hours, body.service_id)


@router.post("/portal")
async def portal(ctx: Annotated[dict, Depends(get_acting_context)]):
    return await billing.billing_portal(await _plan_holder(ctx))


@router.post("/cancel")
async def cancel(body: CancelPlanIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    await billing.cancel_placeholder(await _plan_holder(ctx), body.plan_id)
    return {"ok": True}


@router.post("/webhook")
async def webhook(request: Request, stripe_signature: Annotated[str | None, Header()] = None):
    # The raw body is needed: the signature is computed over the exact bytes Stripe sent.
    kind = await billing.handle_webhook(await request.body(), stripe_signature)
    return {"received": kind}
