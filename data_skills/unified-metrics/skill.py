"""
unified-metrics — reshape a cleaned table into the UNIFIED (long) metric format,
and prove nothing was lost (Data Engineering). A test bench: it stores nothing.

Python implementation of README.md in this folder (written for this platform).

THE IDEA
--------
Every client's spreadsheet has different metric columns (amount, unit_price,
shipping_cost, csat, hours_worked...). A unified table stores them all the
same way: IDs, dates and categories (demographics) stay as they are; each
metric becomes its own ROW described by

    metric_type     what is measured            price, cost, amount, score, quantity
    metric_subtype  which one (optional)        unit, shipping, csat, nps
    metric_unit     what the number is in       usd, count, score, percent, hours
    metric_value    the number itself
    source_column   the original column name    (so it can always be traced back)

    wide:  order_id | order_date | store   | quantity | unit_price | amount
           50001    | 2024-10-11 | Midtown | 3        | 7.00       | 21.00

    long:  order_id | order_date | store   | metric_type | metric_subtype | metric_unit | metric_value | source_column
           50001    | 2024-10-11 | Midtown | quantity    |                | count       | 3            | quantity
           50001    | 2024-10-11 | Midtown | price       | unit           | usd         | 7.00         | unit_price
           50001    | 2024-10-11 | Midtown | amount      |                | usd         | 21.00        | amount

HOW IT DECIDES (and where a person must)
----------------------------------------
1. Column roles: dates -> date; id-looking names -> id; text / yes-no -> category;
   numbers -> metric. Override any of them with the options.
2. Each metric column's name is split into words and matched to a list of
   known measures ("price", "cost", "csat"...). The matched word is the TYPE,
   the remaining words the SUBTYPE, and the unit comes from the type.
   A name with no known word gets confidence "low" -> NEEDS REVIEW. Correct it
   with `metric_map` and run again — that's the loop to test before building.
3. ROUND TRIP: the long table is pivoted back to wide and compared with the
   original, cell by cell. "Same information" is checked, not assumed.

Nothing is written to the database: this is the prototype for a future
`fact_metric` table (docs/DATA_MODEL.md -> "Unified metrics").
"""

from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, date_columns, numeric_columns, records

SKILL = SkillSpec(
    id="unified-metrics",
    title="Unified metrics (long format) test bench",
    description="Turn metric columns into rows (metric_type, subtype, unit, value), keep IDs, dates and "
                "categories, and verify by round trip that no information is lost. Stores nothing.",
    params={
        "metric_columns": Param("columns", None, "Columns to treat as metrics (default: every number column that isn't an ID)"),
        "id_columns": Param("columns", None, "Columns to keep as IDs (default: guessed from the names)"),
        "metric_map": Param("json", None, 'Fix the guesses, e.g. {"unit_price": {"type": "price", "subtype": "unit", "unit": "usd"}}'),
        "keep_missing": Param("bool", False, "Keep a row for blank metric values (default: leave them out)"),
        "preview_rows": Param("int", 200, "How many unified rows to show"),
        "full_table": Param("bool", False, "Also save the whole unified table as CSV", api_allowed=False),
    },
)

