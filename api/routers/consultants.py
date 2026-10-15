"""
Consultant endpoints (consultants only — everyone else gets 403).

    GET  /api/consultant/clients   the businesses and people assigned to me,
                                   for the workspace's "Working on" picker
    POST /api/consultant/clients   create an account for a client and assign
                                   it to me (for clients who'd rather not sign
                                   up themselves). Same fields as signup.

To then work on a client, the website sends `X-Acting-As: <client id>` with
the signed-in requests (see api/services/consultants.py for the access rule).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.security import get_current_account
from api.models.accounts import SignupIn
from api.routers.accounts import create_client_account
from api.services import account_store, consultants

router = APIRouter(prefix="/consultant")


async def _me(token: dict) -> dict:
    """
    The caller's consultant row, or 403.

    Consultant status is checked against the database on every call (not
    trusted from the token's "role").

    Args:
        token: dict, the decoded login token (its "sub" is the account id).

    Returns:
        dict: the caller's row from the consultants table.

    Raises:
        HTTPException: 403 "This is for consultants only."

    Example:
        consultant = await _me(token)
        consultant["display_name"]   # e.g. "Dana"
    """
    consultant = await account_store.get_consultant_by_account(token["sub"])
    if consultant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This is for consultants only.")
    return consultant


@router.get("/clients")
async def my_clients(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/consultant/clients — the businesses and people assigned to me.

    Feeds the workspace's "Working on" picker.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: {"consultant": {"id", "name"}, "clients": [client summary, ...]};
        each summary has "id", "label", "person", "email", "account_type",
        "company_name", "location", "naics_sector", "industry_other".

    Raises:
        HTTPException: 401 without a valid token; 403 for non-consultants.

    Example:
        GET /api/consultant/clients
        Authorization: Bearer eyJ...
        -> 200 {"consultant": {"id": "...", "name": "Dana"},
                "clients": [{"id": "...", "label": "Rise Bakery", "person": "Ana Lee", ...}]}
    """
    consultant = await _me(token)
    clients = await account_store.list_clients(str(consultant["id"]))
    return {
        "consultant": {"id": str(consultant["id"]), "name": consultant["display_name"]},
        "clients": [consultants.client_summary(c) for c in clients],
    }


@router.post("/clients", status_code=status.HTTP_201_CREATED)
async def add_client(body: SignupIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/consultant/clients — create an account for a client and assign it to me.

    For clients who'd rather not sign up themselves. Same fields and checks as
    sign-up (`create_client_account`), but the client is assigned to the
    calling consultant and marked as wanting a consultant. No token is issued
    to the client.

    Args:
        body: SignupIn, the client's sign-up details.
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: the new client's summary (same shape as in GET /clients), HTTP 201.

    Raises:
        HTTPException: 401 without a valid token; 403 for non-consultants;
            400 "That email belongs to a consultant." or unknown service ids;
            409 when an email already has an account.

    Example:
        POST /api/consultant/clients
        Authorization: Bearer eyJ...
        {"account_type": "business", "first_name": "Ana", "last_name": "Lee",
         "email": "ana@risebakery.com", "company_name": "Rise Bakery"}
        -> 201 {"id": "...", "label": "Rise Bakery", "person": "Ana Lee", ...}
    """
    consultant = await _me(token)
    if consultants.is_consultant_email(body.email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="That email belongs to a consultant.")
    client = await create_client_account(body, consultant_id=str(consultant["id"]))
    return consultants.client_summary(client)
