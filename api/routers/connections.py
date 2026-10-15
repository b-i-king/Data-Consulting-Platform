"""
Connector endpoints: which data sources a client can connect and how often
each one refreshes. The rules live in api/services/connections.py; the
prices in api/services/pricing.py (REFRESH_OPTIONS).

    GET /api/connections                      the refresh choices (with prices),
                                              the client's connectors and whether
                                              they may set them up (Data Engineering)
    PUT /api/connections/{connector}/refresh  choose how often a connector refreshes

Both accept X-Acting-As, so a consultant can set this up for a client they
manage (the setting is saved on the CLIENT's account).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.security import get_acting_context
from api.models.accounts import RefreshIn
from api.services import account_store, connections, entitlements
from api.services.pricing import refresh_catalog

router = APIRouter(prefix="/connections")


@router.get("")
async def list_connections(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/connections — refresh choices, the client's connectors, and whether they can connect.

    Args:
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: {"options": refresh_catalog(), "connectors": connections.connector_list(...),
        "can_connect": bool (has Data Engineering), "addon_total_usd": float per month}.

    Raises:
        HTTPException: 401/403/404 from `get_acting_context`.

    Example:
        GET /api/connections
        Authorization: Bearer eyJ...
        -> 200 {"options": [{"id": "manual", ...}, ...],
                "connectors": [{"connector": "quickbooks", "refresh_frequency": "daily", ...}],
                "can_connect": false, "addon_total_usd": 0.0}
    """
    account = ctx["account"]
    holder = await entitlements.plan_holder_id(account)
    saved = await account_store.list_connections(holder)
    return {
        "options": refresh_catalog(),
        "connectors": connections.connector_list(saved),
        "can_connect": connections.can_connect(await entitlements.active_services(account)),
        "addon_total_usd": connections.addon_total(saved),
    }


@router.put("/{connector}/refresh")
async def set_refresh(connector: str, body: RefreshIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    PUT /api/connections/{connector}/refresh — choose how often a connector pulls new data.

    Needs Data Engineering (connected data is stored for the client). Only the
    plan holder (or their consultant) can change it, because faster choices are
    paid add-ons. The connection is created as "pending" if it doesn't exist.

    Args:
        connector: str, path part, e.g. "quickbooks".
        body: RefreshIn, {"refresh_frequency": "manual" | "weekly" | "daily" | "hourly" | "live"}.
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: the client's connectors after the change (same shape as
        GET /api/connections "connectors").

    Raises:
        HTTPException: 404 "Unknown connector."; 403 when the client doesn't have
            Data Engineering, or the caller isn't the team owner; 401/403/404 from
            `get_acting_context`.

    Example:
        PUT /api/connections/quickbooks/refresh
        {"refresh_frequency": "weekly"}
        -> 200 {"connectors": [{"connector": "quickbooks", "refresh_frequency": "weekly",
                                "status": "pending", ...}]}
    """
    account = ctx["account"]
    try:
        connections.validate(connector, body.refresh_frequency)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown connector.") from None
    if not connections.can_connect(await entitlements.active_services(account)):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Connected data sources need the Data Engineering tool in your package.")
    holder = await entitlements.plan_holder_id(account)
    if holder != str(account["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can change data refresh.")
    saved = {c["connector"]: c for c in await account_store.list_connections(holder)}
    last = saved.get(connector, {}).get("last_synced_at")
    await account_store.set_connection_frequency(
        account_id=holder, connector=connector, refresh_frequency=body.refresh_frequency,
        # Not connected yet (never synced) -> no schedule; the first pull sets it.
        next_sync_at=connections.next_sync_at(body.refresh_frequency, last) if last else None,
        consultant_id=str(ctx["consultant"]["id"]) if ctx["acting"] else None,
    )
    return {"connectors": connections.connector_list(await account_store.list_connections(holder))}
