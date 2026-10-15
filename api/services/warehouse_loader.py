"""
Plan a load into fact.account_data — and explain every change BEFORE anything is stored.

WHAT THIS FILE DOES
-------------------
Data Engineering clients can have their cleaned data stored in
fact.account_data (db/schema.sql; the design is "Unified data" in
docs/DATA_MODEL.md). Every file is stored in the same shape: IDs, dates and
categories kept as they are, every number that is measured turned into its
own row (metric_type, metric_subtype, metric_unit, metric_value). This module
works out that reshape with api/services/unified.py and produces:

  * the STEPS, each with a plain-English WHY (shown to the client),
  * the COLUMN PLAN: each column's role, its shared name, and — for metrics —
    what it measures, with a confidence (the client can change any of it),
  * a PREVIEW of the first rows exactly as they will be stored,
  * the ROUND-TRIP CHECK: the rows are turned back into the original table
    and compared cell by cell, so nothing can be lost by storing them,
  * the COUNTS and a FINGERPRINT of the plan.

The client reads the preview and either accepts or walks away. Only an
accepted plan is loaded (api/services/warehouse_store.py), and only if its
fingerprint still matches — so what gets stored is exactly what they saw.

Everything here is plain pandas and stores nothing.

THE CLIENT'S CHOICES (all optional; the server guesses the rest)
-----------------------------------------------------------------
    event_type      what the rows are: "transaction/income", "list/records", ...
    metric_columns  exactly which columns are metrics (numbers that are measured)
    id_columns      columns that are IDs (never summed, even if they are numbers)
    metric_map      {column: {"type", "subtype", "unit"}}: fix a metric's description
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pandas as pd

from api.services import unified

# What the rows can be stored as (account_data.event_type / event_subtype).
# The QuickBooks entities in unified.QUICKBOOKS_ENTITIES use these pairs too
# (tests/test_unified.py checks it).
EVENT_TYPES: dict[str, dict] = {
    "transaction/income": {"label": "Sales / income (money in)", "measure": "amount"},
    "transaction/expense": {"label": "Expenses (money out)", "measure": "amount"},
    "transaction/refund": {"label": "Refunds to customers", "measure": "amount"},
    "invoice/sent": {"label": "Invoices you sent", "measure": "amount"},
    "invoice/received": {"label": "Invoices (bills) you received", "measure": "amount"},
    "invoice/paid": {"label": "Invoices paid", "measure": "amount"},
    "feedback/csat": {"label": "Customer satisfaction (CSAT) scores", "measure": "score"},
    "feedback/rating": {"label": "Ratings / reviews", "measure": "score"},
    "feedback/nps": {"label": "Net Promoter Score responses", "measure": "score"},
    "inventory/adjustment": {"label": "Inventory changes", "measure": "quantity"},
    "payroll/gross_pay": {"label": "Payroll (gross pay)", "measure": "amount"},
    "list/records": {"label": "A list, not events (customers, products, employees...)", "measure": "count"},
}

ROLES = {"metric": "Metric (a number that is measured)", "id": "ID", "date": "Date",
         "category": "Category / description"}
PREVIEW_ROWS = 8
CHOICE_KEYS = ("metric_columns", "id_columns", "metric_map")


@dataclass
class LoadPlan:
    """A worked-out load: what the client sees, plus the rows ready to write."""

    source_name: str
    event_type: str
    columns: list[str]
    column_plan: list[dict]
    choices: dict
    steps: list[dict]
    tables: dict[str, dict]
    counts: dict[str, int]
    round_trip: dict
    fingerprint: str
    problem: str | None = None
    source_system: str = "file_upload"
    # The unified rows for warehouse_store (not sent to the browser).
    rows: pd.DataFrame = field(default_factory=pd.DataFrame)

    def to_dict(self) -> dict:
        """
        The plan as sent to the browser (web/lib/types.ts: LoadPlan).

        Returns:
            dict: everything except the prepared rows.
        """
        return {
            "source_name": self.source_name, "source_system": self.source_system, "event_type": self.event_type,
            "event_types": [{"id": k, **v} for k, v in EVENT_TYPES.items()],
            "roles": [{"id": k, "label": v} for k, v in ROLES.items()],
            "columns": self.columns, "column_plan": self.column_plan, "choices": self.choices,
            "steps": self.steps, "tables": self.tables, "counts": self.counts, "round_trip": self.round_trip,
            "fingerprint": self.fingerprint, "problem": self.problem, "ready": self.problem is None,
        }


def _norm(col: str) -> str:
    """Lowercase a column name with spaces/dashes as underscores, for hint matching."""
    return str(col).strip().lower().replace(" ", "_").replace("-", "_")


def suggest_event_type(mapping: dict[str, str | None], columns: list[str] | None = None,
                       source_name: str = "") -> str:
    """
    Guess what the rows ARE from the metrics found, the column names and the file name.

    A score and no amount -> CSAT (or NPS) feedback; expense-like words
    (expense, vendor, supplier, bill, spend) in a column or the file name ->
    expenses; otherwise sales / income. The client can change the guess.

    Args:
        mapping: dict, {"amount": a money column or None, "score": a score column or None}.
        columns: list[str] | None, every column name in the table.
        source_name: str, the table's label or file name.

    Returns:
        str: an EVENT_TYPES key.

    Example:
        >>> suggest_event_type({"score": "csat", "amount": None})
        'feedback/csat'
        >>> suggest_event_type({"amount": "amount"}, ["expense_date", "vendor", "amount"])
        'transaction/expense'
    """
    if mapping.get("score") and not mapping.get("amount"):
        return "feedback/nps" if "nps" in _norm(mapping["score"]) else "feedback/csat"
    words = " ".join(_norm(c) for c in [*(columns or []), mapping.get("amount") or "", source_name])
    if any(w in words for w in ("expense", "vendor", "supplier", "bill", "spend")):
        return "transaction/expense"
    return "transaction/income"


def _clean_choices(choices: dict | None, df: pd.DataFrame) -> dict:
    """
    Check the client's choices and keep only what applies.

    Returns:
        dict: {"metric_columns": list | None, "id_columns": list | None, "metric_map": dict | None}.

    Raises:
        ValueError: an unknown key, a wrong type, or a column that isn't in the table.
    """
    choices = dict(choices or {})
    unknown = [k for k in choices if k not in CHOICE_KEYS]
    if unknown:
        raise ValueError(f"Unknown choice(s): {', '.join(unknown)}.")
    out: dict = {}
    for key in ("metric_columns", "id_columns"):
        value = choices.get(key)
        if value is not None and (not isinstance(value, list) or not all(isinstance(c, str) for c in value)):
            raise ValueError(f"'{key}' must be a list of column names.")
        out[key] = value
    metric_map = choices.get("metric_map")
    if metric_map is not None and not isinstance(metric_map, dict):
        raise ValueError("'metric_map' must be an object like {\"amount\": {\"type\": \"amount\", \"unit\": \"usd\"}}.")
    out["metric_map"] = metric_map or None
    for col in (out["metric_columns"] or []) + (out["id_columns"] or []) + list(out["metric_map"] or {}):
        if col not in df.columns:
            raise ValueError(f"Column '{col}' isn't in this table.")
    return out


def _table(rows: list[dict], columns: list[str], total: int, limit: int | None = PREVIEW_ROWS) -> dict:
    """A preview table: the first rows (all when limit is None) and the full count."""
    shown = rows if limit is None else rows[:limit]
    return {"columns": columns, "rows": [{c: r.get(c) for c in columns} for r in shown], "total": total}


def _join(names: list[str], limit: int = 8) -> str:
    """Up to `limit` names joined with commas, then "…"."""
    return ", ".join(map(str, names[:limit])) + ("…" if len(names) > limit else "")


def build_plan(df: pd.DataFrame, source_name: str, choices: dict | None = None,
               event_type: str | None = None) -> LoadPlan:
    """
    Work out how a cleaned table becomes fact.account_data rows, with the WHY of each step.

    Args:
        df: pd.DataFrame, the cleaned table (the same data the client previewed).
        source_name: str, what the source is called (the table's label / file name).
        choices: dict | None, the client's changes (see the module docstring).
        event_type: str | None, an EVENT_TYPES key; guessed when None.

    Returns:
        LoadPlan: steps, column plan, preview, round-trip check, counts,
        fingerprint and the prepared rows. `problem` is set (and nothing can be
        loaded) when the table is empty or the round trip fails.

    Raises:
        ValueError: for an unknown event type, a bad choice, or a column that doesn't exist.

    Example:
        >>> plan = build_plan(pd.DataFrame({"order_date": ["2024-10-11"], "amount": [21.0]}), "sales.csv")
        >>> plan.event_type, plan.counts["rows_to_load"], plan.round_trip["ok"]
        ('transaction/income', 1, True)
    """
    clean = _clean_choices(choices, df)
    if event_type is not None and event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type '{event_type}'.")
    guessing = event_type is None
    etype, esub = event_type.split("/") if event_type else (None, None)
    # A guessed event leaves "amount means cost?" as a question for the client;
    # a chosen one settles it.
    plan = unified.plan_columns(df, event_type=etype, event_subtype=esub, **clean)
    metrics = [p for p in plan if p["role"] == "metric"]
    kept = [p for p in plan if p["role"] != "metric"]
    if guessing:
        first = lambda types: next((p["column"] for p in metrics if p["metric_type"] in types), None)   # noqa: E731
        event_type = (suggest_event_type({"amount": first(("amount", "cost")), "score": first(("score",))},
                                         [str(c) for c in df.columns], source_name)
                      if metrics else "list/records")
    store_type, store_sub = event_type.split("/")
    columns = [str(c) for c in df.columns]

    def finish(steps, tables, counts, check, rows, problem=None) -> LoadPlan:
        # The fingerprint covers the choices AND the data, so a changed file or
        # choice can't be loaded under an old acceptance.
        keys = rows[["record_id", "line_number", "field_name", "row_hash"]].astype(str).values.tolist() if len(rows) else []
        digest = hashlib.sha256(json.dumps(
            {"source": source_name, "type": event_type, "choices": clean, "rows": keys},
            sort_keys=True, default=str).encode()).hexdigest()[:24]
        return LoadPlan(source_name=source_name, event_type=event_type, columns=columns, column_plan=plan,
                        choices=clean, steps=steps, tables=tables, counts=counts, round_trip=check,
                        fingerprint="" if problem else digest, problem=problem, rows=rows)

    steps: list[dict] = [{
        "title": f"Store the rows as \"{EVENT_TYPES[event_type]['label']}\"",
        "why": "Every source is stored in one table, fact.account_data. The type says what the rows are, so one "
               "query or dashboard works across your sales, expenses, invoices and feedback.",
    }]
    if df.empty:
        return finish(steps, {}, {"rows_in": 0, "records": 0, "rows_to_load": 0, "metrics": 0, "kept": 0,
                                  "needs_review": 0, "duplicates": 0, "blanks": 0},
                      {"ok": False, "cells_checked": 0, "mismatches": 0, "examples": []},
                      pd.DataFrame(), "The table has no rows, so there is nothing to load.")

    # 1. Name each record, then reshape. keep_identical_rows=True keeps the
    #    rows lined up for the round-trip proof; exact copies are then dropped
    #    below (stored once), exactly as the database would.
    id_col = unified.pick_record_id_column(df, plan)
    date_col = next((p["column"] for p in plan if p["role"] == "date"), None)
    long = unified.dataframe_to_unified(df, event_type=store_type, event_subtype=store_sub, date_column=date_col,
                                        id_column=id_col, plan=plan, keep_identical_rows=True)
    check = unified.round_trip(df, long, plan)
    # Exact copies got IDs ending in "#2", "#3"... (a fingerprint never contains "#").
    copy = long["record_id"].str.contains("#", regex=False) if not id_col else pd.Series(False, index=long.index)
    rows = long[~copy].reset_index(drop=True)
    rows.attrs["plan"] = plan
    duplicates = int(long.loc[copy, "record_id"].nunique())

    # 2. The steps, in plain English.
    if kept:
        steps.append({
            "title": f"Keep {len(kept)} column(s) exactly as they are: {_join([p['column'] for p in kept])}",
            "why": "IDs, dates and categories (store, customer, product, age group...) describe each record. They "
                   "are stored unchanged with every number of that record, so you can filter and group by them.",
        })
    renamed = [p for p in plan if p["mapped"] and unified.snake_case(p["column"]) != p["field_name"]]
    if renamed:
        steps.append({
            "title": "Use the shared names: " + _join([f"{p['column']} → {p['field_name']}" for p in renamed], 6),
            "why": "Different sources name the same thing differently (QuickBooks says TxnDate, your file says "
                   "order_date). Shared names line them up; your own column name is kept too (source_field), so "
                   "every value can be traced back.",
        })
    if metrics:
        steps.append({
            "title": f"Turn {len(metrics)} number column(s) into metric rows: {_join([p['column'] for p in metrics])}",
            "why": "Each number becomes one row with what it measures (type), which one (subtype), its unit and the "
                   "value. A new kind of number from a new source is then just a new type — never a new column.",
        })
    else:
        steps.append({
            "title": "No number columns: store one row per record with its details",
            "why": "This table has nothing to add up (a list of customers or products, say). Each record is still "
                   "stored once, with all of its columns, so it can be joined with your other data later.",
        })
    review = [p["column"] for p in metrics if p["confidence"] in ("low", "medium")]
    if review:
        steps.append({
            "title": f"Please check: {_join(review)}",
            "why": "We guessed what these measure from their names (see the confidence below). Change the type, "
                   "subtype or unit if it's wrong — they are stored with every value.",
        })
    if id_col:
        repeats = not df[id_col].is_unique
        steps.append({
            "title": f"Use \"{id_col}\" as each record's ID" + (" (repeats = lines 1, 2, 3 of that record)" if repeats else ""),
            "why": "Loading the same rows again (an export that overlaps, say) won't create duplicates: a stored "
                   "record whose content hasn't changed is skipped; a changed one is updated.",
        })
    else:
        steps.append({
            "title": "Give each row an ID made from its content (a fingerprint)",
            "why": "The table has no column that names each row, so the row's own values make its ID. The same row "
                   "loaded again gets the same ID, so it is never stored twice.",
        })
    if duplicates:
        steps.append({"title": f"Store {duplicates:,} exact duplicate row(s) once",
                      "why": "These rows are identical to an earlier row in the file, so storing them again would "
                             "double-count them."})
    if date_col is not None:
        undated = int(df[date_col].notna().sum() - pd.to_datetime(df[date_col].astype("string"), errors="coerce",
                                                                    format="mixed").notna().sum())
        if undated:
            steps.append({"title": f"Keep {undated:,} value(s) of \"{date_col}\" that aren't dates as written",
                          "why": "They are stored unchanged (nothing is lost), but those rows have no date, so "
                                 "they won't show on time charts until the value is fixed."})
    measured = rows[rows["field_name"] != unified.NO_METRIC]
    blanks = int(df[[p["column"] for p in metrics]].isna().sum().sum()) if metrics else 0
    if blanks:
        steps.append({"title": f"Don't store {blanks:,} blank number(s)",
                      "why": "A blank has no value to store: no row means no value. Rebuilding the table still "
                             "gives back the blank."})
    if check["ok"]:
        steps.append({
            "title": f"Checked: rebuilding your table from these rows gives back all {check['cells_checked']:,} "
                     "cells exactly",
            "why": "We turned the rows back into your original table and compared every cell. Nothing is lost by "
                   "storing your data this way, and \"Use my stored data\" gives you the same table back.",
        })
    steps.append({"title": "Tag every row with your account",
                  "why": "All accounts share the same table; each query only ever reads your rows, and \"Delete "
                         "stored data\" removes all of them at once."})

    # 3. What the client sees: the column plan and the first stored rows.
    plan_rows = [{"column": p["column"], "role": p["role"], "stored_as": p["field_name"],
                  "metric_type": p["metric_type"], "metric_subtype": p["metric_subtype"],
                  "metric_unit": p["metric_unit"], "confidence": p["confidence"]} for p in plan]
    preview = [{"record_id": r["record_id"], "line_number": r["line_number"], "field_name": r["field_name"],
                "metric_type": r["metric_type"], "metric_subtype": r["metric_subtype"],
                "metric_unit": r["metric_unit"],
                "metric_value": None if pd.isna(r["metric_value"]) else float(r["metric_value"]),
                "source_field": r["source_field"], "attributes": unified.attributes_json(r["attributes"])}
               for r in rows.head(PREVIEW_ROWS).to_dict("records")]
    tables = {
        "columns": _table(plan_rows, ["column", "role", "stored_as", "metric_type", "metric_subtype", "metric_unit",
                                      "confidence"], len(plan_rows), limit=None),
        "account_data": _table(preview, ["record_id", "line_number", "field_name", "metric_type", "metric_subtype",
                                         "metric_unit", "metric_value", "source_field", "attributes"], len(rows)),
    }
    counts = {"rows_in": len(df), "records": int(rows[["record_id", "line_number"]].drop_duplicates().shape[0]),
              "rows_to_load": len(rows), "metrics": len(metrics), "kept": len(kept), "needs_review": len(review),
              "duplicates": duplicates, "blanks": blanks, "metric_rows": len(measured)}
    problem = None
    if not check["ok"]:
        problem = (f"Rebuilding the table from the rows didn't give back every cell ({check['mismatches']:,} of "
                   f"{check['cells_checked']:,} differ, e.g. {check['examples'][:1]}). Nothing can be loaded: "
                   "check that number columns hold only numbers, or mark the column as a category.")
    return finish(steps, tables, counts, check, rows, problem)
