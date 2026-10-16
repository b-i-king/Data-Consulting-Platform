"""
Write an ACCEPTED load plan into fact.account_data, and read a client's stored data back.

WHAT THIS FILE DOES
-------------------
    load(holder, plan)       write a LoadPlan (api/services/warehouse_loader.py) into
                             fact.account_data (db/schema.sql)
    has_data(holder)         is anything stored for this client? (AI needs it)
    summary(holder)          what is stored, per source (file)
    export(holder, source)   a stored source as its ORIGINAL table again (pandas), so
                             the client and their consultant can open the same stored
                             data in any service — dashboards, analysis, AI — or
                             download it as a CSV
    delete(holder)           delete everything stored for the client ("delete my
                             stored data"), entities included
    delete_records(...)      remove records a source says are gone (a voided Xero invoice)
    relink_stored(holder)    the one-time backfill: link data stored before entities

ENTITIES: every load first goes through entities.link() — each customer,
vendor, product, payee and location is stored ONCE in fact.entities, and the
rows keep only a link to it (api/services/entities.py). export() puts the
names and details back, so "Use my stored data" still gives the table back.

Only called after the client (or their consultant) saw the preview, accepted
it, and consented to storage (api/routers/warehouse.py checks all of that).

HOW ONE LOAD IS WRITTEN (database path)
---------------------------------------
ONE statement inside ONE transaction, so a failure stores nothing:
  1. set_config('app.account_id', ...) — the Row Level Security switch in
     db/schema.sql: from here on, this connection only sees this account's rows.
  2. INSERT ... SELECT FROM unnest(...): all rows at once (unnest turns the
     Python lists into rows).
  3. ON CONFLICT (the unique key) DO UPDATE ... WHERE the row_hash changed:
       new key                -> inserted
       stored, same content   -> skipped (nothing written, modified_at stays)
       stored, new content    -> updated
     RETURNING (xmax = 0) tells inserted from updated rows (xmax is 0 for a
     row this statement created).

The stored data belongs to the PLAN HOLDER's account (the individual, or a
business's team owner), so it is the same on every load and for every colleague.

Like account_store.py there is an in-memory path for development and tests
(no DATABASE_URL); it vanishes on restart.
"""

from __future__ import annotations

import dataclasses
import json
import uuid
from decimal import Decimal

import pandas as pd
from fastapi import HTTPException, status

from api.core import database
from api.services import account_store, entities, unified
from api.services.warehouse_loader import EVENT_TYPES, LoadPlan

# ── In-memory fallback (development only) ────────────────────────────────────
# (account_id, source_system, source_name) -> the stored unified rows
_stored: dict[tuple[str, str, str], pd.DataFrame] = {}

NOT_SET_UP = ("The data table isn't in the database yet. Run `python scripts/setup_database.py` "
              "(or paste db/schema.sql into Supabase -> SQL Editor), then try again.")

# The columns of one stored row, in the order the INSERT below lists them.
_ROW_COLUMNS = ["record_id", "line_number", "event_type", "event_subtype", "event_date", "field_name",
                "source_field", "metric_type", "metric_subtype", "metric_unit", "metric_value", "attributes",
                "row_hash", "mapped", "source_modified_at"]


def _label(event_type: str) -> str:
    """The plain-English name of an event type ("transaction/income" -> "Sales / income (money in)")."""
    return EVENT_TYPES.get(event_type, {}).get("label", event_type)


def _none(value):
    """None for any kind of missing value (NaN, NaT, pd.NA), else the value."""
    return None if unified._is_missing(value) else value


def _tables_missing(exc: Exception) -> bool:
    """True when an error means fact.account_data doesn't exist yet (setup not run)."""
    import asyncpg

    return isinstance(exc, (asyncpg.exceptions.UndefinedTableError, asyncpg.exceptions.InvalidSchemaNameError))