# Known measures: (word in the column name) -> (metric_type, unit, default subtype).
# The FIRST word found in the name wins, scanning this list top to bottom, so
# more specific words come first ("nps" before "score").
KNOWN: list[tuple[str, str, str, str | None]] = [
    # word         type          unit       subtype when the word itself is specific
    ("csat",       "score",      "score",   "csat"),
    ("nps",        "score",      "score",   "nps"),
    ("rating",     "score",      "score",   "rating"),
    ("stars",      "score",      "score",   "rating"),
    ("score",      "score",      "score",   None),
    ("price",      "price",      "usd",     None),
    ("cost",       "cost",       "usd",     None),
    ("expense",    "cost",       "usd",     "expense"),
    ("spend",      "cost",       "usd",     "spend"),
    ("fee",        "cost",       "usd",     "fee"),
    ("tax",        "tax",        "usd",     None),
    ("discount",   "discount",   "usd",     None),
    ("tip",        "amount",     "usd",     "tip"),
    ("revenue",    "amount",     "usd",     "revenue"),
    ("sales",      "amount",     "usd",     "sales"),
    ("income",     "amount",     "usd",     "income"),
    ("amount",     "amount",     "usd",     None),
    ("total",      "amount",     "usd",     "total"),
    ("balance",    "balance",    "usd",     None),
    ("salary",     "pay",        "usd",     "salary"),
    ("wage",       "pay",        "usd",     "wage"),
    ("pay",        "pay",        "usd",     None),
    ("profit",     "profit",     "usd",     None),
    ("margin",     "margin",     "percent", None),
    ("pct",        "rate",       "percent", None),
    ("percent",    "rate",       "percent", None),
    ("rate",       "rate",       "percent", None),
    ("hours",      "time",       "hours",   None),
    ("hrs",        "time",       "hours",   None),
    ("minutes",    "time",       "minutes", None),
    ("mins",       "time",       "minutes", None),
    ("days",       "time",       "days",    None),
    ("quantity",   "quantity",   "count",   None),
    ("qty",        "quantity",   "count",   None),
    ("units",      "quantity",   "count",   None),
    ("count",      "count",      "count",   None),
    ("visits",     "count",      "count",   "visits"),
    ("orders",     "count",      "count",   "orders"),
    ("age",        "age",        "years",   None),
    ("weight",     "weight",     "lbs",     None),
]
# Words in OTHER columns that say "this table is expenses", so its amounts are costs.
EXPENSE_HINTS = {"expense", "expenses", "vendor", "supplier", "bill", "payee"}
_ID_WORDS = re.compile(r"(^id$|_id$|^id_|_no$|_num$|number$|code$|zip|postal|phone|sku|^key$|_key$)", re.I)


def _words(name: str) -> list[str]:
    """Split a column name into lowercase words: "Unit Price ($)" -> ["unit", "price"]."""
    return [w for w in re.split(r"[^a-z0-9]+", str(name).lower()) if w]


def classify_metric(column: str) -> dict:
    """
    Guess metric_type, metric_subtype and metric_unit from a column name.

    The name's words are checked against KNOWN; the first match gives the type
    and unit. The OTHER words become the subtype ("shipping_cost" -> cost /
    shipping). No match: the type is the column name itself, unit "number",
    confidence "low" (a person should decide).

    Args:
        column: str, the metric column's name.

    Returns:
        dict: {"type", "subtype", "unit", "confidence" ("high" | "medium" | "low"), "why"}.

    Example:
        >>> classify_metric("shipping_cost")
        {'type': 'cost', 'subtype': 'shipping', 'unit': 'usd', 'confidence': 'high', 'why': 'found "cost" in the name'}
        >>> classify_metric("widgets")["confidence"]
        'low'
    """
    words = _words(column)
    for word, mtype, unit, default_sub in KNOWN:
        if word in words:
            rest = [w for w in words if w != word]
            subtype = "_".join(rest) or default_sub
            # A leftover word we can't place (e.g. "x" in "x_amount") lowers confidence a little.
            confidence = "high" if len(rest) <= 2 else "medium"
            return {"type": mtype, "subtype": subtype, "unit": unit, "confidence": confidence,
                    "why": f'found "{word}" in the name'}
    return {"type": "_".join(words) or str(column), "subtype": None, "unit": "number", "confidence": "low",
            "why": "no known measure in the name — please set type, subtype and unit"}


