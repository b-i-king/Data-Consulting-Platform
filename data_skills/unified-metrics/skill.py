"""
unified-metrics — the TEST BENCH for the unified data design (Data Engineering).
It runs a cleaned table through the same code that will fill
`warehouse.unified_data` (api/services/unified.py), shows every decision, and
proves by round trip that nothing is lost. It stores nothing.

Python implementation of README.md in this folder (written for this platform).

THE IDEA
--------
IDs, dates and categories (demographics) stay as they are; each metric
becomes its own ROW described the same way for every client:

    metric_type     what is measured            price, cost, amount, score, quantity
    metric_subtype  which one (optional)        unit, line, shipping, csat
    metric_unit     what the number is in       usd, count, score, percent, hours
    metric_value    the number itself
    field_name      the metric's unified name   line_amount, unit_price (via field_map)
    source_field    the original column name    (so it can always be traced back)

    wide:  order_id | order_date | store   | quantity | unit_price | amount
           50001    | 2024-10-11 | Midtown | 3        | 7.00       | 21.00

    long:  reference_number | transaction_date | location_name | metric_type | metric_subtype | metric_unit | metric_value
           50001            | 2024-10-11       | Midtown       | quantity    |                | count       | 3
           50001            | 2024-10-11       | Midtown       | price       | unit           | usd         | 7.00
           50001            | 2024-10-11       | Midtown       | amount      | line           | usd         | 21.00

(the kept columns are shown by their unified names; in the database they sit
in the `attributes` column of every row.)

HOW IT DECIDES (and where a person must)
----------------------------------------
1. Column roles and names (unified.plan_columns): the shared field map first
   (order_date -> transaction_date, amount -> line_amount, a metric); then
   dates -> date, ID-looking names -> id, numbers -> metric, the rest -> category.
2. Metrics nobody mapped are described from the words in their name
   (unified.classify_metric) with a confidence; "low" / "medium" -> NEEDS
   REVIEW. Correct them with `metric_map` and run again — that's the loop to
   test before loading. The reviewed columns come out as ready-made
   `warehouse.field_map` rows, so the next client file with the same headers
   is mapped automatically.
3. ROUND TRIP (unified.round_trip): the unified rows are pivoted back to the
   original columns and compared cell by cell — every column, not just the metrics.
"""

from __future__ import annotations

import json

import pandas as pd

from api.services import unified
from api.services.warehouse_loader import EVENT_TYPES, suggest_event_type
from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, records

SKILL = SkillSpec(
    id="unified-metrics",
    title="Unified metrics (long format) test bench",
    description="Turn metric columns into rows (metric_type, subtype, unit, value), keep IDs, dates and "
                "categories, and verify by round trip that no information is lost — the same code that fills "
                "warehouse.unified_data. Stores nothing.",
    params={
        "metric_columns": Param("columns", None, "Columns to treat as metrics (default: every number column that isn't an ID)"),
        "id_columns": Param("columns", None, "Columns to keep as IDs (default: guessed from the names)"),
        "metric_map": Param("json", None, 'Fix the guesses, e.g. {"unit_price": {"type": "price", "subtype": "unit", "unit": "usd"}}'),
        "event": Param("choice", "auto", "What each row is (default: guessed from the columns)",
                       choices=("auto", *EVENT_TYPES)),
        "preview_rows": Param("int", 200, "How many unified rows to show"),
        "full_table": Param("bool", False, "Also save the whole unified table as CSV", api_allowed=False),
    },
)


def _record_id_column(df: pd.DataFrame, plan: list[dict]) -> str | None:
    """
    The ID column that names each record, if there is one.

    First choice: an id column with no blanks and no repeats (one row per
    record). Second: a document number (order / invoice / receipt number,
    unified name reference_number) with no blanks that repeats — one row per
    LINE of an order; unified.py then numbers the lines 1, 2, 3. A repeating
    customer_id is never used (a customer is not a record). Without either,
    unified.py gives each row an ID made from its content.

    Example:
        >>> d = pd.DataFrame({"customer_id": [1, 1], "order_id": [7, 7]})
        >>> _record_id_column(d, [{"column": "customer_id", "role": "id", "field_name": "customer_source_id"},
        ...                       {"column": "order_id", "role": "id", "field_name": "reference_number"}])
        'order_id'
    """
    ids = [p for p in plan if p["role"] == "id" and df[p["column"]].notna().all()]
    unique = next((p["column"] for p in ids if df[p["column"]].is_unique), None)
    document = next((p["column"] for p in ids if p["field_name"] == "reference_number"), None)
    return unique or document