async def load(holder: dict, plan: LoadPlan, consultant_id: str | None = None, replace: bool = False,
               skip_records: set[str] | None = None) -> dict:
    """
    Write an accepted plan into fact.account_data.

    Args:
        holder: dict, the plan holder's accounts row (whose data it is).
        plan: LoadPlan, the plan the client accepted (with its prepared rows).
        consultant_id: str | None, the consultant who loaded it, if one did.
        replace: bool, True = "update my most recent upload": every stored row of
            this source is deleted first, in the same transaction, so rows that
            are no longer in the new file disappear too (a normal load only
            adds and updates).
        skip_records: set | None, record_ids NOT to store — the records the
            duplicate check found already stored from another source, when the
            person chose "load only the new records" (api/services/dedupe.py).

    Returns:
        dict: {"rows_loaded": new rows, "rows_updated": stored rows whose content
        changed, "rows_already_stored": rows skipped because they were stored
        unchanged, "records_skipped_as_duplicates", "entities_added",
        "matches_to_review"}.

    Raises:
        HTTPException: 503 when the table doesn't exist yet.

    Example:
        >>> await load(holder, build_plan(df, "sales.csv"))
        {'rows_loaded': 54630, 'rows_updated': 0, 'rows_already_stored': 0}
    """
    aid = str(holder["id"])
    rows = plan.rows
    skipped = 0
    if skip_records:
        keep = ~rows["record_id"].isin(skip_records)
        skipped = int(rows.loc[~keep, "record_id"].nunique())
        rows = rows[keep]
    # Customers, vendors, products... stored once; the rows keep a link (entities.py).
    rows, ent = await entities.link(aid, plan.source_system, rows)
    plan = dataclasses.replace(plan, rows=rows)
    extra = {"records_skipped_as_duplicates": skipped, "entities_added": ent["entities_added"],
             "matches_to_review": ent["matches_to_review"]}
    if database.pool is None:
        if replace:
            _stored.pop((aid, plan.source_system, plan.source_name), None)
        return {**_load_in_memory(aid, plan), **extra}
    if rows.empty:
        return {"rows_loaded": 0, "rows_updated": 0, "rows_already_stored": 0, **extra}

    data = rows[_ROW_COLUMNS].to_dict("records")
    col = lambda name, fix=lambda v: v: [fix(_none(r[name])) for r in data]   # noqa: E731 — one Python list per column
    try:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                # Row Level Security: this transaction may only touch this account's rows.
                await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
                if replace:
                    await conn.execute(
                        """DELETE FROM fact.account_data
                           WHERE account_id = $1 AND source_system = $2 AND source_name = $3""",
                        uuid.UUID(aid), plan.source_system, plan.source_name)
                changed = await conn.fetch(
                    """INSERT INTO fact.account_data AS a
                           (account_id, consultant_id, source_system, source_name, record_id, line_number,
                            event_type, event_subtype, event_date, field_name, source_field, metric_type,
                            metric_subtype, metric_unit, metric_value, attributes, row_hash, mapped,
                            source_modified_at)
                       SELECT $1, $2, $3, $4, rid, ln, et, es, ed, fn, sf, mt, ms, mu, mv, attr::jsonb, rh, mp, smod
                       FROM unnest($5::text[], $6::int[], $7::text[], $8::text[], $9::date[], $10::text[],
                                   $11::text[], $12::text[], $13::text[], $14::text[], $15::numeric[], $16::text[],
                                   $17::text[], $18::bool[], $19::timestamptz[])
                            AS t(rid, ln, et, es, ed, fn, sf, mt, ms, mu, mv, attr, rh, mp, smod)
                       ON CONFLICT (account_id, source_system, source_name, record_id, line_number, field_name)
                       DO UPDATE SET
                           event_type = EXCLUDED.event_type, event_subtype = EXCLUDED.event_subtype,
                           event_date = EXCLUDED.event_date, source_field = EXCLUDED.source_field,
                           metric_type = EXCLUDED.metric_type, metric_subtype = EXCLUDED.metric_subtype,
                           metric_unit = EXCLUDED.metric_unit, metric_value = EXCLUDED.metric_value,
                           attributes = EXCLUDED.attributes, row_hash = EXCLUDED.row_hash, mapped = EXCLUDED.mapped,
                           source_modified_at = EXCLUDED.source_modified_at, consultant_id = EXCLUDED.consultant_id,
                           modified_at = now()                       -- added_at is never touched
                       -- Only rows whose content really changed are rewritten:
                       WHERE a.row_hash IS DISTINCT FROM EXCLUDED.row_hash
                       RETURNING (xmax = 0) AS inserted""",
                    uuid.UUID(aid), uuid.UUID(consultant_id) if consultant_id else None,
                    plan.source_system, plan.source_name,
                    col("record_id"), col("line_number", int), col("event_type"), col("event_subtype"),
                    col("event_date"), col("field_name"), col("source_field"), col("metric_type"),
                    col("metric_subtype"), col("metric_unit"),
                    col("metric_value", lambda v: None if v is None else Decimal(repr(float(v)))),
                    col("attributes", unified.attributes_json), col("row_hash"), col("mapped", bool),
                    col("source_modified_at"),
                )
    except Exception as exc:
        if _tables_missing(exc):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_SET_UP)
        raise
    inserted = sum(1 for r in changed if r["inserted"])
    return {"rows_loaded": inserted, "rows_updated": len(changed) - inserted,
            "rows_already_stored": len(data) - len(changed), **extra}


