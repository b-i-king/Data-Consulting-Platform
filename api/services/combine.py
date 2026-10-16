"""
Combine tables before loading them into the database: STACK (union) or JOIN.

WHY THIS EXISTS
---------------
A client often sends several files. Two cases need a choice before storing:

  * STACK (union) — tables with the same kind of rows that the categorizer
    did NOT already put together (for example "sales_web.csv" and
    "sales_store.csv" whose columns differ a little). Rows are appended;
    columns that only one table has are left empty for the others. A
    `source_table` column says which table each row came from.
  * JOIN — tables that describe different things linked by a shared ID (for
    example orders.customer_id <-> customers.customer_id). Each row of the
    MAIN table gets the matching columns of the other table (a LEFT join keeps
    every main row; an INNER join keeps only rows with a match).

Files with the same columns are already stacked automatically by the
categorizer (api/services/categorizer.py combine_group); this module is for
the cases a person has to decide. The result goes through the same load
preview (api/services/warehouse_loader.py build_plan) as any single table.

THE SPEC (sent by the website as JSON)
--------------------------------------
    {"mode": "union", "groups": [1, 2]}
    {"mode": "join", "groups": [1], "left_on": "customer_id", "right_on": "customer_id", "how": "left"}

Everything here is plain pandas: no data leaves memory.
"""

from __future__ import annotations

import re

import pandas as pd

# A join may not blow the table up: more than this many times the main
# table's rows means the key repeats on both sides (many-to-many) — refused.
MAX_JOIN_GROWTH = 5


def _suffix(label: str) -> str:
    """A short, safe column suffix from a table label: "Customers (2 files)" -> "_customers_2_files"."""
    return "_" + (re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or "other")


def combine(main: pd.DataFrame, main_label: str, others: list[tuple[str, pd.DataFrame]], spec: dict) -> tuple[pd.DataFrame, str]:
    """
    Stack or join `main` with other cleaned tables.

    Args:
        main: pd.DataFrame, the table being loaded (cleaned).
        main_label: str, its name (e.g. "sales.csv").
        others: list of (label, cleaned DataFrame), the tables named in spec["groups"].
        spec: dict, {"mode": "union" | "join", "left_on", "right_on", "how"} (see module notes).

    Returns:
        tuple[pd.DataFrame, str]: the combined table and its source name
        ("sales.csv + customers.csv").

    Raises:
        ValueError: unknown mode, a join with not exactly one other table, a
            key column that doesn't exist, or a join that multiplies rows
            (many-to-many keys).

    Example:
        >>> df, name = combine(orders, "orders.csv", [("customers.csv", customers)],
        ...                    {"mode": "join", "left_on": "customer_id", "right_on": "customer_id"})
        >>> name
        'orders.csv + customers.csv'
    """
    mode = spec.get("mode")
    if not others:
        return main, main_label
    name = " + ".join([main_label, *(label for label, _ in others)])

    if mode == "union":
        parts = [main.assign(source_table=main_label)]
        parts += [df.assign(source_table=label) for label, df in others]
        # Columns that only some tables have are left empty (NaN) for the rest.
        return pd.concat(parts, ignore_index=True, sort=False), name

    if mode == "join":
        if len(others) != 1:
            raise ValueError("Join one table at a time.")
        label, other = others[0]
        left_on, right_on = spec.get("left_on"), spec.get("right_on") or spec.get("left_on")
        how = spec.get("how", "left")
        if how not in ("left", "inner"):
            raise ValueError("A join is 'left' (keep every row) or 'inner' (only rows with a match).")
        if left_on not in main.columns:
            raise ValueError(f"'{left_on}' isn't a column of {main_label}.")
        if right_on not in other.columns:
            raise ValueError(f"'{right_on}' isn't a column of {label}.")
        # Compare keys as text, so 123 and "123" match.
        left = main.assign(_key=main[left_on].astype("string").str.strip())
        right = other.assign(_key=other[right_on].astype("string").str.strip())
        if right_on == left_on:
            right = right.drop(columns=[right_on])
        joined = left.merge(right, on="_key", how=how, suffixes=("", _suffix(label))).drop(columns=["_key"])
        if len(joined) > MAX_JOIN_GROWTH * max(len(main), 1):
            raise ValueError(f"Joining on {left_on} repeats rows too much ({len(main):,} -> {len(joined):,}): the key "
                             f"isn't unique in {label}. Pick a key that identifies one row there.")
        return joined.reset_index(drop=True), name

    raise ValueError("Combine mode must be 'union' (stack) or 'join'.")