def _field_map_sql(plan: list[dict]) -> str:
    """
    Ready-to-run warehouse.field_map rows for the columns the shared map doesn't know yet.

    Run them (after checking) and the next upload with the same headers is
    named and described automatically, with mapped = true.

    Example:
        >>> "widgets" in _field_map_sql([{"column": "widgets", "field_name": "widgets", "mapped": False,
        ...     "value_type": "number", "metric_type": "quantity", "metric_subtype": None,
        ...     "metric_unit": "count", "confidence": "set by you"}])
        True
    """
    def q(v):
        return "NULL" if v is None else "'" + str(v).replace("'", "''") + "'"

    new = [p for p in plan if not p["mapped"] or p["confidence"] == "set by you"]
    if not new:
        return "-- Every column is already in warehouse.field_map: nothing to add."
    rows = [f"    ('file_upload', {q(unified.snake_case(p['column']))}, {q(p['field_name'])}, {q(p['value_type'])}, "
            f"{q(p['metric_type'])}, {q(p['metric_subtype'])}, {q(p['metric_unit'])}, "
            f"{q('reviewed with the unified-metrics skill' if p['metric_type'] else None)})" for p in new]
    return ("-- Proposed warehouse.field_map rows for this file's new columns (check them first;\n"
            "-- metric_type NULL = kept as an attribute). Then add the same pairs to\n"
            "-- FILE_UPLOAD_FIELD_MAP in api/services/unified.py (a test keeps them identical).\n"
            "INSERT INTO warehouse.field_map (system, source_field, field_name, value_type,\n"
            "                                 metric_type, metric_subtype, metric_unit, notes) VALUES\n"
            + ",\n".join(rows) + "\nON CONFLICT (system, source_field) DO NOTHING;")


def _readable(long: pd.DataFrame) -> pd.DataFrame:
    """
    The unified rows as a flat table to read: the kept columns spelled out, then the metric columns.

    (In the database the kept columns are one JSON `attributes` column.)
    """
    kept = pd.DataFrame(long["attributes"].tolist(), index=long.index)
    metric = long[["metric_type", "metric_subtype", "metric_unit", "metric_value", "field_name", "source_field"]]
    return pd.concat([long[["record_id"]], kept, metric], axis=1)