def _load_in_memory(aid: str, plan: LoadPlan) -> dict:
    """
    The same load as `load`, into a Python dictionary (no database configured).

    Uses unified.plan_upsert, which makes the same insert / update / unchanged
    decision as the SQL upsert.
    """
    key = (aid, plan.source_system, plan.source_name)
    existing = _stored.get(key)
    if plan.rows.empty:
        return {"rows_loaded": 0, "rows_updated": 0, "rows_already_stored": 0}
    decision = unified.plan_upsert(plan.rows, existing)
    new = pd.concat([decision["insert"], decision["update"][plan.rows.columns]], ignore_index=True)
    merged = new if existing is None else pd.concat([existing, new], ignore_index=True)
    # The newest copy of each key wins (an update replaces the stored row).
    _stored[key] = merged.drop_duplicates(subset=unified.KEY_COLUMNS, keep="last").reset_index(drop=True)
    return {"rows_loaded": len(decision["insert"]), "rows_updated": len(decision["update"]),
            "rows_already_stored": len(decision["unchanged"])}


async def has_data(holder: dict) -> bool:
    """
    Tell whether anything is stored for this client (the AI tool needs stored data).

    Args:
        holder: dict, the plan holder's accounts row.

    Returns:
        bool: True when at least one row is stored.

    Example:
        >>> await has_data(holder)
        True
    """
    aid = str(holder["id"])
    if database.pool is None:
        return any(k[0] == aid and len(v) for k, v in _stored.items())
    try:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
                return bool(await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM fact.account_data WHERE account_id = $1)", uuid.UUID(aid)))
    except Exception as exc:
        if _tables_missing(exc):
            return False
        raise


def _source_row(system: str, name: str, event_type: str, rows: int, records: int, first, last) -> dict:
    """One source in the summary (dates as "YYYY-MM-DD" or None)."""
    iso = lambda d: None if d is None or unified._is_missing(d) else pd.Timestamp(d).date().isoformat()   # noqa: E731
    return {"source_system": system, "source_name": name, "event_type": event_type, "label": _label(event_type),
            "rows": int(rows), "records": int(records), "first_date": iso(first), "last_date": iso(last)}


