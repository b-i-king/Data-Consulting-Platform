"""
Duplicate records ACROSS sources: "most of this file is already in your QuickBooks data".

WHY THIS EXISTS
---------------
Within one source, duplicates can't happen: fact.account_data's unique key and
row_hash see to that (docs/DATA_MODEL.md, "Loading data safely"). But the same
invoice can arrive twice from two SOURCES — QuickBooks syncing it, and an
exported spreadsheet of the same invoices uploaded by hand. Their record IDs
differ, so the key can't catch it. This module compares what the records SAY.

THE FINGERPRINT OF A RECORD
---------------------------
    direction   money in (sales, invoices sent) / money out (expenses, bills,
                bill payments) / refund — so a sales file and QuickBooks
                invoices are compared even though one is "transaction/income"
                and the other "invoice/sent"
    date        the record's event_date
    amount      its total (total_amount; else payment_amount; else the sum of
                its line amounts), to the cent
    reference   its reference number (invoice / order number), when it has one

Two records are the same when direction, date and amount match AND their
reference numbers match — or one of them has no reference number.

WHERE IT IS USED
----------------
  * the load preview (api/routers/warehouse.py): "N of these records are
    already stored from <source>", with "load only the new records" (default);
  * the stored sync (api/services/sync.py): reports how many synced records
    were also in uploaded files (the tool is the source of truth, so the sync
    always stores them; the client can delete the file).
"""

from __future__ import annotations

import uuid
from datetime import date

import pandas as pd

from api.core import database

DIRECTION = {("transaction", "income"): "in", ("invoice", "sent"): "in",
             ("transaction", "expense"): "out", ("invoice", "received"): "out", ("invoice", "paid"): "out",
             ("transaction", "refund"): "refund"}
_AMOUNT_FIELDS = ("total_amount", "payment_amount", "line_amount")   # in order of preference


def _direction(event_type: str, event_subtype: str) -> str:
    return DIRECTION.get((event_type, event_subtype), f"{event_type}/{event_subtype}")


def fingerprints(rows: pd.DataFrame) -> pd.DataFrame:
    """
    One fingerprint per record of unified rows.

    Args:
        rows: unified rows (UNIFIED_COLUMNS), attributes as dicts.

    Returns:
        pd.DataFrame: columns record_id, direction, date (YYYY-MM-DD), amount
        (rounded to cents), reference ("" when none). Records with no date or
        no amount are left out (nothing to compare).

    Example:
        >>> fingerprints(rows).iloc[0].to_dict()
        {'record_id': 'Invoice:130', 'direction': 'in', 'date': '2026-01-14', 'amount': 96.0, 'reference': '1037'}
    """
    if rows.empty:
        return pd.DataFrame(columns=["record_id", "direction", "date", "amount", "reference"])
    out = []
    for rid, rec in rows.groupby("record_id", sort=False):
        dates = pd.to_datetime(rec["event_date"], errors="coerce").dropna()
        amount = None
        for field in _AMOUNT_FIELDS:
            values = pd.to_numeric(rec.loc[rec["field_name"] == field, "metric_value"], errors="coerce").dropna()
            if len(values):
                amount = float(values.sum())
                break
        if dates.empty or amount is None:
            continue
        refs = [a.get("reference_number") for a in rec["attributes"] if isinstance(a, dict) and a.get("reference_number")]
        out.append({"record_id": str(rid), "direction": _direction(rec["event_type"].iloc[0], rec["event_subtype"].iloc[0]),
                    "date": dates.min().date().isoformat(), "amount": round(amount, 2),
                    "reference": str(refs[0]).strip() if refs else ""})
    return pd.DataFrame(out, columns=["record_id", "direction", "date", "amount", "reference"])


async def _stored_fingerprints(aid: str, exclude: tuple[str, str], first: str, last: str,
                               only_systems: set[str] | None) -> pd.DataFrame:
    """Fingerprints of what is already stored from OTHER sources, between two dates."""
    if database.pool is None:
        from api.services import warehouse_store

        frames = []
        for (a, system, name), df in warehouse_store._stored.items():
            if a != aid or (system, name) == exclude or (only_systems and system not in only_systems):
                continue
            fp = fingerprints(df)
            frames.append(fp.assign(source_name=name))
        found = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if found.empty:
            return found
        return found[(found["date"] >= first) & (found["date"] <= last)]
    async with database.pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT set_config('app.account_id', $1, true)", aid)
        records = await conn.fetch(
            """SELECT source_name, record_id, min(event_type) AS event_type, min(event_subtype) AS event_subtype,
                      min(event_date)::text AS date,
                      max(attributes->>'reference_number') AS reference,
                      coalesce(sum(metric_value) FILTER (WHERE field_name = 'total_amount'),
                               sum(metric_value) FILTER (WHERE field_name = 'payment_amount'),
                               sum(metric_value) FILTER (WHERE field_name = 'line_amount'))::float8 AS amount
               FROM fact.account_data
               WHERE account_id = $1 AND NOT (source_system = $2 AND source_name = $3)
                 AND ($6::text[] IS NULL OR source_system = ANY($6::text[]))
                 AND event_date BETWEEN $4::date AND $5::date
               GROUP BY source_name, record_id""",
            uuid.UUID(aid), exclude[0], exclude[1], date.fromisoformat(first), date.fromisoformat(last),
            sorted(only_systems) if only_systems else None)
    out = pd.DataFrame([dict(r) for r in records])
    if out.empty:
        return out
    out = out.dropna(subset=["amount", "date"])
    out["direction"] = [_direction(t, s) for t, s in zip(out["event_type"], out["event_subtype"])]
    out["amount"] = out["amount"].round(2)
    out["reference"] = out["reference"].fillna("").astype(str).str.strip()
    return out[["source_name", "record_id", "direction", "date", "amount", "reference"]]


async def find_duplicates(aid: str, rows: pd.DataFrame, source_system: str, source_name: str,
                          only_systems: set[str] | None = None) -> dict:
    """
    Which incoming records are already stored from ANOTHER source.

    Args:
        aid: str, the account (plan holder).
        rows: the incoming unified rows.
        source_system, source_name: where they come from (that source itself is not compared).
        only_systems: compare only with these systems (e.g. {"file_upload"} for the sync).

    Returns:
        dict: {"records": n duplicates, "of": n incoming records,
        "record_ids": [...] (incoming ids), "sources": [other source names]}.

    Example:
        >>> await find_duplicates(aid, rows, "file_upload", "invoices.csv")
        {'records': 120, 'of': 130, 'record_ids': [...], 'sources': ['QuickBooks Online']}
    """
    incoming = fingerprints(rows)
    none = {"records": 0, "of": len(incoming), "record_ids": [], "sources": []}
    if incoming.empty:
        return none
    stored = await _stored_fingerprints(aid, (source_system, source_name), incoming["date"].min(),
                                        incoming["date"].max(), only_systems)
    if stored.empty:
        return none
    pairs = incoming.merge(stored, on=["direction", "date", "amount"], suffixes=("", "_stored"))
    same = pairs[(pairs["reference"] == pairs["reference_stored"]) | (pairs["reference"] == "")
                 | (pairs["reference_stored"] == "")]
    ids = sorted(same["record_id"].unique())
    return {"records": len(ids), "of": len(incoming), "record_ids": ids,
            "sources": sorted(same["source_name"].unique())}
