"""
Shared building blocks for every skill's Python implementation.

WHAT THIS FILE DOES
-------------------
Each skill folder has two files:

    data_skills/<skill-name>/README.md   the written procedure (for people; GitHub shows it)
    data_skills/<skill-name>/skill.py   the same procedure as runnable pandas code

Every skill.py follows ONE pattern so the API, the command line and the web
app can run any skill the same way:

    SKILL = SkillSpec(...)                    # name, what it needs, its options
    def run(data, **params) -> SkillResult    # do the work, return results

This file defines those two shapes plus small helpers that many skills share
(finding the date column, the number columns, making results JSON-safe...).

THE RESULT SHAPE (SkillResult)
------------------------------
    summary    one or two plain-English sentences, the headline
    findings   bullet points a client can read
    tables     named tables (lists of row dicts) — shown as tables / saved as CSV
    charts     chart specs the web app draws (see `line_chart` / `bar_chart`)
    artifacts  text outputs: Markdown reports, SQL, Mermaid diagrams, HTML
    data       anything else structured (numbers other code may want)
"""

from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any, Literal

import numpy as np
import pandas as pd

# ── Describing a skill ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class Param:
    """One option a skill accepts (shown as a form field in the web app)."""

    type: Literal["str", "int", "float", "bool", "column", "columns", "choice", "json"]
    default: Any = None
    help: str = ""
    choices: tuple[str, ...] = ()
    # False = only allowed from the command line (e.g. writing files on disk).
    api_allowed: bool = True


@dataclass(frozen=True)
class SkillSpec:
    id: str                                   # folder name, e.g. "trend-analysis"
    title: str                                # human name, e.g. "Trend analysis"
    input: Literal["table", "tables"] = "table"   # one DataFrame, or {name: DataFrame}
    params: dict[str, Param] = field(default_factory=dict)
    description: str = ""
    # True = run on the data exactly as uploaded, BEFORE automatic cleaning
    # (skills that inspect the mess itself: cleaning, type and "too clean" checks).
    wants_raw: bool = False


# ── The result ───────────────────────────────────────────────────────────────


@dataclass
class SkillResult:
    skill: str
    summary: str
    findings: list[str] = field(default_factory=list)
    tables: dict[str, list[dict]] = field(default_factory=dict)
    charts: list[dict] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """
        Return the whole result as plain JSON types, ready to send from the API or save.

        Converts the dataclass to a dict with `dataclasses.asdict`, then runs it through
        `json_safe`, so NaN/inf become None, numpy numbers become Python numbers and
        dates become ISO strings.

        Returns:
            dict: keys skill, summary, findings, tables, charts, artifacts and data, holding
            only JSON-friendly values.

        Example:
            >>> SkillResult(skill="demo", summary="Hi", data={"x": float("nan")}).to_dict()["data"]
            {'x': None}
        """
        return json_safe(asdict(self))


class SkillInputError(ValueError):
    """The data or options don't fit this skill. The message is shown to the user."""


# ── JSON safety ──────────────────────────────────────────────────────────────


def json_safe(value: Any) -> Any:
    """
    Recursively convert numpy/pandas values into JSON-friendly Python values.

    Rules, applied in order:
    - dict -> dict with string keys (values converted recursively)
    - list / tuple / set -> list (items converted recursively)
    - pandas Timestamp, datetime, date -> ISO string ("2025-10-13" or "2025-10-13T00:00:00"); NaT -> None
    - numpy scalars (np.int64, np.float64, np.bool_...) -> the matching Python value
    - float NaN or +/-inf -> None; other floats are rounded to 6 decimals
    - pd.NaT / pd.NA -> None
    - anything else is returned unchanged

    Args:
        value: any value, typically a dict/list built from DataFrame contents.

    Returns:
        Any: the same structure made of str, int, float, bool, None, list and dict only.

    Example:
        >>> json_safe({"n": np.int64(3), "x": float("nan"), "d": date(2025, 1, 2)})
        {'n': 3, 'x': None, 'd': '2025-01-02'}
    """
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if isinstance(value, (pd.Timestamp, datetime, date)):
        return None if pd.isna(value) else value.isoformat()
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float):
        return None if (math.isnan(value) or math.isinf(value)) else round(value, 6)
    if value is pd.NaT or value is pd.NA:
        return None
    return value