async def summary(holder: dict) -> dict:
    """
    What is stored for this client, per source (file or connection).

    Args:
        holder: dict, the plan holder's accounts row.

    Returns:
        dict: {"set_up": bool (the table exists), "rows": stored rows,
        "records": stored record lines, "sources": [{"source_system",
        "source_name", "event_type", "label", "rows", "records", "first_date",
        "last_date"}], "first_date", "last_date", "latest_upload"} — dates as
        "YYYY-MM-DD" or None. latest_upload = the source name of the most
        recent successful FILE load that is still stored (what "Update my most
        recent upload" replaces), or None. Connector data is never offered:
        it is edited in the connected app.

    Example:
        >>> (await summary(holder))["sources"][0]["source_name"]
        'sales.csv'
    """
    aid = str(holder["id"])
    sources: list[dict] = []
    if database.pool is None:
        for (a, system, name), df in sorted(_stored.items()):
            if a != aid or df.empty:
                continue
            dates = pd.to_datetime(df["event_date"], errors="coerce")
            sources.append(_source_row(system, name, f"{df['event_type'].iloc[0]}/{df['event_subtype'].iloc[0]}",
                                       len(df), len(df[["record_id", "line_number"]].drop_duplicates()),
                                       dates.min(), dates.max()))
    else:
        try:
            async with database.pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
                    found = await conn.fetch(
                        """SELECT source_system, source_name,
                                  min(event_type || '/' || event_subtype) AS event_type,
                                  count(*) AS rows,
                                  count(DISTINCT (record_id, line_number)) AS records,
                                  min(event_date) AS first_date, max(event_date) AS last_date
                           FROM fact.account_data WHERE account_id = $1
                           GROUP BY source_system, source_name ORDER BY source_name""", uuid.UUID(aid))
        except Exception as exc:
            if _tables_missing(exc):
                return {"set_up": False, "rows": 0, "records": 0, "sources": [], "first_date": None, "last_date": None,
                        "latest_upload": None}
            raise
        sources = [_source_row(r["source_system"], r["source_name"], r["event_type"], r["rows"], r["records"],
                               r["first_date"], r["last_date"]) for r in found]
    firsts = [s["first_date"] for s in sources if s["first_date"]]
    lasts = [s["last_date"] for s in sources if s["last_date"]]
    files = {s["source_name"] for s in sources if s["source_system"] == "file_upload"}
    latest = next((r["source_name"] for r in await account_store.list_load_runs(aid)
                   if r["status"] == "succeeded" and r.get("source", "file_upload") == "file_upload"
                   and r["source_name"] in files), None)
    return {"set_up": True, "rows": sum(s["rows"] for s in sources), "records": sum(s["records"] for s in sources),
            "sources": sources, "first_date": min(firsts) if firsts else None,
            "last_date": max(lasts) if lasts else None, "latest_upload": latest}


# ── Reading stored data back, and deleting it ────────────────────────────────

async def _stored_rows(aid: str, source_name: str | None) -> dict[tuple[str, str], pd.DataFrame]:
    """
    The stored unified rows of one source (or all), in the order they were loaded.

    Returns:
        dict: (source_system, source_name) -> rows (UNIFIED_COLUMNS, attributes as dicts).
    """
    if database.pool is None:
        return {(system, name): df for (a, system, name), df in _stored.items()
                if a == aid and (source_name is None or name == source_name)}
    try:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
                records = await conn.fetch(
                    """SELECT source_system, source_name, record_id, line_number, event_type, event_subtype,
                              event_date, source_modified_at, field_name, source_field, metric_type,
                              metric_subtype, metric_unit, metric_value::float8 AS metric_value,
                              attributes::text AS attributes, row_hash, mapped
                       FROM fact.account_data
                       WHERE account_id = $1 AND ($2::text IS NULL OR source_name = $2)
                       ORDER BY source_name, row_id""", uuid.UUID(aid), source_name)
    except Exception as exc:
        if _tables_missing(exc):
            return {}
        raise
    out: dict[tuple[str, str], list[dict]] = {}
    for r in records:
        row = dict(r)
        row["attributes"] = json.loads(row["attributes"] or "{}")
        out.setdefault((row.pop("source_system"), row.pop("source_name")), []).append(row)
    return {k: pd.DataFrame(v, columns=unified.UNIFIED_COLUMNS) for k, v in out.items()}


async def export(holder: dict, source_name: str | None = None) -> pd.DataFrame:
    """
    The client's stored data as a normal table again, ready to analyze or download.

    For a file, this is the SAME table that was loaded: the stored rows are
    pivoted back (unified.unified_to_wide) and given the client's own column
    names and order, from the column plan saved with its last load
    (dim.load_runs.column_plan). Without a saved plan (a QuickBooks pull, say)
    the columns keep their shared names, with record_id and line_number first.
    Several sources at once are stacked, with a "source" column first.

    Args:
        holder: dict, the plan holder's accounts row.
        source_name: str | None, one source (e.g. "sales.csv"); None = all.

    Returns:
        pd.DataFrame: one row per stored record line (empty when nothing is stored).

    Example:
        >>> (await export(holder, "sales.csv")).columns[:3].tolist()
        ['order_id', 'order_date', 'customer_id']
    """
    aid = str(holder["id"])
    tables = []
    links = await entities.lookup(aid)
    for (system, name), rows in (await _stored_rows(aid, source_name)).items():
        # Each source's own names, IDs and the entities' details back in place of the links.
        rows = rows.assign(attributes=rows["attributes"].map(lambda a: entities.expand(a, links)))
        plan = await account_store.latest_column_plan(aid, system, name)
        if plan:
            wide = unified.unified_to_wide(rows, use_source_names=True, plan=plan).drop(
                columns=["record_id", "line_number"])
        else:
            wide = unified.unified_to_wide(rows)
        if source_name is None:
            wide.insert(0, "source", name)
        tables.append(wide)
    if not tables:
        return pd.DataFrame()
    return pd.concat(tables, ignore_index=True) if len(tables) > 1 else tables[0]


