"""
Connector endpoints: which data sources a client can connect and how often
each one refreshes. The rules live in api/services/connections.py; the
prices in api/services/pricing.py (REFRESH_OPTIONS).

    GET /api/connections                      the refresh choices (with prices),
                                              the client's connectors and whether
                                              they may set them up (Data Engineering)
    PUT /api/connections/{connector}/refresh  choose how often a business tool refreshes
    POST   /api/connections/supabase          connect a client's own Supabase / PostgreSQL
                                              database (checked, login saved ENCRYPTED)
    GET    /api/connections/supabase/tables   its tables and views
    GET    /api/connections/supabase/table    one table as CSV (read-only, never stored)
    DELETE /api/connections/{connector}       disconnect (the saved login is deleted)

All accept X-Acting-As, so a consultant can set this up for a client they
manage (the setting is saved on the CLIENT's account).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import Response

from api.core.security import get_acting_context
from api.models.accounts import CloudConnectIn, RefreshIn
from api.services import account_store, cloud_sources, connections, entitlements
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
        "kinds": {"cloud_storage": "Cloud storage", "business_tool": "Business tools"},
        "can_connect": bool (has Data Engineering — business tools), "addon_total_usd": float per month}.

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
        "kinds": connections.KINDS,
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


async def _holder_only(ctx: dict) -> str:
    """The plan holder's id; 403 for a team colleague (only the owner, or their consultant, connects data)."""
    holder = await entitlements.plan_holder_id(ctx["account"])
    if holder != str(ctx["account"]["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can connect data sources.")
    return holder


async def _saved_dsn(ctx: dict) -> str:
    """The client's saved database login, decrypted (404 when not connected)."""
    holder = await entitlements.plan_holder_id(ctx["account"])
    row = await account_store.get_integration(holder, "supabase")
    if not row or not row.get("credentials_encrypted"):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="No database is connected. Settings -> Integrations -> Supabase database.")
    return cloud_sources.decrypt(row["credentials_encrypted"])


@router.post("/supabase")
async def connect_supabase(body: CloudConnectIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/connections/supabase — connect the client's own Supabase / PostgreSQL database.

    The connection string is checked (a real, public host; it really connects
    and lists tables) BEFORE it is saved, and it is saved ENCRYPTED. We only
    ever read from it, read-only (api/services/cloud_sources.py).

    Args:
        body: CloudConnectIn, {"connection_string": "postgresql://user:pass@host:5432/db"}.
        ctx: dict, the acting context.

    Returns:
        dict: {"connectors": [...], "tables": [{"schema", "name", "kind", "rows_estimate"}]}.

    Raises:
        HTTPException: 400 for a bad string, a private host or a failed connection;
            403 for a team colleague.

    Example:
        POST /api/connections/supabase  {"connection_string": "postgresql://ro:...@db.abcd.supabase.co:5432/postgres"}
        -> 200 {"connectors": [{"connector": "supabase", "status": "connected", ...}], "tables": [...]}
    """
    holder = await _holder_only(ctx)
    info = cloud_sources.parse_dsn(body.connection_string)
    tables = await cloud_sources.list_tables(body.connection_string.strip())
    await account_store.save_integration(
        account_id=holder, connector="supabase", kind="cloud_storage", display_name=info["label"],
        credentials_encrypted=cloud_sources.encrypt(body.connection_string.strip()),
        consultant_id=str(ctx["consultant"]["id"]) if ctx["acting"] else None)
    return {"connectors": connections.connector_list(await account_store.list_connections(holder)), "tables": tables}


@router.get("/supabase/tables")
async def supabase_tables(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/connections/supabase/tables — the connected database's tables and views.

    Returns:
        dict: {"tables": [{"schema", "name", "kind", "rows_estimate"}], "max_rows": 100000}.

    Raises:
        HTTPException: 404 when no database is connected; 400 when it can't be reached.
    """
    return {"tables": await cloud_sources.list_tables(await _saved_dsn(ctx)), "max_rows": cloud_sources.MAX_ROWS}


@router.get("/supabase/table")
async def supabase_table(ctx: Annotated[dict, Depends(get_acting_context)], schema: str, name: str,
                         limit: int = cloud_sources.MAX_ROWS):
    """
    GET /api/connections/supabase/table?schema=public&name=orders — one table as a CSV file.

    Read-only and never stored: the workspace opens it like a file (analysis,
    dashboards, ML, AI). Up to `limit` rows (at most 100,000).

    Returns:
        Response: text/csv, named "<schema>.<name>.csv".

    Raises:
        HTTPException: 404 for an unknown table or no connection; 400 when unreachable.
    """
    df = await cloud_sources.read_table(await _saved_dsn(ctx), schema, name, limit)
    return Response(df.to_csv(index=False), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{schema}.{name}.csv"',
                             "X-Data-Origin": "cloud"})


@router.delete("/{connector}")
async def disconnect(connector: str, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    DELETE /api/connections/{connector} — disconnect an integration (its saved login is deleted).

    Returns:
        dict: {"connectors": [...]} after the change.

    Raises:
        HTTPException: 404 for an unknown connector; 403 for a team colleague.
    """
    if connector not in connections.CONNECTORS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown connector.")
    holder = await _holder_only(ctx)
    await account_store.delete_integration(holder, connector)
    return {"connectors": connections.connector_list(await account_store.list_connections(holder))}
