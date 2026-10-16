"""
"Is this the same customer?" — the review of name matches between sources.

    GET  /api/entities/review   name matches waiting for a person (score 80-91)
    POST /api/entities/review   {"link_id": 12, "same": true}  the answer

When a stored source names a customer, vendor or product almost like one
already stored ("Blue Door Cafe" / "Blue Door Café & Bakery"), they are kept
apart until a person decides (api/services/entities.py). Answering "same"
merges them: every source then counts as one customer; no stored row changes.
All accept X-Acting-As, so a consultant can review for a client.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.security import get_acting_context
from api.models.accounts import EntityDecisionIn
from api.services import entities, entitlements

router = APIRouter(prefix="/entities")


@router.get("/review")
async def review(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/entities/review — name matches waiting for a person.

    Returns:
        dict: {"matches": [{"link_id", "entity_type", "source_system", "name", "score",
        "candidate": {"entity_id", "name", "sources"}}]}.
    """
    holder = await entitlements.plan_holder_id(ctx["account"])
    return {"matches": await entities.pending_reviews(holder)}


@router.post("/review")
async def decide(body: EntityDecisionIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/entities/review — "same" merges the two; "different" keeps them apart.

    Returns:
        dict: {"matches": [...]} still waiting.

    Raises:
        HTTPException: 403 for a team colleague; 404 for an unknown match.
    """
    holder = await entitlements.plan_holder_id(ctx["account"])
    if holder != str(ctx["account"]["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can merge customers.")
    if not await entities.decide(holder, body.link_id, body.same):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That match wasn't found.")
    return {"matches": await entities.pending_reviews(holder)}