def records(df: pd.DataFrame, limit: int | None = 500) -> list[dict]:
    """
    Turn a DataFrame into a list of JSON-safe row dicts (first `limit` rows).

    The index is dropped (reset), so only the columns appear in each row. This is
    the shape `SkillResult.tables` expects.

    Args:
        df: the table to convert.
        limit: maximum number of rows to keep (default 500); None keeps every row.

    Returns:
        list[dict]: one dict per row, {column name: JSON-safe value}.

    Example:
        >>> records(pd.DataFrame({"store": ["Midtown"], "amount": [np.nan]}))
        [{'store': 'Midtown', 'amount': None}]
    """
    view = df if limit is None else df.head(limit)
    return json_safe(view.reset_index(drop=True).to_dict(orient="records"))


# ── Column helpers ───────────────────────────────────────────────────────────

_DATE_NAME = re.compile(r"date|time|created|updated|day|month|period|_at$|timestamp", re.I)
_ID_NAME = re.compile(r"(^id$|_id$|^id_|number$|_no$|_num$|code$|zip|postal|phone|sku)", re.I)


def numeric_columns(df: pd.DataFrame, exclude_ids: bool = True) -> list[str]:
    """
    List the number columns that are real measures, skipping ID-like ones.

    An order number is not a measure, so with `exclude_ids=True` a column whose name
    looks like an identifier (id, order_id, *_number, *_no, *_num, *code, zip, postal,
    phone, sku) is skipped even if it holds numbers. Boolean columns are never counted.

    Args:
        df: the table to inspect.
        exclude_ids: True (default) to skip ID-like column names.

    Returns:
        list[str]: names of numeric columns, in the table's column order.

    Example:
        >>> numeric_columns(pd.DataFrame({"order_id": [1, 2], "amount": [5.0, 7.5], "store": ["A", "B"]}))
        ['amount']
    """
    cols = []
    for col in df.columns:
        if pd.api.types.is_bool_dtype(df[col]) or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        if exclude_ids and _ID_NAME.search(str(col)):
            continue
        cols.append(str(col))
    return cols


def categorical_columns(df: pd.DataFrame, max_unique: int = 50) -> list[str]:
    """
    List text columns with a manageable number of distinct values (good for grouping).

    A column qualifies when it is not numeric and not a datetime, has more than one
    distinct value, at most `max_unique` distinct values, and fewer distinct values
    than half its row count (so free text and unique IDs are left out).

    Args:
        df: the table to inspect.
        max_unique: largest number of distinct values allowed (default 50).

    Returns:
        list[str]: names of grouping-friendly columns, in the table's column order.

    Example:
        >>> df = pd.DataFrame({"store": ["Midtown", "Decatur"] * 3, "amount": range(6)})
        >>> categorical_columns(df)
        ['store']
    """
    out = []
    for col in df.columns:
        s = df[col]
        if pd.api.types.is_numeric_dtype(s) or pd.api.types.is_datetime64_any_dtype(s):
            continue
        n = s.nunique(dropna=True)
        if 1 < n <= max_unique and n < max(len(s) * 0.5, 2):
            out.append(str(col))
    return out


def as_datetime(series: pd.Series) -> pd.Series:
    """
    Parse a column as dates; values that can't be parsed become NaT.

    A column that already has a datetime dtype is returned unchanged. Otherwise the
    values are read as text and parsed with `format="mixed"`, so each value may use
    its own format (2025-10-13, 10/13/2025, ...).

    Args:
        series: the column to parse.

    Returns:
        pd.Series: a datetime64 series with NaT where a value is not a date.

    Example:
        >>> as_datetime(pd.Series(["2025-10-13", "not a date"])).tolist()
        [Timestamp('2025-10-13 00:00:00'), NaT]
    """
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    return pd.to_datetime(series.astype("string"), errors="coerce", format="mixed")


def date_columns(df: pd.DataFrame) -> list[str]:
    """
    Find the columns that hold dates: real datetimes, or text where 80% or more parses as a date.

    Rules per column (empty columns are skipped):
    - an actual datetime dtype always counts
    - a numeric column counts only if its NAME looks like a date (date, time, created,
      updated, day, month, period, *_at, timestamp) and its values parse
    - other non-text dtypes are skipped unless the name looks like a date
    - text columns: up to 500 non-empty values are parsed; at least 80% must succeed

    Args:
        df: the table to inspect.

    Returns:
        list[str]: names of date columns, in the table's column order.

    Example:
        >>> date_columns(pd.DataFrame({"order_date": ["2025-01-02", "2025-01-03"], "store": ["A", "B"]}))
        ['order_date']
    """
    found = []
    for col in df.columns:
        s = df[col].dropna()
        if s.empty:
            continue
        if pd.api.types.is_datetime64_any_dtype(s):
            found.append(str(col))
            continue
        if pd.api.types.is_numeric_dtype(s) and not _DATE_NAME.search(str(col)):
            continue
        if not _DATE_NAME.search(str(col)) and s.dtype != object:
            continue
        sample = s.astype("string").head(500)
        if as_datetime(sample).notna().mean() >= 0.8:
            found.append(str(col))
    return found


