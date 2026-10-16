"""
Admin endpoints (admins only — everyone else gets 403).

    GET  /api/admin/consultant-requests                 who asked to be a consultant
                                                        (waiting first), plus every
                                                        consultant and how full they are
    POST /api/admin/consultant-requests/{id}/approve    make them a consultant (their
                                                        dim.consultants row is created at once)
    POST /api/admin/consultant-requests/{id}/deny       say no (they stay a client)

An admin is an ADMIN_EMAILS address (normally just the owner). Admin status is
checked against the database on every call, never trusted from the token.
The website shows these in the workspace's Admin panel (components/workspace/AdminPanel.tsx).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.security import get_current_account
from api.services import account_store, consultants

router = APIRouter(prefix="/admin")


async def _admin(token: dict) -> dict:
    """
    The caller's account if they are an admin, or 403.

    Args:
        token: dict, the decoded login token (its "sub" is the account id).

    Returns:
        dict: the admin's accounts row.

    Raises:
        HTTPException: 403 "This is for admins only."

    Example:
        me = await _admin(token)
    """
    me = await account_store.get_account(token["sub"])
    if me is None or not (me.get("is_admin") or consultants.is_admin_email(me["email"])):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This is for admins only.")
    return me


def _request_out(acc: dict) -> dict:
    """
    One consultant request as the Admin panel shows it.

    Args:
        acc: dict, the accounts row of the person who asked.

    Returns:
        dict: {"account_id", "name", "email", "company_name", "location",
        "status", "requested_at", "decided_at"} (dates as ISO text).

    Example:
        >>> _request_out(kim)["status"]
        'pending'
    """
    def iso(d):
        return d.isoformat() if d else None

    return {
        "account_id": str(acc["id"]),
        "name": f"{acc['first_name']} {acc['last_name']}",
        "email": acc["email"],
        "company_name": acc.get("company_name"),
        "location": acc.get("location_label"),
        "status": acc["consultant_request"],
        "requested_at": iso(acc.get("consultant_requested_at")),
        "decided_at": iso(acc.get("consultant_decided_at")),
    }


async def _panel() -> dict:
    """
    Everything the Admin panel shows: the requests and every consultant's load.

    Returns:
        dict: {"requests": [_request_out, ...], "consultants": [consultants.load, ...]}.
    """
    return {
        "requests": [_request_out(a) for a in await account_store.list_consultant_requests()],
        "consultants": await consultants.directory(),
    }


@router.get("/consultant-requests")
async def consultant_requests(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/admin/consultant-requests — who asked to be a consultant, and every consultant's load.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: {"requests": [...], "consultants": [...]} (see `_panel`).

    Raises:
        HTTPException: 401 without a valid token; 403 for non-admins.

    Example:
        GET /api/admin/consultant-requests
        -> 200 {"requests": [{"account_id": "...", "name": "Kim Park", "status": "pending", ...}],
                "consultants": [{"name": "Brandon", "clients": 1, "capacity": 3, ...}]}
    """
    await _admin(token)
    return await _panel()


@router.post("/consultant-requests/{account_id}/{decision}")
async def decide_request(account_id: str, decision: str, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/consultant-requests/{account_id}/approve (or /deny).

    Approving makes the person a consultant straight away (capacity 3, which
    they can change in their workspace). Denying keeps them a client; they may
    ask again later.

    Args:
        account_id: str, the person who asked.
        decision: str, "approve" or "deny".
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: the refreshed Admin panel (see `_panel`).

    Raises:
        HTTPException: 403 for non-admins; 404 for an unknown decision or
            account; 400 if the person never asked.

    Example:
        POST /api/admin/consultant-requests/7b1e.../approve
        -> 200 {"requests": [{"status": "approved", ...}], "consultants": [...]}
    """
    me = await _admin(token)
    if decision not in ("approve", "deny"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Use /approve or /deny.")
    await consultants.decide(account_id, decision == "approve", me)
    return await _panel()
