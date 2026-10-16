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
    GET    /api/connections/supabase/projects after "Sign in with Supabase": their projects
    PUT    /api/connections/supabase/project  pick the project to read
    GET    /api/connections/{connector}/authorize    start "Connect" (QuickBooks, Xero, Supabase)
    GET    /api/connections/oauth/callback           where the provider sends the browser back
    GET    /api/connections/{connector}/quick-look   QuickBooks / Xero live summary (never stored)
    GET    /api/connections/{connector}/datasets     record types that can be opened (empty ones flagged)
    GET    /api/connections/{connector}/dataset      one record type as CSV (live, never stored)
    POST   /api/connections/{connector}/sync         the STORED sync now (Data Engineering + consent)
    DELETE /api/connections/{connector}       disconnect (the saved login is deleted; sign-in access revoked)

All accept X-Acting-As, so a consultant can set this up for a client they
manage (the setting is saved on the CLIENT's account).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import RedirectResponse, Response

from api.core.config import get_settings
from api.core.security import get_acting_context
from api.models.accounts import CloudConnectIn, RefreshIn, SupabaseProjectIn, SyncIn
from api.services import account_store, business_tools, cloud_sources, connections, entitlements, oauth, sync
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
        "coming_soon": [{"name", "kind", "when" ("soon" | "later")}] (planned, not connectable yet),
        "slots": {"xero": {"used", "max"}, "quickbooks": {...}} (connected companies, platform-wide),
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
        "coming_soon": connections.COMING_SOON,
        # The tools' free plans: how many companies may connect (Xero 5, QuickBooks 15).
        "slots": {cid: {"used": await account_store.count_connected(cid), "max": _max_connections(cid)}
                  for cid in ("xero", "quickbooks")},
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


async def _supabase_source(ctx: dict) -> dict:
    """
    How the client's database is connected:
      {"mode": "dsn", "dsn": ...}                     a pasted connection string (read directly), or
      {"mode": "oauth", "token": ..., "ref": ...}     "Sign in with Supabase" (read through Supabase's API;
                                                      ref is None until they pick a project).
    404 when nothing is connected.
    """
    holder = await entitlements.plan_holder_id(ctx["account"])
    row = await account_store.get_integration(holder, "supabase")
    if not row or not row.get("credentials_encrypted"):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="No database is connected. Settings -> Integrations -> Supabase database.")
    if oauth.unpack(row["credentials_encrypted"]) is None:
        return {"mode": "dsn", "dsn": cloud_sources.decrypt(row["credentials_encrypted"])}
    _, creds = await oauth.load(holder, "supabase")
    return {"mode": "oauth", "token": creds["tokens"]["access_token"], "ref": creds["extra"].get("project_ref"),
            "creds": creds, "row": row, "holder": holder}


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
        dict: {"connectors": [...], "tables": [{"schema", "name", "kind", "rows_estimate", "has_rows"}],
        "schemas": [{"schema", "tables", "with_rows"}]}.

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
    return {"connectors": connections.connector_list(await account_store.list_connections(holder)), "tables": tables,
            "schemas": cloud_sources.summarize_schemas(tables)}


