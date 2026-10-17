"""
The STORED SYNC (Data Engineering): copy a connected QuickBooks / Xero / Wave into
the client's managed storage, then keep it up to date on their schedule.

WHY THIS EXISTS
---------------
The free quick look reads the last 12 months live and stores nothing. Storing
gives a business its full history, trends over the years, and its books
combined with its other sources (spreadsheets, Square later) — one shape,
one customer list (fact.entities). It needs Data Engineering and the storage
consent, like any storing (docs/ARCHITECTURE.md, "Managed storage").

ONE RUN, STEP BY STEP (run())
-----------------------------
For each record type (QuickBooks Invoice, Bill, Payment...; Xero Invoices,
BankTransactions, Payments):
  1. ask only for what CHANGED since the last run: QuickBooks
     `WHERE MetaData.LastUpdatedTime >= <cursor>`, Xero `If-Modified-Since`,
     oldest first, a page at a time;
  2. flatten each page into unified rows (api/services/unified.py), with the
     platform's shared names;
  3. remove records the tool says are gone (a voided / deleted Xero invoice);
  4. count records that were ALSO in the client's uploaded files
     (api/services/dedupe.py) — reported, so the client can delete the file;
  5. store the page: customers / vendors / products linked to fact.entities,
     unchanged rows skipped (row_hash), changed rows updated;
  6. save the cursor (the newest change stored) — so if the run stops, the
     next one carries on from there, and re-reading a boundary record is
     harmless (it is unchanged, so nothing is written).
Every API call goes through the CallMeter (business_tools.py): a sync can't
pass the tool's free-plan limits. When today's allowance runs out, the run
stops cleanly ("complete": false) and the next one — at the next UTC day —
continues.

WHEN IT RUNS
------------
  * "Turn on the stored sync" / "Sync now" (POST /api/connections/{c}/sync);
  * the scheduler: `python scripts/run_syncs.py` (a Render Cron Job, hourly)
    runs every connection whose next_sync_at has come (run_due()).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pandas as pd
from fastapi import HTTPException, status

from api.services import account_store, business_tools, connections, dedupe, entitlements, oauth, unified, warehouse_store
from api.services.warehouse_loader import LoadPlan

log = logging.getLogger(__name__)

SOURCE_NAMES = {"quickbooks": "QuickBooks Online", "xero": "Xero", "wave": "Wave"}   # = fact.account_data.source_name
QB_RECORD_TYPES = list(unified.QUICKBOOKS_ENTITIES)                    # Invoice, Bill, Payment, ...
XERO_ENDPOINTS = list(unified.XERO_ENDPOINTS)                          # Invoices, BankTransactions, Payments
XERO_SYNC_PAGE = 100            # Xero sends line items with up to 100 records per page
START = "1970-01-01T00:00:00+00:00"   # "everything" (the first run)


def _utc_iso(value) -> str | None:
    """Any timestamp text ("2026-10-16T09:12:33-07:00") as UTC ISO, or None."""
    stamp = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(stamp) else stamp.isoformat()


async def check_ready(holder: dict) -> None:
    """
    May this client's data be stored? Data Engineering, and the storage consent.

    Raises:
        HTTPException: 403 without Data Engineering; 400 without consent.
    """
    if connections.REQUIRED_SERVICE not in await entitlements.active_services(holder):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Storing your records on a schedule needs Data Engineering in your package.")
    if await account_store.active_storage_consent(str(holder["id"])) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Please tick the box to agree to storing your data first.")


# ── Reading what changed, a page at a time ───────────────────────────────────

async def _qb_pages(token: str, realm: str, entity: str, since: str, meter):
    """QuickBooks records of one type changed since `since`, oldest first: yields (page, newest change)."""
    start = 1
    while True:
        sql = (f"SELECT * FROM {entity} WHERE MetaData.LastUpdatedTime >= '{since}' "
               f"ORDERBY MetaData.LastUpdatedTime STARTPOSITION {start} MAXRESULTS {business_tools.QB_PAGE}")
        page = (await business_tools.qb_query(token, realm, sql, meter)).get(entity) or []
        if not page:
            return
        stamps = [_utc_iso((o.get("MetaData") or {}).get("LastUpdatedTime")) for o in page]
        yield page, max([s for s in stamps if s] or [since])
        if len(page) < business_tools.QB_PAGE:
            return
        start += business_tools.QB_PAGE


async def _wave_pages(token: str, business_id: str, since: str, meter):
    """
    Wave invoices changed since `since`: yields (page, newest change).

    Wave's invoice list doesn't promise an order, so the cursor only moves
    once the WHOLE list has been read (a run cut short re-reads from the old
    cursor next time — unchanged invoices are skipped, nothing is duplicated).
    If Wave refuses the "changed since" filter, every invoice is read instead.
    """
    filters = {} if since == START else {"modifiedAtAfter": since}
    try:
        items, _ = await business_tools.wave_pages(token, business_id, "invoices", filters,
                                                   max_rows=10 ** 6, meter=meter)
    except HTTPException as exc:
        if exc.status_code != status.HTTP_502_BAD_GATEWAY or not filters:
            raise
        items, _ = await business_tools.wave_pages(token, business_id, "invoices", {}, max_rows=10 ** 6, meter=meter)
    if items:
        stamps = [_utc_iso(i.get("modifiedAt")) for i in items]
        yield items, max([s for s in stamps if s] or [since])


async def _xero_pages(token: str, tenant: str, endpoint: str, since: str, meter):
    """Xero items of one endpoint changed since `since`, oldest first: yields (page, newest change)."""
    headers = {"xero-tenant-id": tenant}
    if since != START:
        # Xero's If-Modified-Since: a UTC time without the offset.
        headers["If-Modified-Since"] = pd.Timestamp(since).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S")
    page = 1
    while True:
        body = await business_tools._get_json(
            f"{business_tools.XERO_API}/{endpoint}", token,
            {"page": page, "pageSize": XERO_SYNC_PAGE, "order": "UpdatedDateUTC ASC"}, headers, meter)
        items = body.get(endpoint) or []
        if not items:
            return
        stamps = [_utc_iso(unified._xero_plain(i.get("UpdatedDateUTC"), "UpdatedDateUTC")) for i in items]
        yield items, max([s for s in stamps if s] or [since])
        pages = (body.get("pagination") or {}).get("pageCount")
        if (pages is not None and page >= pages) or (pages is None and len(items) < XERO_SYNC_PAGE):
            return
        page += 1


# ── One run ──────────────────────────────────────────────────────────────────

def _plan(connector: str, rows: pd.DataFrame) -> LoadPlan:
    """The rows of one page as a load (warehouse_store.load writes LoadPlans)."""
    return LoadPlan(source_name=SOURCE_NAMES[connector], event_type="sync", columns=[], column_plan=[], choices={},
                    steps=[], tables={}, counts={}, round_trip={}, fingerprint="", source_system=connector, rows=rows)


def _next_run(frequency: str, complete: bool, now: datetime) -> datetime | None:
    """Next scheduled run: by the chosen frequency; when cut short, just after the allowance resets (UTC)."""
    if not complete:
        return datetime(now.year, now.month, now.day, tzinfo=timezone.utc) + timedelta(days=1, minutes=5)
    return connections.next_sync_at(frequency, now, now)


async def run(holder: dict, connector: str, *, consultant_id: str | None = None,
              started_by: str | None = None) -> dict:
    """
    Store what changed in a connected QuickBooks / Xero since the last run.

    Args:
        holder: dict, the plan holder's accounts row (whose data it is).
        connector: str, "quickbooks" or "xero".
        consultant_id: str | None, the consultant running it for the client.
        started_by: str | None, the account that started it (None = the scheduler).

    Returns:
        dict: {"connector", "records", "rows_loaded", "rows_updated", "rows_skipped",
        "complete" (False = stopped at the tool's daily allowance), "duplicates_elsewhere",
        "entities_added", "matches_to_review", "finished_at"} (web/lib/types.ts SyncResult).

    Raises:
        HTTPException: 403 / 400 from check_ready; 404 when not connected; 409 when the
            connection was removed at the tool (connect again).

    Example:
        >>> (await run(holder, "xero"))["complete"]
        True
    """
    if connector not in SOURCE_NAMES:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown integration.")
    await check_ready(holder)
    aid = str(holder["id"])
    row, creds = await oauth.load(aid, connector)
    token, extra = creds["tokens"]["access_token"], creds["extra"]
    meter = await business_tools.meter_for(connector, row, creds)
    state = row.get("sync_state") or {}
    state = dict(json.loads(state) if isinstance(state, str) else state)
    source_name = SOURCE_NAMES[connector]
    result = {"connector": connector, "records": 0, "rows_loaded": 0, "rows_updated": 0, "rows_skipped": 0,
              "complete": True, "duplicates_elsewhere": 0, "entities_added": 0, "matches_to_review": 0}
    run_row = await account_store.save_load_run({"account_id": aid, "source": connector, "source_name": source_name,
                                                 "event_type": "sync", "status": "running", "started_by": started_by})
    try:
        kinds = {"quickbooks": QB_RECORD_TYPES, "xero": XERO_ENDPOINTS, "wave": ["Invoices"]}[connector]
        for kind in kinds:
            since = state.get(kind, START)
            pages = (_qb_pages(token, extra["realm_id"], kind, since, meter) if connector == "quickbooks"
                     else _wave_pages(token, extra["business_id"], since, meter) if connector == "wave"
                     else _xero_pages(token, extra["tenant_id"], kind, since, meter))
            try:
                async for items, newest in pages:
                    if connector == "quickbooks":
                        rows = unified.quickbooks_response_to_unified({"QueryResponse": {kind: items}})
                    elif connector == "wave":
                        rows = unified.wave_invoices_to_unified(items)
                    else:
                        gone = [unified.xero_record_id(kind, i) for i in items if unified.xero_event(kind, i) is None
                                and str(i.get("Status", "")).upper() in unified.XERO_GONE_STATUSES]
                        await warehouse_store.delete_records(holder, connector, source_name, gone)
                        rows = unified.xero_response_to_unified(kind, items)
                    if not rows.empty:
                        dup = await dedupe.find_duplicates(aid, rows, connector, source_name, only_systems={"file_upload"})
                        stored = await warehouse_store.load(holder, _plan(connector, rows), consultant_id)
                        result["records"] += int(rows["record_id"].nunique())
                        result["duplicates_elsewhere"] += dup["records"]
                        result["rows_loaded"] += stored["rows_loaded"]
                        result["rows_updated"] += stored["rows_updated"]
                        result["rows_skipped"] += stored["rows_already_stored"]
                        result["entities_added"] += stored["entities_added"]
                        result["matches_to_review"] += stored["matches_to_review"]
                    state[kind] = newest
                    await account_store.save_sync_state(aid, connector, state)
            except HTTPException as exc:
                if exc.status_code != status.HTTP_429_TOO_MANY_REQUESTS:
                    raise
                result["complete"] = False      # today's allowance: carry on next time
                break
    except Exception as exc:
        await account_store.save_load_run({"id": run_row["id"], "status": "failed", "error": str(exc)[:500],
                                           "finished_at": datetime.now(timezone.utc)})
        raise
    finally:
        await business_tools.save_meter(row, meter)
    now = datetime.now(timezone.utc)
    await account_store.finish_sync(aid, connector, last_synced_at=now if result["complete"] else None,
                                    next_sync_at=_next_run(row.get("refresh_frequency") or "daily", result["complete"], now),
                                    status="connected")
    await account_store.save_load_run({"id": run_row["id"], "status": "succeeded", "rows_in": result["records"],
                                       "rows_loaded": result["rows_loaded"], "rows_updated": result["rows_updated"],
                                       "rows_skipped": result["rows_skipped"], "finished_at": now})
    return {**result, "finished_at": now.isoformat()}


async def run_due(now: datetime | None = None) -> list[dict]:
    """
    The scheduler's job: run every connection whose next_sync_at has come.

    A connection whose client lost Data Engineering or the consent is skipped
    (and stops being scheduled); one that fails is marked "error" and the
    others still run.

    Returns:
        list[dict]: one {"account_id", "connector", "ok", "result" | "error"} per connection tried.
    """
    now = now or datetime.now(timezone.utc)
    report = []
    for conn in await account_store.list_due_connections(now):
        aid, connector = str(conn["account_id"]), conn["connector"]
        if connector not in SOURCE_NAMES:
            continue
        holder = await account_store.get_account(aid)
        try:
            result = await run(holder, connector)
            report.append({"account_id": aid, "connector": connector, "ok": True, "result": result})
        except HTTPException as exc:
            # No Data Engineering / consent any more: stop scheduling it until they turn it on again.
            nxt = None if exc.status_code in (400, 403) else now + timedelta(hours=1)
            await account_store.finish_sync(aid, connector, last_synced_at=None, next_sync_at=nxt,
                                            status="error" if exc.status_code == 409 else "connected")
            report.append({"account_id": aid, "connector": connector, "ok": False, "error": str(exc.detail)})
        except Exception as exc:  # noqa: BLE001 — one client's failure never stops the others
            log.exception("Sync failed for %s / %s", aid, connector)
            await account_store.finish_sync(aid, connector, last_synced_at=None, next_sync_at=now + timedelta(hours=1),
                                            status="error")
            report.append({"account_id": aid, "connector": connector, "ok": False, "error": str(exc)[:200]})
    return report