def column_roles(df: pd.DataFrame, metric_columns=None, id_columns=None) -> dict[str, str]:
    """
    Decide each column's role: "id", "date", "category" or "metric".

    Order: chosen ids/metrics first; then dates; then id-looking names (order_id,
    sku, phone...); number columns are metrics; everything else (text, yes/no)
    is a category — demographic and descriptive fields that stay as they are.

    Args:
        df: pd.DataFrame, the cleaned table.
        metric_columns: list[str] | None, force these to be metrics.
        id_columns: list[str] | None, force these to be ids.

    Returns:
        dict: column -> role, in the table's column order.

    Raises:
        SkillInputError: for a chosen column that isn't in the table.

    Example:
        >>> column_roles(pd.DataFrame({"order_id": [1], "store": ["A"], "amount": [2.0]}))
        {'order_id': 'id', 'store': 'category', 'amount': 'metric'}
    """
    for chosen in (metric_columns or []) + (id_columns or []):
        if chosen not in df.columns:
            raise SkillInputError(f"Column '{chosen}' isn't in this table.")
    dates = set(date_columns(df))
    numbers = set(numeric_columns(df, exclude_ids=False))
    roles: dict[str, str] = {}
    for col in df.columns:
        if id_columns and col in id_columns:
            roles[col] = "id"
        elif metric_columns is not None:
            roles[col] = "metric" if col in metric_columns else (
                "date" if col in dates else "id" if _ID_WORDS.search(str(col)) else "category")
        elif col in dates:
            roles[col] = "date"
        elif _ID_WORDS.search(str(col)):
            roles[col] = "id"
        elif col in numbers and not pd.api.types.is_bool_dtype(df[col]):
            roles[col] = "metric"
        else:
            roles[col] = "category"
    return roles


def unify(df: pd.DataFrame, roles: dict[str, str], meta: dict[str, dict], keep_missing: bool = False) -> pd.DataFrame:
    """
    The reshape itself: one row per (original row, metric column).

    Uses pandas `melt`: the id, date and category columns are kept on every
    row ("id_vars"), the metric columns are turned into rows. A `row_id`
    (1, 2, 3... in the original order) is added so every value can be traced
    back to its original row — that's what makes the round trip exact.

    Args:
        df: pd.DataFrame, the cleaned table.
        roles: dict, column -> role (from column_roles).
        meta: dict, metric column -> {"type", "subtype", "unit"}.
        keep_missing: bool, True to keep rows whose value is blank.

    Returns:
        pd.DataFrame: row_id, the kept columns, metric_type, metric_subtype,
        metric_unit, metric_value, source_column.

    Example:
        >>> wide = pd.DataFrame({"store": ["A"], "amount": [2.0]})
        >>> unify(wide, {"store": "category", "amount": "metric"},
        ...       {"amount": {"type": "amount", "subtype": None, "unit": "usd"}})[["store", "metric_type", "metric_value"]].values.tolist()
        [['A', 'amount', 2.0]]
    """
    metrics = [c for c, r in roles.items() if r == "metric"]
    kept = [c for c, r in roles.items() if r != "metric"]
    base = df.reset_index(drop=True).copy()
    base.insert(0, "row_id", np.arange(1, len(base) + 1))
    long = base.melt(id_vars=["row_id", *kept], value_vars=metrics, var_name="source_column", value_name="metric_value")
    if not keep_missing:
        long = long[long["metric_value"].notna()]
    long["metric_type"] = long["source_column"].map(lambda c: meta[c]["type"])
    long["metric_subtype"] = long["source_column"].map(lambda c: meta[c].get("subtype"))
    long["metric_unit"] = long["source_column"].map(lambda c: meta[c]["unit"])
    order = ["row_id", *kept, "metric_type", "metric_subtype", "metric_unit", "metric_value", "source_column"]
    return long[order].sort_values(["row_id", "source_column"], kind="stable").reset_index(drop=True)