async def delete(holder: dict) -> dict:
    """
    Delete EVERYTHING stored in fact.account_data for this client.

    Platform records (the account, consents, load history) are not touched here.

    Args:
        holder: dict, the plan holder's accounts row.

    Returns:
        dict: {"rows": n, "sources": n} — what was deleted.

    Example:
        >>> await delete(holder)
        {'rows': 54630, 'sources': 1}
    """
    before = await summary(holder)
    gone = {"rows": before["rows"], "sources": len(before["sources"])}
    aid = str(holder["id"])
    if database.pool is None:
        for key in [k for k in _stored if k[0] == aid]:
            del _stored[key]
        await entities.delete_all(aid)
        return gone
    if not before["set_up"]:
        return gone
    async with database.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
            await conn.execute("DELETE FROM fact.account_data WHERE account_id = $1", uuid.UUID(aid))
    await entities.delete_all(aid)
    return gone


async def delete_records(holder: dict, source_system: str, source_name: str, record_ids: list[str]) -> int:
    """
    Remove stored records that their source says are gone (a voided or deleted
    Xero invoice), so stored totals match the source.

    Returns:
        int: rows deleted.
    """
    if not record_ids:
        return 0
    aid = str(holder["id"])
    if database.pool is None:
        key = (aid, source_system, source_name)
        df = _stored.get(key)
        if df is None:
            return 0
        keep = ~df["record_id"].isin(record_ids)
        _stored[key] = df[keep].reset_index(drop=True)
        return int((~keep).sum())
    async with database.pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
        result = await conn.execute(
            """DELETE FROM fact.account_data WHERE account_id = $1 AND source_system = $2 AND source_name = $3
               AND record_id = ANY($4::text[])""", uuid.UUID(aid), source_system, source_name, list(record_ids))
    return int(result.split()[-1])


async def relink_stored(holder: dict) -> dict:
    """
    The one-time BACKFILL for data stored before entities existed: link each
    stored source's customers, vendors... and rewrite its rows' attributes and
    fingerprints in place (added_at / modified_at stay; nothing is reloaded).
    Rows already linked are left as they are, so running it twice is safe.

    Returns:
        dict: {"rows_relinked", "entities_added", "matches_to_review"}.
    """
    aid = str(holder["id"])
    totals = {"rows_relinked": 0, "entities_added": 0, "matches_to_review": 0}
    for (system, name), rows in (await _stored_rows(aid, None)).items():
        linked, stats = await entities.link(aid, system, rows)
        changed = linked["row_hash"].ne(rows["row_hash"].values)
        totals["entities_added"] += stats["entities_added"]
        totals["matches_to_review"] += stats["matches_to_review"]
        if not changed.any():
            continue
        totals["rows_relinked"] += int(changed.sum())
        if database.pool is None:
            _stored[(aid, system, name)] = linked.reset_index(drop=True)
            continue
        part = linked[changed.values]
        async with database.pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
            await conn.execute(
                """UPDATE fact.account_data AS a SET attributes = t.attr::jsonb, row_hash = t.rh
                   FROM unnest($4::text[], $5::int[], $6::text[], $7::text[], $8::text[])
                        AS t(rid, ln, fn, attr, rh)
                   WHERE a.account_id = $1 AND a.source_system = $2 AND a.source_name = $3
                     AND a.record_id = t.rid AND a.line_number = t.ln AND a.field_name = t.fn""",
                uuid.UUID(aid), system, name, part["record_id"].tolist(), [int(v) for v in part["line_number"]],
                part["field_name"].tolist(), [unified.attributes_json(a) for a in part["attributes"]],
                part["row_hash"].tolist())
    return totals
