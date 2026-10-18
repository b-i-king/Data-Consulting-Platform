"""
Consultant endpoints (consultants only — everyone else gets 403).

    GET  /api/consultant/clients   the businesses and people assigned to me,
                                   for the workspace's "Working on" picker
    POST /api/consultant/clients   create an account for a client and assign
                                   it to me (for clients who'd rather not sign
                                   up themselves). Same fields as signup.
                                   Refused when I'm at my capacity.
    GET  /api/consultant/settings  my capacity, how full I am, my Cal.com links
                                   and my Cal.com webhook (URL path + secret)
    PUT  /api/consultant/settings  change my capacity (1 to 10) and/or my links
    POST /api/consultant/settings/webhook-secret   make (or replace) my webhook secret

CAL.COM, PER CONSULTANT
-----------------------
A new consultant finishes setting up in Settings:
  1. "Your booking links": their FREE 30-minute Cal.com link and their PAID
     session link (paid on their own Cal.com + Stripe, so they're paid directly).
     Their clients then see these links (api/services/consultations.py links_for).
  2. "Your Cal.com webhook": the app makes a secret; they create a webhook in
     Cal.com -> Settings -> Developer -> Webhooks with the subscriber URL
     <API>/api/consultations/calcom-webhook/<their consultant id> and that
     secret. Bookings then arrive signed, and are recorded as theirs.

To then work on a client, the website sends `X-Acting-As: <client id>` with
the signed-in requests (see api/services/consultants.py for the access rule).
"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.security import get_current_account
from api.models.accounts import ConsultantSettingsIn, SignupIn
from api.routers.accounts import create_client_account
from api.services import account_store, consultants, mfa
from api.services.entitlements import is_admin

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
    consultant = await consultants.active_consultant(token["sub"])   # deactivated = not a consultant
    if consultant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This is for consultants only.")
    mfa.require_for_staff(token, consultant)   # consultant pages need the two-step sign-in
    return consultant


@router.get("/clients")
async def my_clients(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/consultant/clients — the businesses and people assigned to me.

    Feeds the workspace's "Working on" picker.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: {"consultant": {"id", "name"}, "clients": [client summary, ...],
        "load": {"capacity", "clients", "percent", "full", ...}}; each summary has
        "id", "label", "person", "email", "account_type", "company_name",
        "location", "naics_sector", "industry_other", "consultant_plan".

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
    assigned = len(clients)                      # the fill bar counts only MY clients
    me = await account_store.get_account(token["sub"])
    if is_admin(me):
        # An admin can open ANY client (just in case): theirs first, then everyone else.
        mine = {str(c["id"]) for c in clients}
        clients += [c for c in await account_store.list_all_clients() if str(c["id"]) not in mine]
    return {
        "consultant": {"id": str(consultant["id"]), "name": consultant["display_name"]},
        "clients": [consultants.client_summary(c) for c in clients],
        "load": consultants.load({**consultant, "client_count": assigned}),
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
            400 "That email belongs to an admin." or unknown service ids;
            409 when an email already has an account or I'm at my capacity.

    Example:
        POST /api/consultant/clients
        Authorization: Bearer eyJ...
        {"account_type": "business", "first_name": "Ana", "last_name": "Lee",
         "email": "ana@risebakery.com", "company_name": "Rise Bakery"}
        -> 201 {"id": "...", "label": "Rise Bakery", "person": "Ana Lee", ...}
    """
    consultant = await _me(token)
    if consultants.is_admin_email(body.email):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="That email belongs to an admin.")
    capacity = consultant.get("capacity") or account_store.DEFAULT_CAPACITY
    if await account_store.count_clients(str(consultant["id"])) >= capacity:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"You're at your capacity ({capacity} clients). Raise it in Settings first.")
    client = await create_client_account(body, consultant_id=str(consultant["id"]))
    return consultants.client_summary(client)


@router.get("/settings")
async def my_settings(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/consultant/settings — my capacity and how full I am.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: see `_settings_out` — load, max_capacity, booking links, webhook,
        setup_needed.

    Raises:
        HTTPException: 401 without a valid token; 403 for non-consultants.

    Example:
        GET /api/consultant/settings
        -> 200 {"load": {"capacity": 3, "clients": 1, "percent": 33, ...}, "max_capacity": 10,
                "free_booking_url": null, "webhook": {"path": "/api/consultations/calcom-webhook/...",
                "secret": null}, "setup_needed": true}
    """
    consultant = await _me(token)
    return await _settings_out(consultant)


async def _settings_out(consultant: dict) -> dict:
    """
    A consultant's Settings page: capacity, load, Cal.com links and webhook.

    Args:
        consultant: dict, the consultant's consultants row.

    Returns:
        dict: {"load", "max_capacity", "free_booking_url", "paid_booking_url",
        "webhook": {"path", "secret"}, "setup_needed"} — setup_needed is True
        until the free booking link is saved (the onboarding nudge).
    """
    count = await account_store.count_clients(str(consultant["id"]))
    return {
        "load": consultants.load({**consultant, "client_count": count}),
        "max_capacity": consultants.MAX_CAPACITY,
        "free_booking_url": consultant.get("free_booking_url"),
        "paid_booking_url": consultant.get("paid_booking_url"),
        # The website adds its API address in front of the path. The secret is
        # shown only to this consultant (it signs their own webhook).
        "webhook": {"path": f"/api/consultations/calcom-webhook/{consultant['id']}",
                    "secret": consultant.get("calcom_webhook_secret")},
        "setup_needed": not consultant.get("free_booking_url"),
    }


@router.put("/settings")
async def change_settings(body: ConsultantSettingsIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    PUT /api/consultant/settings — change my capacity and/or my Cal.com booking links.

    Lowering capacity below my current clients drops nobody; I just can't be
    picked until I have room again. An empty link clears it (my clients then
    see the default links).

    Args:
        body: ConsultantSettingsIn, only the fields that change.
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: the refreshed settings (see `_settings_out`).

    Raises:
        HTTPException: 401 / 403 as above; 422 for a capacity outside 1..10 or
            a link that isn't https://.

    Example:
        PUT /api/consultant/settings  {"capacity": 5, "free_booking_url": "https://cal.com/kim/30min"}
        -> 200 {"load": {"capacity": 5, ...}, "free_booking_url": "https://cal.com/kim/30min", ...}
    """
    consultant = await _me(token)
    if body.capacity is not None:
        await consultants.set_capacity(consultant, body.capacity)
    links = {k: (v or None) for k, v in (("free_booking_url", body.free_booking_url),
                                         ("paid_booking_url", body.paid_booking_url)) if v is not None}
    if links:
        await account_store.set_consultant_cal(str(consultant["id"]), **links)
    return await _settings_out(await account_store.get_consultant(str(consultant["id"])))


@router.post("/settings/webhook-secret")
async def new_webhook_secret(token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/consultant/settings/webhook-secret — make (or replace) my Cal.com webhook secret.

    The secret is 32 random bytes (secrets.token_urlsafe), so it can't be
    guessed. Paste it into the webhook in Cal.com. Replacing it stops the old
    one at once: update Cal.com with the new one.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: the refreshed settings, with webhook.secret set.

    Raises:
        HTTPException: 401 / 403 as above.

    Example:
        POST /api/consultant/settings/webhook-secret
        -> 200 {"webhook": {"path": "/api/consultations/calcom-webhook/...", "secret": "q8Yw..."}, ...}
    """
    consultant = await _me(token)
    row = await account_store.set_consultant_cal(str(consultant["id"]), calcom_webhook_secret=secrets.token_urlsafe(32))
    return await _settings_out(row)