@router.get("/supabase/tables")
async def supabase_tables(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/connections/supabase/tables — the connected database's tables and views, by schema.

    `has_rows` says whether each one has data (False = empty: the workspace
    hides it, so nothing is spent on it). Works for both ways of connecting
    (a pasted connection string, or "Sign in with Supabase").

    Returns:
        dict: {"tables": [{"schema", "name", "kind", "rows_estimate", "has_rows"}],
        "schemas": [{"schema", "tables", "with_rows"}], "max_rows": 100000,
        "needs_project": bool (signed in with Supabase but no project picked yet)}.

    Raises:
        HTTPException: 404 when no database is connected; 400 when it can't be reached.
    """
    src = await _supabase_source(ctx)
    if src["mode"] == "oauth" and not src["ref"]:
        return {"tables": [], "schemas": [], "max_rows": cloud_sources.MAX_ROWS, "needs_project": True}
    tables = (await cloud_sources.list_tables(src["dsn"]) if src["mode"] == "dsn"
              else await cloud_sources.mgmt_list_tables(src["token"], src["ref"]))
    return {"tables": tables, "schemas": cloud_sources.summarize_schemas(tables), "max_rows": cloud_sources.MAX_ROWS,
            "needs_project": False}


def _csv_response(r: dict, filename: str) -> Response:
    """A live read as a CSV download, with how many rows were sent and whether it was cut short."""
    return Response(r["csv"], media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{filename}"',
                             "X-Data-Origin": "cloud", "X-Rows": str(r["rows"]),
                             "X-Truncated": "true" if r["truncated"] else "false"})


@router.get("/supabase/table")
async def supabase_table(ctx: Annotated[dict, Depends(get_acting_context)], schema: str, name: str,
                         limit: int = cloud_sources.MAX_ROWS):
    """
    GET /api/connections/supabase/table?schema=public&name=orders — one table as a CSV file.

    Read-only and never stored: the workspace opens it like a file (analysis,
    dashboards, ML, AI). Up to `limit` rows (at most 100,000) and no more than
    the upload size limit; an empty table is refused before anything is read.

    Returns:
        Response: text/csv, named "<schema>.<name>.csv", with headers
        X-Rows (rows sent) and X-Truncated ("true" when the table has more).

    Raises:
        HTTPException: 404 for an unknown table or no connection; 409 when no
            project is picked yet; 422 for an empty table; 400 when unreachable.
    """
    src = await _supabase_source(ctx)
    if src["mode"] == "dsn":
        r = await cloud_sources.read_table_csv(src["dsn"], schema, name, limit)
    elif not src["ref"]:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Pick a Supabase project first (Settings -> Integrations).")
    else:
        r = await cloud_sources.mgmt_read_table_csv(src["token"], src["ref"], schema, name, limit)
    return _csv_response(r, f"{schema}.{name}.csv")


@router.get("/supabase/projects")
async def supabase_projects(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/connections/supabase/projects — after "Sign in with Supabase": the client's projects.

    Returns:
        dict: {"projects": [{"ref", "name", "region"}], "selected": "abcd1234" | None}.

    Raises:
        HTTPException: 404 when Supabase isn't connected by sign-in.
    """
    src = await _supabase_source(ctx)
    if src["mode"] != "oauth":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Supabase is connected with a connection string.")
    return {"projects": await cloud_sources.mgmt_projects(src["token"]), "selected": src["ref"]}


@router.put("/supabase/project")
async def choose_supabase_project(body: SupabaseProjectIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    PUT /api/connections/supabase/project  {"ref": "abcd1234"} — which project to read.

    Returns:
        dict: {"connectors": [...]} (the card now shows "Supabase: <project>").

    Raises:
        HTTPException: 404 when the project isn't one of theirs; 403 for a team colleague.
    """
    await _holder_only(ctx)
    src = await _supabase_source(ctx)
    if src["mode"] != "oauth":
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Supabase is connected with a connection string.")
    project = next((p for p in await cloud_sources.mgmt_projects(src["token"]) if p["ref"] == body.ref), None)
    if not project:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That project wasn't found in your Supabase account.")
    creds = src["creds"]
    creds["extra"].update(project_ref=project["ref"], project_name=project["name"])
    await oauth.save(src["holder"], "supabase", f"Supabase: {project['name']}", creds, src["row"].get("consultant_id"))
    return {"connectors": connections.connector_list(await account_store.list_connections(src["holder"]))}


# ── "Connect" sign-in (OAuth): QuickBooks, Xero, Supabase ─────────────────────

def _max_connections(provider: str) -> int:
    """How many companies may connect a tool (Xero Starter: 5; QuickBooks: 15 keeps the free month safe)."""
    s = get_settings()
    return {"xero": s.xero_max_connections, "quickbooks": s.quickbooks_max_connections}.get(provider, 10 ** 6)


async def _slot_free(provider: str, account_id: str) -> bool:
    """
    May this client connect the tool? Yes if they already have it (reconnecting
    reuses their spot) or fewer than the maximum are connected platform-wide.
    """
    mine = await account_store.get_integration(account_id, provider)
    if mine and mine.get("status") == "connected":
        return True
    return await account_store.count_connected(provider) < _max_connections(provider)


def _full(provider: str) -> HTTPException:
    name = oauth.PROVIDERS[provider]["name"]
    return HTTPException(status.HTTP_409_CONFLICT,
                         detail=f"{name} connections are full right now ({_max_connections(provider)} in use). "
                                f"Please upload an export from {name} instead, or ask us to open a spot.")


@router.get("/oauth/callback", include_in_schema=False)
async def oauth_callback(state: str = "", code: str = "", error: str = "", realmId: str = ""):  # noqa: N803 (Intuit's name)
    """
    GET /api/connections/oauth/callback — where every provider sends the browser after sign-in.

    Not called by our website: the PROVIDER redirects here with a one-time
    `code` and our encrypted `state` (QuickBooks also sends `realmId`, the
    company id). We swap the code for tokens, save them encrypted, and send
    the browser back to the workspace:
        /app?integration=xero&connected=1             it worked
        /app?integration=xero&integration_error=...   it didn't (the message says why)
    (/start instead of /app when the sign-in began in Get Started.)

    No sign-in token is needed here: `state` (encrypted, 15 minutes) says
    which account it is for, and can't be forged.
    """
    site = get_settings().site_url.rstrip("/")
    provider, page = "", oauth.RETURN_PAGES["app"]
    try:
        st = oauth.read_state(state)
        provider, page = st["provider"], oauth.RETURN_PAGES[st["return_to"]]
        if error or not code:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Access wasn't allowed, so nothing was connected.")
        tokens = await oauth.exchange_code(provider, code, st["verifier"])
        if provider in ("xero", "quickbooks") and not await _slot_free(provider, st["account_id"]):
            # Full: undo the connection at the tool too (for Xero it would use a slot there).
            if provider == "xero":
                await business_tools.xero_remove_all(tokens["access_token"])
            await oauth.revoke(provider, tokens)
            raise _full(provider)
        display, extra = await oauth.finish_connect(provider, tokens, realmId)
        await oauth.save(st["account_id"], provider, display, {"tokens": tokens, "extra": extra}, st["consultant_id"])
        return RedirectResponse(f"{site}{page}?integration={provider}&connected=1", status_code=303)
    except HTTPException as exc:
        q = urlencode({"integration": provider, "integration_error": str(exc.detail)})
        return RedirectResponse(f"{site}{page}?{q}", status_code=303)


@router.get("/{connector}/authorize")
async def authorize(connector: str, ctx: Annotated[dict, Depends(get_acting_context)], return_to: str = "app"):
    """
    GET /api/connections/{connector}/authorize — start "Connect QuickBooks / Xero / Supabase".

    Returns the provider's sign-in address; the website sends the browser
    there. The connection is saved on the CLIENT (a consultant may connect
    for a client they manage). Free with any plan: the live quick look stores
    nothing. `return_to=start` sends them back to Get Started instead of the
    workspace.

    Returns:
        dict: {"url": "https://appcenter.intuit.com/connect/oauth2?..."}.

    Raises:
        HTTPException: 404 unknown; 503 when the platform's keys for it aren't set;
            403 for a team colleague; 409 when the tool's connection limit is reached.
    """
    if connector not in oauth.PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown integration.")
    holder = await _holder_only(ctx)
    if connector in ("xero", "quickbooks") and not await _slot_free(connector, holder):
        raise _full(connector)
    consultant = str(ctx["consultant"]["id"]) if ctx["acting"] else None
    return {"url": oauth.authorize_url(connector, holder, consultant, return_to)}


async def _live(ctx: dict, connector: str) -> tuple[dict, dict, business_tools.CallMeter]:
    """
    A connected QuickBooks / Xero: (row, credentials with a fresh token, call
    meter). Any team member may read. The meter keeps the company under the
    tool's daily, monthly and per-minute limits; save it with _save_meter.
    """
    if connector not in ("quickbooks", "xero"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown integration.")
    holder = await entitlements.plan_holder_id(ctx["account"])
    row, creds = await oauth.load(holder, connector)
    return row, creds, await business_tools.meter_for(connector, row, creds)


async def _save_meter(row: dict, meter: business_tools.CallMeter) -> None:
    """Save today's count on the connection and add this request's calls to the month (also after a failure)."""
    await business_tools.save_meter(row, meter)


@router.get("/{connector}/quick-look")
async def quick_look(connector: str, ctx: Annotated[dict, Depends(get_acting_context)],
                     start: date | None = None, end: date | None = None):
    """
    GET /api/connections/{connector}/quick-look?start=2026-01-01&end=2026-10-16 — the free live summary.

    Read from QuickBooks / Xero when asked; NOTHING is stored
    (api/services/business_tools.py). Default dates: this year so far.

    Returns:
        dict: {"source", "company", "start", "end", "currency", "estimate", "basis",
        "totals": {"income", "expenses", "net_income", "owed_to_you", "you_owe"},
        "monthly": [...], "top_customers": [...], "stored": false}.

    Raises:
        HTTPException: 404 not connected; 409 connection expired (connect again); 429 slow down.
    """
    row, creds, meter = await _live(ctx, connector)
    try:
        return await business_tools.quick_look(connector, row["display_name"], creds["tokens"]["access_token"],
                                               creds["extra"], start, end, meter)
    finally:
        await _save_meter(row, meter)


@router.get("/{connector}/datasets")
async def live_datasets(connector: str, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/connections/{connector}/datasets — the record types that can be opened in the workspace.

    Returns:
        dict: {"datasets": [{"id", "name", "has_rows"}]} (has_rows False = empty: hidden).
    """
    row, creds, meter = await _live(ctx, connector)
    try:
        return {"datasets": await business_tools.list_datasets(connector, creds["tokens"]["access_token"],
                                                               creds["extra"], meter)}
    finally:
        await _save_meter(row, meter)


@router.get("/{connector}/dataset")
async def live_dataset(connector: str, id: str, ctx: Annotated[dict, Depends(get_acting_context)]):  # noqa: A002
    """
    GET /api/connections/{connector}/dataset?id=Invoice — one record type as CSV, live, never stored.

    Returns:
        Response: text/csv with X-Rows and X-Truncated (see /supabase/table).

    Raises:
        HTTPException: 404 unknown record type; 422 no records.
    """
    row, creds, meter = await _live(ctx, connector)
    try:
        r = await business_tools.read_dataset_csv(connector, creds["tokens"]["access_token"], creds["extra"], id,
                                                  meter=meter)
    finally:
        await _save_meter(row, meter)
    return _csv_response(r, f"{r['name']}.csv")


@router.post("/{connector}/sync")
async def sync_now(connector: str, body: SyncIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/connections/{connector}/sync — store a connected QuickBooks / Xero now
    ("Turn on the stored sync", "Sync now"), then on its refresh schedule.

    Needs Data Engineering and the storage consent (given now with
    consent=true, or before). Only the plan holder (or their consultant).
    "Sync now" is allowed again only after the gap its schedule allows
    (connections.refresh_now_allowed), so nobody gets hourly data for the
    price of daily. Each run stores only what changed (api/services/sync.py)
    and never passes the tool's free-plan limits.

    Returns:
        dict: {"sync": SyncResult, "connectors": [...]}.

    Raises:
        HTTPException: 404 unknown / not connected; 403 without Data Engineering or for a
            colleague; 400 without consent; 429 when "Sync now" is too soon.

    Example:
        POST /api/connections/xero/sync  {"consent": true}
        -> 200 {"sync": {"records": 420, "rows_loaded": 3150, "complete": true, ...}, "connectors": [...]}
    """
    if connector not in sync.SOURCE_NAMES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown integration.")
    holder_id = await _holder_only(ctx)
    holder = await account_store.get_account(holder_id)
    if connections.REQUIRED_SERVICE not in await entitlements.active_services(holder):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Storing your records on a schedule needs Data Engineering in your package.")
    if await account_store.active_storage_consent(holder_id) is None:
        if not body.consent:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Please tick the box to agree to storing your data first.")
        from api.routers.warehouse import TERMS_VERSION

        await account_store.add_storage_consent(holder_id, TERMS_VERSION, str(ctx["account"]["id"]) if not ctx["acting"]
                                                else str(ctx["consultant"]["account_id"]))
    row = await account_store.get_integration(holder_id, connector)
    if row and row.get("last_synced_at"):
        ok, ready_at = connections.refresh_now_allowed(row.get("refresh_frequency") or "daily", row["last_synced_at"])
        if not ok:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail=f"Your schedule allows the next sync after {ready_at:%m/%d/%Y %I:%M %p} UTC.")
    result = await sync.run(holder, connector, consultant_id=str(ctx["consultant"]["id"]) if ctx["acting"] else None,
                            started_by=str(ctx["consultant"]["account_id"]) if ctx["acting"] else str(ctx["account"]["id"]))
    return {"sync": result, "connectors": connections.connector_list(await account_store.list_connections(holder_id))}


@router.delete("/{connector}")
async def disconnect(connector: str, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    DELETE /api/connections/{connector} — disconnect an integration (its saved login is deleted).

    For a sign-in integration we also cancel our access at the provider (best
    effort), and for Xero remove the organization connection — which frees one
    of the app's connection slots (Xero's Starter plan has 5).

    Returns:
        dict: {"connectors": [...]} after the change.

    Raises:
        HTTPException: 404 for an unknown connector; 403 for a team colleague.
    """
    if connector not in connections.CONNECTORS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown connector.")
    holder = await _holder_only(ctx)
    row = await account_store.get_integration(holder, connector)
    creds = oauth.unpack(row["credentials_encrypted"]) if row and row.get("credentials_encrypted") else None
    if creds:
        try:
            if connector == "xero":
                _, fresh = await oauth.load(holder, connector)
                await business_tools.xero_remove_connection(fresh["tokens"]["access_token"],
                                                            fresh["extra"].get("connection_id"))
                creds = fresh
            await oauth.revoke(connector, creds["tokens"])
        except HTTPException:
            pass   # already revoked on their side: just delete ours
    await account_store.delete_integration(holder, connector)
    return {"connectors": connections.connector_list(await account_store.list_connections(holder))}
