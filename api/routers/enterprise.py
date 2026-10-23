"""
Enterprise endpoints (rules in api/services/enterprise.py).

    GET  /api/enterprise/me                      my contract (if any) and the agreement's status
    POST /api/enterprise/me/accept               the account's owner accepts the Enterprise agreement
    GET  /api/enterprise/admin/list              admins: every enterprise client (ending soonest first)
    GET  /api/enterprise/admin/{account_id}      admins: one client's contract, notes included
    PUT  /api/enterprise/admin/{account_id}      admins: set or change the tag, prices, dates, services, limits

Routers stay thin: every rule is in the service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from api.core.security import get_acting_context
from api.services import account_store, enterprise, entitlements

router = APIRouter(prefix="/enterprise")


class AcceptIn(BaseModel):
    signer_name: str = Field(min_length=1, max_length=120)
    agreed: bool = False


def _require_admin(ctx: dict) -> None:
    if not ctx.get("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This is for admins only.")


@router.get("/me")
async def my_contract(ctx: Annotated[dict, Depends(get_acting_context)]):
    """GET /api/enterprise/me — the plan holder's enterprise contract and agreement status (is_enterprise False = none)."""
    return enterprise.summary(await entitlements.plan_holder(ctx["account"]))


@router.post("/me/accept")
async def accept_agreement(body: AcceptIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/enterprise/me/accept {"signer_name": "Ana Lee", "agreed": true}

    Raises:
        HTTPException: 403 not the account's owner (or a consultant acting for them); 400 no contract / not agreed.
    """
    if ctx["acting"]:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the client can accept their agreement.")
    return await enterprise.accept(ctx["account"], body.signer_name, body.agreed)


@router.get("/admin/list")
async def enterprise_clients(ctx: Annotated[dict, Depends(get_acting_context)]):
    """GET /api/enterprise/admin/list — every enterprise client, contracts ending soonest first (admins only)."""
    _require_admin(ctx)
    return {"clients": [{"account_id": str(a["id"]), "name": a.get("company_name") or f"{a['first_name']} {a['last_name']}",
                         "email": a["email"], **enterprise.summary(a)}
                        for a in await account_store.list_enterprise_accounts()]}


@router.get("/admin/{account_id}")
async def client_contract(account_id: str, ctx: Annotated[dict, Depends(get_acting_context)]):
    """GET /api/enterprise/admin/{account_id} — one client's contract, notes included (admins only)."""
    _require_admin(ctx)
    acc = await account_store.get_account(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That account wasn't found.")
    return enterprise.summary(acc, admin=True)


@router.put("/admin/{account_id}")
async def set_contract(account_id: str, body: dict, ctx: Annotated[dict, Depends(get_acting_context)]):
    """PUT /api/enterprise/admin/{account_id} — set or change the contract (any of enterprise.FIELDS; admins only)."""
    _require_admin(ctx)
    return await enterprise.admin_update(account_id, body)