def require_column(df: pd.DataFrame, name: str | None, kind: str, candidates: list[str]) -> str:
    """
    Return the column to use: the one the user named, or the first auto-detected candidate.

    Skills call this for options like "metric" or "date_column": if the user gave a
    name it must exist; if not, the first of `candidates` (from helpers such as
    `numeric_columns`) is used.

    Args:
        df: the table the column must belong to.
        name: the column the user asked for, or None/"" to auto-detect.
        kind: a word for the error message, e.g. "number", "date", "category".
        candidates: auto-detected columns, best first.

    Returns:
        str: the chosen column name.

    Raises:
        SkillInputError: the named column is not in the table (the message lists the
            columns), or no name was given and `candidates` is empty.

    Example:
        >>> df = pd.DataFrame({"amount": [1.0]})
        >>> require_column(df, None, "number", numeric_columns(df))
        'amount'
    """
    if name:
        if name not in df.columns:
            raise SkillInputError(f"Column '{name}' was not found. Columns: {', '.join(map(str, df.columns))}")
        return name
    if not candidates:
        raise SkillInputError(f"This skill needs a {kind} column, and none was found in the data.")
    return candidates[0]


def fmt_num(x: float | int | None, decimals: int = 2) -> str:
    """
    Format a number the US way with thousands separators; None or NaN -> 'n/a'.

    Whole numbers drop their decimals (1234.0 -> '1,234') unless `decimals` is 0,
    in which case the value is simply rounded to a whole number. Other values keep
    `decimals` decimal places.

    Args:
        x: the number to format (int, float, or None).
        decimals: decimal places for non-whole numbers (default 2).

    Returns:
        str: the formatted number, e.g. '1,234.50', or 'n/a'.

    Example:
        >>> fmt_num(1234.5)
        '1,234.50'
        >>> fmt_num(1234.0)
        '1,234'
        >>> fmt_num(None)
        'n/a'
    """
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if float(x).is_integer() and decimals:
        return f"{int(x):,}"
    return f"{x:,.{decimals}f}"


def fmt_pct(x: float | None, decimals: int = 1) -> str:
    """
    Format a fraction as a signed percentage: 0.123 -> '+12.3%'.

    The sign is always shown so a change reads clearly as up or down. None, NaN and
    infinity (e.g. a change from zero) become 'n/a'.

    Args:
        x: the fraction to format (0.123 means 12.3%), or None.
        decimals: decimal places in the percentage (default 1).

    Returns:
        str: e.g. '+12.3%', '-4.0%', or 'n/a'.

    Example:
        >>> fmt_pct(0.123)
        '+12.3%'
        >>> fmt_pct(-0.04)
        '-4.0%'
    """
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x * 100:+.{decimals}f}%"


# ── Chart specs (drawn by the web app; also readable as plain data) ─────────


def line_chart(title: str, x: list, series: dict[str, list], x_label: str = "", y_label: str = "") -> dict:
    """
    Build a line chart spec: one x axis (usually dates) and one or more named series.

    The web app draws this spec in the platform's colors; it is also plain data, so
    it can be saved or inspected as JSON.

    Args:
        title: chart title.
        x: x-axis values (e.g. ISO date strings), one per point.
        series: {series name: list of y values}, each list the same length as `x`.
        x_label: optional x-axis label.
        y_label: optional y-axis label.

    Returns:
        dict: {"type": "line", "title", "x", "series": [{"name", "values"}, ...],
        "x_label", "y_label"}, JSON-safe (NaN -> None).

    Example:
        >>> line_chart("Sales", ["2025-01-01", "2025-02-01"], {"amount": [10, 12]})["series"]
        [{'name': 'amount', 'values': [10, 12]}]
    """
    return json_safe({"type": "line", "title": title, "x": x, "series": [
        {"name": k, "values": v} for k, v in series.items()
    ], "x_label": x_label, "y_label": y_label})


def bar_chart(title: str, categories: list, values: list, value_label: str = "") -> dict:
    """
    Build a horizontal bar chart spec: one value per category.

    Args:
        title: chart title.
        categories: bar labels, top to bottom.
        values: one number per category (same length as `categories`).
        value_label: optional label for the value axis (e.g. "amount", "rows").

    Returns:
        dict: {"type": "bar", "title", "categories", "values", "value_label"},
        JSON-safe (NaN -> None).

    Example:
        >>> bar_chart("Sales by store", ["Midtown", "Decatur"], [120, 75], "amount")["type"]
        'bar'
    """
    return json_safe({"type": "bar", "title": title, "categories": categories,
                      "values": values, "value_label": value_label})