def run(df, metric_columns=None, id_columns=None, metric_map=None, event="auto", preview_rows=200,
        full_table=False) -> SkillResult:
    """
    Reshape the table into unified rows, explain every choice, and verify by round trip.

    Args:
        df: pd.DataFrame, the cleaned table.
        metric_columns: list[str] | None, metric columns (default: guessed).
        id_columns: list[str] | None, id columns (default: guessed).
        metric_map: dict | None, {column: {"type", "subtype", "unit"}} to override guesses.
        event: str, "auto" or an EVENT_TYPES key such as "transaction/expense".
        preview_rows: int, unified rows shown in the "unified_preview" table.
        full_table: bool, command line only: add the whole unified table ("unified_full").

    Returns:
        SkillResult: tables "column_roles", "metric_definitions", "unified_preview"
        (+ "unified_full"); findings incl. columns needing review; data
        {"round_trip", "rows_in", "rows_out", "metrics", "needs_review", "event",
        "record_id_column"}; artifacts "json_metric_map" (edit and re-run) and
        "sql_field_map" (warehouse.field_map rows for the new columns).

    Raises:
        SkillInputError: when no metric column is found, or an option is malformed.

    Example:
        >>> r = run(sales)
        >>> r.data["round_trip"]["ok"]
        True
    """
    if metric_map is not None and not isinstance(metric_map, dict):
        raise SkillInputError('metric_map must be a JSON object like {"amount": {"type": "amount", "unit": "usd"}}.')
    chosen = None if event in (None, "", "auto") else event
    if chosen is not None and chosen not in EVENT_TYPES:
        raise SkillInputError(f"Unknown event '{event}'. Choose one of: {', '.join(EVENT_TYPES)}.")
    event_type, event_subtype = chosen.split("/") if chosen else (None, None)
    try:
        plan = unified.plan_columns(df, event_type=event_type, event_subtype=event_subtype,
                                    metric_columns=metric_columns, id_columns=id_columns, metric_map=metric_map)
    except ValueError as e:
        raise SkillInputError(str(e)) from e
    metrics = [p for p in plan if p["role"] == "metric"]
    if not metrics:
        raise SkillInputError("No metric (number) columns found. Pick them with the metric_columns option.")

    # The event the rows would be stored as. A guess (from the columns) leaves
    # the expense rule as a "please confirm"; a chosen event makes it certain.
    if chosen is None:
        first = lambda types: next((p["column"] for p in metrics if p["metric_type"] in types), None)   # noqa: E731
        chosen = suggest_event_type({"amount": first(("amount", "cost")), "score": first(("score",))},
                                    [str(c) for c in df.columns])
    store_type, store_subtype = chosen.split("/")

    id_col = _record_id_column(df, plan)
    date_col = next((p["column"] for p in plan if p["role"] == "date"), None)
    # keep_identical_rows: compare row for row in the round trip (the database
    # would store identical rows once; that's reported below).
    long = unified.dataframe_to_unified(df, event_type=store_type, event_subtype=store_subtype,
                                        date_column=date_col, id_column=id_col, plan=plan,
                                        keep_identical_rows=True)
    check = unified.round_trip(df, long, plan)
    review = [p["column"] for p in metrics if p["confidence"] in ("low", "medium")]
    kept = [p for p in plan if p["role"] != "metric"]
    identical = int(df.duplicated().sum())

    role_rows = [{"column": p["column"], "role": p["role"], "unified_name": p["field_name"],
                  "in_field_map": p["mapped"], "kept_as_is": p["role"] != "metric"} for p in plan]
    def_rows = [{"source_column": p["column"], "field_name": p["field_name"], "metric_type": p["metric_type"],
                 "metric_subtype": p["metric_subtype"], "metric_unit": p["metric_unit"],
                 "confidence": p["confidence"], "why": p["why"], "values": int(df[p["column"]].notna().sum())}
                for p in metrics]

    measured = long[long["field_name"] != unified.NO_METRIC]
    blanks = len(df) * len(metrics) - len(measured)
    findings = [
        f"{len(df):,} row(s) × {len(metrics)} metric column(s) → {len(measured):,} unified row(s) "
        f"({blanks:,} blank metric value(s) left out: no row = no value).",
        "Kept as they are (in attributes): "
        + (", ".join(f"{p['column']} ({p['role']}" + (f" → {p['field_name']})" if p['field_name'] != p['column'] else ")")
                     for p in kept) or "nothing") + ".",
        f"Stored as event {chosen} ({EVENT_TYPES[chosen]['label']}"
        + ("; guessed from the columns — set the event option to be sure)." if event in (None, "", "auto") else ")."),
        (f"Each record is named by {id_col}." if id_col and df[id_col].is_unique else
         f"Each record is named by {id_col}; it repeats, so its rows are numbered as lines 1, 2, 3 "
         "(one order, several items)." if id_col else
         "No ID column names each row, so each row's ID is a fingerprint of its content (the same row "
         "uploaded twice is stored once)."),
    ]
    for p in metrics:
        findings.append(f"{p['column']} → {p['field_name']}: type {p['metric_type']}, subtype "
                        f"{p['metric_subtype'] or '—'}, unit {p['metric_unit']} ({p['confidence']}: {p['why']}).")
    if check["ok"]:
        findings.append(f"Round trip passed: all {check['cells_checked']:,} cells (every column) rebuilt "
                        "exactly — no information lost.")
    else:
        findings.append(f"Round trip FAILED: {check['mismatches']:,} of {check['cells_checked']:,} cells differ "
                        f"(first: {check['examples'][:1]}). Check columns holding text in a number column.")
    if identical and not id_col:
        findings.append(f"{identical:,} row(s) are exact copies of an earlier row; the warehouse would store "
                        "each of them once.")
    if review:
        findings.append("Needs a person's decision: " + ", ".join(review)
                        + ". Edit the json_metric_map file and run again with metric_map.")

    readable = _readable(long)
    tables = {"column_roles": role_rows, "metric_definitions": def_rows,
              "unified_preview": records(readable, limit=max(1, int(preview_rows)))}
    if full_table:
        tables["unified_full"] = records(readable, limit=None)

    edit_me = {p["column"]: {"type": p["metric_type"], "subtype": p["metric_subtype"], "unit": p["metric_unit"]}
               for p in metrics}
    summary = (f"{len(metrics)} metric column(s) became {len(measured):,} unified row(s) with type, subtype and unit; "
               + ("round trip passed (nothing lost)" if check["ok"] else "round trip FAILED")
               + (f"; {len(review)} column(s) need review." if review else "."))
    return SkillResult(
        skill=SKILL.id, summary=summary, findings=findings, tables=tables,
        artifacts={"json_metric_map": json.dumps(edit_me, indent=2), "sql_field_map": _field_map_sql(plan)},
        data={"round_trip": check, "rows_in": len(df), "rows_out": len(measured),
              "metrics": [p["column"] for p in metrics], "needs_review": review, "event": chosen,
              "record_id_column": id_col},
    )
