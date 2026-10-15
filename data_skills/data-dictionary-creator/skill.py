"""
data-dictionary-creator — document every column (Data Engineering).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). For each column: name, storage type, MEANING guess
(identifier, email, phone, currency amount, quantity, percentage, date,
category, free text, flag), nullability, distinct count, allowed values (for
small categories), range (for numbers/dates), and an example.

Pass `descriptions` (JSON: {"column": "what it means"}) to merge in what the
client tells you — automatic profiling + human meaning = a real dictionary.
Output: a table plus a Markdown dictionary ready for a README or Notion.
"""

from __future__ import annotations

import re

import pandas as pd

from data_skills._core import Param, SkillResult, SkillSpec, as_datetime, date_columns, fmt_num

SKILL = SkillSpec(
    id="data-dictionary-creator",
    title="Data dictionaries",
    description="Column-by-column documentation: type, meaning, nullability, allowed values, ranges, examples.",
    params={
        "table_name": Param("str", "table", "Name of the table"),
        "descriptions": Param("json", None, 'Your own column descriptions, e.g. {"amount": "Order total in USD"}'),
    },
)


def meaning(col: str, s: pd.Series, is_date: bool) -> str:
    """
    Guess what a column means from its name and values.

    Rules, first match wins (the name is lowercased; spaces, hyphens and dots become "_"):
    date (if `is_date`) -> identifier (id, *_id, *number, *_no, sku, *code) -> email ->
    phone (phone, mobile) -> currency amount (USD) (amount, price, cost, revenue... AND
    numeric) -> quantity (qty, quantity, count, units) -> percentage / rate (pct,
    percent, rate) -> flag (yes/no) (bool, or only yes/no/true/false/y/n values) ->
    measure (other numbers) -> category (50 or fewer distinct values) -> free text.

    Args:
        col: the column name.
        s: the column's values.
        is_date: True if the column was detected as a date column.

    Returns:
        str: one of the meanings listed above.

    Example:
        >>> meaning("order_id", pd.Series([1, 2]), False)
        'identifier'
        >>> meaning("amount", pd.Series([4.5, 12.0]), False)
        'currency amount (USD)'
    """
    n = re.sub(r"[\s\-.]+", "_", col.lower())
    if is_date:
        return "date"
    if re.search(r"(^id$|_id$|^id_|number$|_no$|sku|code$)", n):
        return "identifier"
    if "email" in n:
        return "email"
    if re.search(r"phone|mobile", n):
        return "phone"
    if re.search(r"amount|price|cost|revenue|total|balance|pay|salary|fee", n) and pd.api.types.is_numeric_dtype(s):
        return "currency amount (USD)"
    if re.search(r"qty|quantity|count|units", n):
        return "quantity"
    if re.search(r"pct|percent|rate", n):
        return "percentage / rate"
    if pd.api.types.is_bool_dtype(s) or s.dropna().astype(str).str.lower().isin(["yes", "no", "true", "false", "y", "n"]).all():
        return "flag (yes/no)"
    if pd.api.types.is_numeric_dtype(s):
        return "measure"
    return "category" if s.nunique() <= 50 else "free text"


def run(df: pd.DataFrame, table_name="table", descriptions=None) -> SkillResult:
    """
    Document every column: type, meaning, nullability, allowed values, range and an example.

    For each column: storage dtype, `meaning()` guess, the client's description (if
    given), whether it has gaps and the % missing, distinct count, allowed values
    (categories and flags with 12 or fewer values), range (min -> max for numbers
    and dates) and the first non-empty value as an example.

    Args:
        df: the table to document.
        table_name: name used in the title (default "table").
        descriptions: your own column descriptions, e.g. {"amount": "Order total in USD"}.

    Returns:
        SkillResult: summary; a finding listing columns that still need a description;
        table "dictionary" (one row per column); artifacts["markdown_dictionary"], a
        Markdown table ready for a README or Notion.

    Example:
        >>> run(pd.read_csv("samples/sales.csv"), table_name="sales").summary
        'Dictionary for 13 columns of sales.'
    """
    descriptions = descriptions or {}
    dates = set(date_columns(df))
    rows = []
    for col in df.columns:
        s, c = df[col], str(col)
        m = meaning(c, s, c in dates)
        nonnull = s.dropna()
        if c in dates:
            d = as_datetime(s).dropna()
            rng = f"{d.min().date()} → {d.max().date()}" if len(d) else ""
        elif pd.api.types.is_numeric_dtype(s) and len(nonnull):
            rng = f"{fmt_num(nonnull.min())} → {fmt_num(nonnull.max())}"
        else:
            rng = ""
        allowed = ", ".join(map(str, sorted(nonnull.astype(str).unique()))) if m in ("category", "flag (yes/no)") and nonnull.nunique() <= 12 else ""
        rows.append({"column": c, "type": str(s.dtype), "meaning": m,
                     "description": descriptions.get(c, ""), "nullable": bool(s.isna().any()),
                     "missing_pct": round(float(s.isna().mean() * 100), 2), "distinct": int(s.nunique()),
                     "allowed_values": allowed, "range": rng, "example": str(nonnull.iloc[0]) if len(nonnull) else ""})

    md = [f"# Data dictionary — {table_name}", "", f"{len(df):,} rows · {df.shape[1]} columns", "",
          "| Column | Type | Meaning | Description | Nullable | Allowed values / range | Example |",
          "|---|---|---|---|---|---|---|"]
    for r in rows:
        md.append(f"| `{r['column']}` | {r['type']} | {r['meaning']} | {r['description'] or '_add_'} | "
                  f"{'yes' if r['nullable'] else 'no'} ({r['missing_pct']}%) | {r['allowed_values'] or r['range']} | {r['example']} |")
    todo = [r["column"] for r in rows if not r["description"]]
    return SkillResult(skill=SKILL.id, summary=f"Dictionary for {df.shape[1]} columns of {table_name}.",
                       findings=([f"Ask the client to describe: {', '.join(todo)}."] if todo else ["Every column has a description."]),
                       tables={"dictionary": rows}, artifacts={"markdown_dictionary": "\n".join(md) + "\n"})
