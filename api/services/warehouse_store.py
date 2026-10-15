"""
Write an ACCEPTED load plan into the warehouse, and count what a client has stored.

WHAT THIS FILE DOES
-------------------
    load(holder, plan)   write a LoadPlan (api/services/warehouse_loader.py) into
                         the warehouse tables of db/warehouse.sql
    summary(holder)      how many customers, products and events a client has stored

Only called after the client (or their consultant) saw the preview, accepted
it, and consented to storage (api/routers/warehouse.py checks all of that).

HOW ONE LOAD IS WRITTEN (database path)
---------------------------------------
Everything happens in ONE transaction, so a failure stores nothing:
  1. `SET LOCAL app.client_id` — the Row Level Security switch in
     db/warehouse.sql: from here on, this connection only sees this client's rows.
  2. dim_client  — the client's row (created on the first load).
  3. dim_source  — one row per file name ("sales.csv"); reloading reuses it.
  4. dim_customer, dim_product — upserted in bulk; we get their keys back.
  5. fact_event  — inserted in bulk; a row whose (source, row ID) is already
     stored is skipped, so loading the same file twice never duplicates.

The warehouse client_id is the PLAN HOLDER's account id (the individual, or a
business's team owner), so it is the same on every load.

Like account_store.py there is an in-memory path for development and tests
(no DATABASE_URL); it vanishes on restart.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

from fastapi import HTTPException, status

from api.core import database
from api.services.warehouse_loader import LoadPlan

# ── In-memory fallback (development only) ────────────────────────────────────
_clients: dict[str, dict] = {}
_sources: dict[tuple[str, str], int] = {}              # (client, name) -> source_id
_customers: dict[tuple[str, int, str], dict] = {}      # (client, source, key) -> row
_products: dict[tuple[str, int, str], dict] = {}
_facts: dict[tuple[str, int, str], dict] = {}          # (client, source, row id) -> row

NOT_SET_UP = ("The warehouse tables aren't in the database yet. In Supabase -> SQL Editor, run "
              "db/warehouse.sql (after db/schema.sql), then try again.")


def _dec(value: float | None) -> Decimal | None:
    """A number as Decimal for Postgres NUMERIC columns (None stays None)."""
    return None if value is None else Decimal(str(value))


def _client_row(holder: dict) -> dict:
    """
    The dim_client row for a plan holder.

    A business is stored by its team (team_id); an individual by their account.

    Args:
        holder: dict, the plan holder's accounts row.

    Returns:
        dict: dim_client columns.
    """
    team_id = holder.get("team_id")
    return {
        "client_id": str(holder["id"]),
        "account_id": None if team_id else str(holder["id"]),
        "team_id": str(team_id) if team_id else None,
        "display_name": holder.get("company_name") or f"{holder['first_name']} {holder['last_name']}",
        "client_type": "business" if team_id else "individual",
        "industry_supersector": holder.get("industry_supersector"),
        "naics_sector": holder.get("naics_sector"),
        "naics_subsector": holder.get("naics_subsector"),
        "company_size": holder.get("company_size"),
    }


async def load(holder: dict, plan: LoadPlan) -> dict:
    """
    Write an accepted plan into the warehouse.

    Args:
        holder: dict, the plan holder's accounts row (the warehouse client).
        plan: LoadPlan, the plan the client accepted (with its prepared rows).

    Returns:
        dict: {"rows_loaded": new events, "rows_already_stored": events skipped
        because they were loaded before, "customers": customers in this load,
        "products": products in this load}.

    Raises:
        HTTPException: 503 when the warehouse tables don't exist yet.

    Example:
        >>> await load(holder, build_plan(df, "sales.csv"))
        {'rows_loaded': 18210, 'rows_already_stored': 0, 'customers': 400, 'products': 9}
    """
    client = _client_row(holder)
    cid = client["client_id"]
    if database.pool is None:
        return _load_in_memory(client, plan)

    import asyncpg   # only needed on the database path

    try:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                # Row Level Security: this transaction may only touch this client's rows.
                await conn.execute("SELECT set_config('app.client_id', $1, true)", cid)
                await conn.execute(
                    """INSERT INTO warehouse.dim_client (client_id, account_id, team_id, display_name, client_type,
                                                         industry_supersector, naics_sector, naics_subsector, company_size)
                       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) ON CONFLICT (client_id) DO NOTHING""",
                    uuid.UUID(cid), uuid.UUID(client["account_id"]) if client["account_id"] else None,
                    uuid.UUID(client["team_id"]) if client["team_id"] else None, client["display_name"],
                    client["client_type"], client["industry_supersector"], client["naics_sector"],
                    client["naics_subsector"], client["company_size"],
                )
                source_id = await conn.fetchval(
                    """SELECT source_id FROM warehouse.dim_source
                       WHERE client_id = $1 AND system = 'file_upload' AND name = $2 ORDER BY source_id LIMIT 1""",
                    uuid.UUID(cid), plan.source_name)
                if source_id is None:
                    source_id = await conn.fetchval(
                        """INSERT INTO warehouse.dim_source (client_id, system, name, last_loaded_at)
                           VALUES ($1, 'file_upload', $2, now()) RETURNING source_id""",
                        uuid.UUID(cid), plan.source_name)
                else:
                    await conn.execute("UPDATE warehouse.dim_source SET last_loaded_at = now() WHERE source_id = $1",
                                       source_id)

                # Customers and products in bulk (unnest = "turn these lists into rows").
                # DO UPDATE (not DO NOTHING) so existing rows also come back with their keys.
                customer_keys: dict[str, int] = {}
                if plan.customers:
                    c = plan.customers
                    rows = await conn.fetch(
                        """INSERT INTO warehouse.dim_customer (client_id, source_id, source_customer_id, name, email,
                                                               phone, match_status)
                           SELECT $1, $2, k, n, e, p, ms
                           FROM unnest($3::text[], $4::text[], $5::text[], $6::text[], $7::text[]) AS t(k, n, e, p, ms)
                           ON CONFLICT (client_id, source_id, source_customer_id) DO UPDATE SET
                               name  = COALESCE(EXCLUDED.name,  warehouse.dim_customer.name),
                               email = COALESCE(EXCLUDED.email, warehouse.dim_customer.email),
                               phone = COALESCE(EXCLUDED.phone, warehouse.dim_customer.phone)
                           RETURNING customer_key, source_customer_id""",
                        uuid.UUID(cid), source_id, [x["source_customer_id"] for x in c], [x["name"] for x in c],
                        [x["email"] for x in c], [x["phone"] for x in c], [x["match_status"] for x in c])
                    customer_keys = {r["source_customer_id"]: r["customer_key"] for r in rows}
                product_keys: dict[str, int] = {}
                if plan.products:
                    p = plan.products
                    rows = await conn.fetch(
                        """INSERT INTO warehouse.dim_product (client_id, source_id, source_product_id, name, category)
                           SELECT $1, $2, k, n, c FROM unnest($3::text[], $4::text[], $5::text[]) AS t(k, n, c)
                           ON CONFLICT (client_id, source_id, source_product_id) DO UPDATE SET
                               category = COALESCE(EXCLUDED.category, warehouse.dim_product.category)
                           RETURNING product_key, source_product_id""",
                        uuid.UUID(cid), source_id, [x["source_product_id"] for x in p], [x["name"] for x in p],
                        [x["category"] for x in p])
                    product_keys = {r["source_product_id"]: r["product_key"] for r in rows}

                # Event type ids ("transaction/income" -> 1, ...), from the shared lookup table.
                type_rows = await conn.fetch("SELECT event_type_id, event_type, event_subtype FROM warehouse.dim_event_type")
                type_ids = {f"{r['event_type']}/{r['event_subtype']}": r["event_type_id"] for r in type_rows}

                f = plan.facts
                inserted = await conn.fetch(
                    """INSERT INTO warehouse.fact_event (client_id, event_type_id, date_key, source_id, source_record_id,
                                                         customer_key, product_key, amount, quantity, score,
                                                         reference_number, status, attributes)
                       SELECT $1, et, dk, $2, rid, ck, pk, amt, qty, sc, ref, st, attr::jsonb
                       FROM unnest($3::smallint[], $4::int[], $5::text[], $6::bigint[], $7::bigint[],
                                   $8::numeric[], $9::numeric[], $10::numeric[], $11::text[], $12::text[], $13::text[])
                            AS t(et, dk, rid, ck, pk, amt, qty, sc, ref, st, attr)
                       ON CONFLICT (client_id, source_id, source_record_id) DO NOTHING
                       RETURNING event_id""",
                    uuid.UUID(cid), source_id,
                    [type_ids[x["event_type"]] for x in f], [x["date_key"] for x in f],
                    [x["source_record_id"] for x in f],
                    [customer_keys.get(x["customer"]) if x["customer"] else None for x in f],
                    [product_keys.get(x["product"]) if x["product"] else None for x in f],
                    [_dec(x["amount"]) for x in f], [_dec(x["quantity"]) for x in f], [_dec(x["score"]) for x in f],
                    [x["reference_number"] for x in f], [x["status"] for x in f],
                    [json.dumps(x["attributes"], default=str) for x in f],
                )
    except (asyncpg.exceptions.UndefinedTableError, asyncpg.exceptions.InvalidSchemaNameError):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=NOT_SET_UP)
    return {"rows_loaded": len(inserted), "rows_already_stored": len(plan.facts) - len(inserted),
            "customers": len(plan.customers), "products": len(plan.products)}


def _load_in_memory(client: dict, plan: LoadPlan) -> dict:
    """
    The same load as `load`, into Python dictionaries (no database configured).

    Args:
        client: dict, the dim_client row.
        plan: LoadPlan, the accepted plan.

    Returns:
        dict: same shape as `load`.
    """
    cid = client["client_id"]
    _clients.setdefault(cid, client)
    source_id = _sources.setdefault((cid, plan.source_name), len(_sources) + 1)
    for c in plan.customers:
        _customers[(cid, source_id, c["source_customer_id"])] = c
    for p in plan.products:
        _products[(cid, source_id, p["source_product_id"])] = p
    new = 0
    for f in plan.facts:
        key = (cid, source_id, f["source_record_id"])
        if key not in _facts:
            _facts[key] = f
            new += 1
    return {"rows_loaded": new, "rows_already_stored": len(plan.facts) - new,
            "customers": len(plan.customers), "products": len(plan.products)}


async def summary(holder: dict) -> dict:
    """
    How much of this client's data is stored in the warehouse.

    Args:
        holder: dict, the plan holder's accounts row.

    Returns:
        dict: {"set_up": bool (warehouse tables exist), "sources", "customers",
        "products", "events", "by_type": [{"event_type", "events", "amount"}],
        "first_date", "last_date"} — dates as "YYYY-MM-DD" or None.

    Example:
        >>> (await summary(holder))["events"]
        18210
    """
    cid = str(holder["id"])
    if database.pool is None:
        facts = [f for (c, _, _), f in _facts.items() if c == cid]
        by_type: dict[str, dict] = {}
        for f in facts:
            t = by_type.setdefault(f["event_type"], {"event_type": f["event_type"], "events": 0, "amount": 0.0})
            t["events"] += 1
            t["amount"] = round(t["amount"] + (f["amount"] or 0), 2)
        keys = sorted(f["date_key"] for f in facts)
        fmt = lambda k: f"{str(k)[:4]}-{str(k)[4:6]}-{str(k)[6:]}"   # noqa: E731 — 20241011 -> 2024-10-11
        return {"set_up": True, "sources": sum(1 for (c, _) in _sources if c == cid),
                "customers": sum(1 for (c, _, _) in _customers if c == cid),
                "products": sum(1 for (c, _, _) in _products if c == cid),
                "events": len(facts), "by_type": sorted(by_type.values(), key=lambda t: t["event_type"]),
                "first_date": fmt(keys[0]) if keys else None, "last_date": fmt(keys[-1]) if keys else None}

    import asyncpg

    try:
        async with database.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT set_config('app.client_id', $1, true)", cid)
                key = uuid.UUID(cid)
                counts = await conn.fetchrow(
                    """SELECT (SELECT count(*) FROM warehouse.dim_source   WHERE client_id = $1) AS sources,
                              (SELECT count(*) FROM warehouse.dim_customer WHERE client_id = $1) AS customers,
                              (SELECT count(*) FROM warehouse.dim_product  WHERE client_id = $1) AS products,
                              (SELECT count(*) FROM warehouse.fact_event   WHERE client_id = $1) AS events,
                              (SELECT min(date_key) FROM warehouse.fact_event WHERE client_id = $1) AS first_key,
                              (SELECT max(date_key) FROM warehouse.fact_event WHERE client_id = $1) AS last_key""", key)
                types = await conn.fetch(
                    """SELECT et.event_type || '/' || et.event_subtype AS event_type, count(*) AS events,
                              coalesce(sum(f.amount), 0) AS amount
                       FROM warehouse.fact_event f JOIN warehouse.dim_event_type et USING (event_type_id)
                       WHERE f.client_id = $1 GROUP BY 1 ORDER BY 1""", key)
    except (asyncpg.exceptions.UndefinedTableError, asyncpg.exceptions.InvalidSchemaNameError):
        return {"set_up": False, "sources": 0, "customers": 0, "products": 0, "events": 0, "by_type": [],
                "first_date": None, "last_date": None}
    fmt = lambda k: f"{str(k)[:4]}-{str(k)[4:6]}-{str(k)[6:]}" if k else None   # noqa: E731
    return {"set_up": True, "sources": counts["sources"], "customers": counts["customers"],
            "products": counts["products"], "events": counts["events"],
            "by_type": [{"event_type": t["event_type"], "events": t["events"], "amount": float(t["amount"])}
                        for t in types],
            "first_date": fmt(counts["first_key"]), "last_date": fmt(counts["last_key"])}