def round_trip(df: pd.DataFrame, long: pd.DataFrame, metrics: list[str]) -> dict:
    """
    Prove nothing was lost: pivot the long table back to wide and compare.

    `pivot` puts each source_column back as a column, by row_id. Values are
    compared as numbers (blanks equal blanks). The check covers every metric
    cell of every row.

    Args:
        df: pd.DataFrame, the original cleaned table.
        long: pd.DataFrame, the output of unify().
        metrics: list[str], the metric columns.

    Returns:
        dict: {"ok": bool, "cells_checked": int, "mismatches": int, "examples": [...]} —
        examples list up to 5 differing (row_id, column, original, rebuilt).

    Example:
        >>> round_trip(wide, unify(wide, roles, meta), ["amount"])["ok"]
        True
    """
    original = df.reset_index(drop=True)[metrics].apply(pd.to_numeric, errors="coerce")
    original.index = np.arange(1, len(original) + 1)
    rebuilt = (long.pivot(index="row_id", columns="source_column", values="metric_value")
               .reindex(index=original.index, columns=metrics)
               .apply(pd.to_numeric, errors="coerce"))
    same = (original == rebuilt) | (original.isna() & rebuilt.isna())
    bad = ~same
    examples = []
    for row_id, col in zip(*np.where(bad.to_numpy())):
        if len(examples) == 5:
            break
        examples.append({"row_id": int(original.index[row_id]), "column": metrics[col],
                         "original": original.iat[row_id, col], "rebuilt": rebuilt.iat[row_id, col]})
    return {"ok": not bad.to_numpy().any(), "cells_checked": int(bad.size),
            "mismatches": int(bad.to_numpy().sum()), "examples": examples}


def _ddl(kept: dict[str, str]) -> str:
    """The proposed warehouse table for this shape (a proposal — not applied anywhere)."""
    return f"""-- PROPOSAL ONLY (unified-metrics skill). Not in db/warehouse.sql yet.
-- One row per metric value. IDs, dates and categories of the source row
-- live on the row (or, in the warehouse, in its dimensions); this table
-- adds the metric description so ANY client's metrics fit the same columns.
CREATE TABLE IF NOT EXISTS warehouse.fact_metric (
    metric_id       BIGSERIAL PRIMARY KEY,
    client_id       UUID NOT NULL REFERENCES warehouse.dim_client (client_id) ON DELETE CASCADE,
    source_id       BIGINT REFERENCES warehouse.dim_source (source_id) ON DELETE SET NULL,
    source_row_id   TEXT NOT NULL,          -- the row's id in the source (row_id / order_id)
    date_key        INTEGER REFERENCES warehouse.dim_date (date_key),
    metric_type     TEXT NOT NULL,          -- price, cost, amount, score, quantity, ...
    metric_subtype  TEXT,                   -- unit, shipping, csat, nps, ...
    metric_unit     TEXT NOT NULL,          -- usd, count, score, percent, hours, ...
    metric_value    NUMERIC(18, 4),
    source_column   TEXT NOT NULL,          -- the client's original column name
    attributes      JSONB NOT NULL DEFAULT '{{}}',   -- the row's categories: {json.dumps(sorted(kept))[1:-1] or "..."}
    UNIQUE (client_id, source_id, source_row_id, source_column)
);"""


