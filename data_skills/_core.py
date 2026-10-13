"""
Shared building blocks for every skill's Python implementation.

WHAT THIS FILE DOES
-------------------
Each skill folder has two files:

    data_skills/<skill-name>/SKILL.md   the written procedure (for people and Claude)
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
        """Everything as plain JSON types (NaN -> None, numpy -> Python)."""
        return json_safe(asdict(self))


class SkillInputError(ValueError):
    """The data or options don't fit this skill. The message is shown to the user."""


# ── JSON safety ──────────────────────────────────────────────────────────────


def json_safe(value: Any) -> Any:
    """Recursively convert numpy/pandas values into JSON-friendly Python values."""
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
    """DataFrame -> list of JSON-safe row dicts (first `limit` rows)."""
    view = df if limit is None else df.head(limit)
    return json_safe(view.reset_index(drop=True).to_dict(orient="records"))


# ── Column helpers ───────────────────────────────────────────────────────────

_DATE_NAME = re.compile(r"date|time|created|updated|day|month|period|_at$|timestamp", re.I)
_ID_NAME = re.compile(r"(^id$|_id$|^id_|number$|_no$|_num$|code$|zip|postal|phone|sku)", re.I)


def numeric_columns(df: pd.DataFrame, exclude_ids: bool = True) -> list[str]:
    """Number columns, skipping ID-like ones (an order number is not a measure)."""
    cols = []
    for col in df.columns:
        if pd.api.types.is_bool_dtype(df[col]) or not pd.api.types.is_numeric_dtype(df[col]):
            continue
        if exclude_ids and _ID_NAME.search(str(col)):
            continue
        cols.append(str(col))
    return cols


def categorical_columns(df: pd.DataFrame, max_unique: int = 50) -> list[str]:
    """Text columns with a manageable number of distinct values (good for grouping)."""
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
    """Parse a column as dates (unparseable values become NaT)."""
    if pd.api.types.is_datetime64_any_dtype(series):
        return series
    return pd.to_datetime(series.astype("string"), errors="coerce", format="mixed")


def date_columns(df: pd.DataFrame) -> list[str]:
    """Columns that hold dates: real datetimes, or text where >=80% parses as a date."""
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
    Return the column to use: the one the user named, or the first candidate.
    Raises a friendly error if neither works.
    """
    if name:
        if name not in df.columns:
            raise SkillInputError(f"Column '{name}' was not found. Columns: {', '.join(map(str, df.columns))}")
        return name
    if not candidates:
        raise SkillInputError(f"This skill needs a {kind} column, and none was found in the data.")
    return candidates[0]


def fmt_num(x: float | int | None, decimals: int = 2) -> str:
    """1234.5 -> '1,234.50' (US formatting); None -> 'n/a'."""
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return "n/a"
    if float(x).is_integer() and decimals:
        return f"{int(x):,}"
    return f"{x:,.{decimals}f}"


def fmt_pct(x: float | None, decimals: int = 1) -> str:
    """0.123 -> '+12.3%'."""
    if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
        return "n/a"
    return f"{x * 100:+.{decimals}f}%"


# ── Chart specs (drawn by the web app; also readable as plain data) ─────────


def line_chart(title: str, x: list, series: dict[str, list], x_label: str = "", y_label: str = "") -> dict:
    """A line chart: one x axis (usually dates), one or more named series."""
    return json_safe({"type": "line", "title": title, "x": x, "series": [
        {"name": k, "values": v} for k, v in series.items()
    ], "x_label": x_label, "y_label": y_label})


def bar_chart(title: str, categories: list, values: list, value_label: str = "") -> dict:
    """A horizontal bar chart: one value per category."""
    return json_safe({"type": "bar", "title": title, "categories": categories,
                      "values": values, "value_label": value_label})
