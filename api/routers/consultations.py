"""
Consultation endpoints (the free first call, then paid sessions).

    POST /api/consultations/calcom-webhook   Cal.com tells us a booking was made,
                                             rescheduled, canceled, or ended
                                             (signed with CALCOM_WEBHOOK_SECRET)
    GET  /api/consultations                  the client's consultations and which
                                             booking link to show (free or paid)
    POST /api/consultations                  CONSULTANT ONLY, working on a client:
                                             "Confirm it happened" / "No-show" /
                                             "Canceled", or add a call by hand

The logic (and THE RULE for when the free call is used up) lives in
api/services/consultations.py. This file is only the HTTP part.
"""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status

from api.core.security import get_acting_context
from api.models.accounts import ConsultationUpdateIn
from api.services import consultations

router = APIRouter(prefix="/consultations")


@router.post("/calcom-webhook")
async def calcom_webhook(request: Request,
                         x_cal_signature_256: Annotated[str | None, Header()] = None):
    """
    POST /api/consultations/calcom-webhook — Cal.com's booking notifications.

    No sign-in: Cal.com can't sign in. Instead every call is checked against
    the X-Cal-Signature-256 header (HMAC of the raw body with CALCOM_WEBHOOK_SECRET).

    Args:
        request: Request, the raw request (the signature covers the exact bytes).
        x_cal_signature_256: str | None, Cal.com's signature header.

    Returns:
        dict: {"ok": True, "action": ..., "consultation_id": ...}.

    Raises:
        HTTPException: 503 when the webhook secret isn't set; 401 for a bad
            signature; 400 for a body that isn't JSON.

    Example:
        POST /api/consultations/calcom-webhook
        X-Cal-Signature-256: 5f0c...
        {"triggerEvent": "BOOKING_CREATED", "payload": {"uid": "bk_1", ...}}
        -> 200 {"ok": true, "action": "booked", "consultation_id": "..."}
    """
    raw = await request.body()
    consultations.verify_signature(raw, x_cal_signature_256)
    try:
        event = json.loads(raw or b"{}")
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Webhook body must be JSON.")
    if not isinstance(event, dict):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Webhook body must be a JSON object.")
    return await consultations.handle_webhook(event)


@router.get("")
async def my_consultations(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/consultations — the client's consultations and the booking link to show.

    A consultant may send `X-Acting-As: <client id>` to see an assigned client's.

    Args:
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: `consultations.summary` — free_used, booking_url, history, ...

    Example:
        GET /api/consultations
        Authorization: Bearer eyJ...
        -> 200 {"free_used": false, "booking_url": "https://cal.com/...", "history": []}
    """
    return await consultations.summary(ctx["account"])


@router.post("")
async def mark_consultation(body: ConsultationUpdateIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/consultations — a consultant marks a client's consultation.

    Only a consultant working on a client (X-Acting-As) may do this; clients
    can't mark their own free call as used (or unused).

    Args:
        body: ConsultationUpdateIn, the new status and (optionally) which consultation.
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: the updated `consultations.summary`.

    Raises:
        HTTPException: 403 unless a consultant is working on a client; 404 when
            the consultation isn't this client's.

    Example:
        POST /api/consultations
        Authorization: Bearer <consultant token>
        X-Acting-As: <client id>
        {"status": "completed", "consultation_id": "..."}
        -> 200 {"free_used": true, "booking_url": "<paid link>", ...}
    """
    if not (ctx["consultant"] and ctx["acting"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Only a consultant working on a client can mark consultations.")
    return await consultations.consultant_update(
        ctx["account"], ctx["consultant"], status_=body.status, consultation_id=body.consultation_id,
        kind=body.kind, note=body.note,
    )