def run(df, metric_columns=None, id_columns=None, metric_map=None, keep_missing=False, preview_rows=200,
        full_table=False) -> SkillResult:
    """
    Reshape the table into unified metric rows, explain every choice, and verify by round trip.

    Args:
        df: pd.DataFrame, the cleaned table.
        metric_columns: list[str] | None, metric columns (default: guessed).
        id_columns: list[str] | None, id columns (default: guessed).
        metric_map: dict | None, {column: {"type", "subtype", "unit"}} to override guesses.
        keep_missing: bool, keep rows for blank metric values.
        preview_rows: int, unified rows shown in the "unified_preview" table.
        full_table: bool, command line only: add the whole unified table ("unified_full").

    Returns:
        SkillResult: tables "column_roles", "metric_definitions", "unified_preview"
        (+ "unified_full"); findings incl. columns needing review; data
        {"round_trip", "rows_in", "rows_out", "metrics"}; artifacts "metric_map"
        (JSON to edit and re-run) and "sql_proposed_table".

    Raises:
        SkillInputError: when no metric column is found, or metric_map is malformed.

    Example:
        >>> r = run(sales)
        >>> r.data["round_trip"]["ok"]
        True
    """
    if metric_map is not None and not isinstance(metric_map, dict):
        raise SkillInputError('metric_map must be a JSON object like {"amount": {"type": "amount", "unit": "usd"}}.')
    roles = column_roles(df, metric_columns, id_columns)
    metrics = [c for c, r in roles.items() if r == "metric"]
    if not metrics:
        raise SkillInputError("No metric (number) columns found. Pick them with the metric_columns option.")

    # Context the column name alone can't give: in an EXPENSES table, "amount"
    # is money going out — a cost. Detected from the other columns' names.
    other_words = {w for c, r in roles.items() if r != "metric" for w in _words(c)}
    expense_table = bool(other_words & EXPENSE_HINTS)

    meta: dict[str, dict] = {}
    for col in metrics:
        guess = classify_metric(col)
        if expense_table and guess["type"] == "amount":
            guess = {**guess, "type": "cost", "confidence": "medium",
                     "why": f"{guess['why']}; the table looks like expenses (vendor / expense columns), so it's "
                            "treated as a cost — please confirm"}
        override = (metric_map or {}).get(col)
        if override is not None:
            if not isinstance(override, dict):
                raise SkillInputError(f"metric_map['{col}'] must be an object with type / subtype / unit.")
            guess = {**guess, **{k: override[k] for k in ("type", "subtype", "unit") if k in override},
                     "confidence": "set by you", "why": "from metric_map"}
        meta[col] = guess

    long = unify(df, roles, meta, keep_missing)
    check = round_trip(df, long, metrics)
    kept = {c: r for c, r in roles.items() if r != "metric"}
    review = [c for c in metrics if meta[c]["confidence"] in ("low", "medium")]

    role_rows = [{"column": c, "role": r, "kept_as_is": r != "metric"} for c, r in roles.items()]
    def_rows = [{"source_column": c, "metric_type": m["type"], "metric_subtype": m.get("subtype"),
                 "metric_unit": m["unit"], "confidence": m["confidence"], "why": m["why"],
                 "values": int(df[c].notna().sum())} for c, m in meta.items()]

    findings = [
        f"{len(df):,} row(s) × {len(metrics)} metric column(s) → {len(long):,} unified row(s) "
        f"({len(df) * len(metrics) - len(long):,} blank value(s) {'kept' if keep_missing else 'left out'}).",
        "Kept as they are: " + (", ".join(f"{c} ({r})" for c, r in kept.items()) or "nothing") + ".",
    ]
    for c in metrics:
        m = meta[c]
        findings.append(f"{c} → type {m['type']}, subtype {m.get('subtype') or '—'}, unit {m['unit']} "
                        f"({m['confidence']}: {m['why']}).")
    if check["ok"]:
        findings.append(f"Round trip passed: all {check['cells_checked']:,} metric cells rebuilt exactly — no information lost.")
    else:
        findings.append(f"Round trip FAILED: {check['mismatches']:,} of {check['cells_checked']:,} cells differ "
                        f"(first: {check['examples'][:1]}). Check columns holding text in a number column.")
    if review:
        findings.append("Needs a person's decision: " + ", ".join(review)
                        + ". Edit the json_metric_map file and run again with metric_map.")

    tables = {"column_roles": role_rows, "metric_definitions": def_rows,
              "unified_preview": records(long, limit=max(1, int(preview_rows)))}
    if full_table:
        tables["unified_full"] = records(long, limit=None)

    edit_me = {c: {"type": m["type"], "subtype": m.get("subtype"), "unit": m["unit"]} for c, m in meta.items()}
    summary = (f"{len(metrics)} metric column(s) became {len(long):,} unified row(s) with type, subtype and unit; "
               + ("round trip passed (nothing lost)" if check["ok"] else "round trip FAILED")
               + (f"; {len(review)} column(s) need review." if review else "."))
    return SkillResult(
        skill=SKILL.id, summary=summary, findings=findings, tables=tables,
        artifacts={"json_metric_map": json.dumps(edit_me, indent=2), "sql_proposed_table": _ddl(kept)},
        data={"round_trip": check, "rows_in": len(df), "rows_out": len(long), "metrics": metrics,
              "needs_review": review},
    )
