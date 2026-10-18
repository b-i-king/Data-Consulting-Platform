"""
Industry KPIs for build-dashboard: the metrics that matter for the client's industry, when the data has them.

WHAT THIS FILE DOES
-------------------
industry_metrics.json (human-readable copy: INDUSTRY_METRICS.md) lists, per
NAICS sector, the KPIs worth showing first — prime cost % for a restaurant,
billable utilization for a law firm, days in A/R for a clinic — each as a
RECIPE: which columns it needs (name hints) and how to combine them.

  plan(data, sector)  -> the metrics this table can actually compute, each as
                         a spec the dashboard works with like any other metric:
                           {"id", "label", "direction", "format", "benchmark",
                            "agg": "sum" | "mean" | "ratio", "column" (sum/mean),
                            "num" / "den" / "den_agg" / "scale" (ratio)}
                         Helper columns are added to `data` (prefixed __ind_).

A metric whose columns aren't in the table is skipped quietly; with none at
all, the dashboard shows the generic KPIs (total, transactions, average per
transaction). So a new industry needs no code: add its recipes to the JSON.

MATCHING COLUMNS
----------------
A hint matches a column whose name contains all the hint's words, in any
case and spacing: "food_cost" matches "Food Cost (USD)" and "food_cost_total".
Hints are tried in order (most specific first). Number roles only take number
columns; one column is never used twice in the same metric.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

import pandas as pd

ROWS = "__rows__"
TRUTHY = {"true", "1", "yes", "y", "t", "x"}


@lru_cache(maxsize=1)
def catalog() -> dict:
    """The recipes from industry_metrics.json, by NAICS sector ("72", "44-45", ...)."""
    raw = json.loads(Path(__file__).with_name("industry_metrics.json").read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def sector_name(sector: str | None) -> str | None:
    """The sector's name, e.g. "72" -> "Accommodation and Food Services" (None if unknown)."""
    return catalog().get(str(sector or ""), {}).get("name")


def _words(name: str) -> list[str]:
    """A column or hint name as lowercase words: "Food Cost (USD)" -> ["food", "cost", "usd"]."""
    return [w for w in re.split(r"[^a-z0-9]+", str(name).lower()) if w]


def find(df: pd.DataFrame, hints: list[str], *, numeric: bool, used: set[str]) -> str | None:
    """
    The first column matching a hint (hints in order; the closest name wins among equals).

    Args:
        df: the table.
        hints: e.g. ["food_cost", "cogs"].
        numeric: only number columns count.
        used: columns already taken by this metric (never reused).

    Returns:
        str | None: the column name.

    Example:
        >>> find(pd.DataFrame({"Food Cost": [1.0]}), ["food_cost"], numeric=True, used=set())
        'Food Cost'
    """
    cols = [c for c in df.columns if str(c) not in used and not str(c).startswith("__")]
    if numeric:
        cols = [c for c in cols if pd.api.types.is_numeric_dtype(df[c])]
    for hint in hints:
        want = _words(hint)
        hits = [c for c in cols if all(w in _words(c) for w in want)]
        if hits:
            return str(min(hits, key=lambda c: len(_words(c))))   # "sales" over "sales_tax_refund"
    return None


def _sum_groups(df, groups, used) -> pd.Series | None:
    """Add one column per group (each group = alternative hints); None when a group finds nothing."""
    total = None
    for hints in groups:
        if hints == [ROWS]:
            col_values = pd.Series(1.0, index=df.index)
        else:
            col = find(df, hints, numeric=True, used=used)
            if col is None:
                return None
            used.add(col)
            col_values = df[col].astype(float)
        total = col_values.fillna(0) if total is None else total + col_values.fillna(0)
    return total


def _truthy(series: pd.Series, values: list[str] | None) -> pd.Series:
    """1.0 where the row counts (in `values`, or a yes/true/1 flag), 0.0 elsewhere, NaN where blank."""
    text = series.astype("string").str.strip().str.lower()
    hit = text.isin([v.lower() for v in values]) if values else text.isin(TRUTHY)
    return hit.astype(float).where(series.notna())


def plan(data: pd.DataFrame, sector: str | None) -> list[dict]:
    """
    The sector's KPIs this table can compute (helper columns added to `data`).

    Args:
        data: the table (changed: __ind_<id>__ helper columns are added).
        sector: NAICS sector code from sign-up ("72", "44-45", ...); None = no industry.

    Returns:
        list[dict]: metric specs, in the JSON's order (see the module notes).

    Example:
        >>> df = pd.DataFrame({"date": ["2026-01-01"], "sales": [100.0], "food_cost": [30.0], "labor_cost": [32.0]})
        >>> [m["id"] for m in plan(df, "72")]
        ['prime_cost_pct']
    """
    out: list[dict] = []
    for spec in catalog().get(str(sector or ""), {}).get("metrics", []):
        kind = spec.get("kind")
        if not kind:
            continue
        mid = f"__ind_{spec['id']}__"
        used: set[str] = set()
        m = {"id": mid, "key": spec["id"], "label": spec["label"], "direction": spec.get("direction", "higher"),
             "format": spec.get("format", "number"), "benchmark": spec.get("benchmark"),
             "target": spec.get("target")}   # [low, high] healthy band (the gauge card marks it)
        if kind in ("sum", "mean"):
            col = find(data, spec["column"], numeric=True, used=used)
            if col is None:
                continue
            data[mid] = data[col].astype(float)
            m.update(agg=kind, column=mid, sources=[col])
        elif kind == "rate":
            col = find(data, spec["column"], numeric=False, used=used)
            if col is None:
                continue
            flags = _truthy(data[col], spec.get("values"))
            if not flags.fillna(0).any():
                continue   # nothing ever counts: probably not the column we meant
            data[mid] = flags
            m.update(agg="mean", column=mid, sources=[col], format="percent", scale=100)
        elif kind == "days_between":
            start = find(data, spec["start"], numeric=False, used=used)
            if start:
                used.add(start)
            end = find(data, spec["end"], numeric=False, used=used)
            if not start or not end:
                continue
            days = (pd.to_datetime(data[end], errors="coerce") - pd.to_datetime(data[start], errors="coerce")).dt.days
            days = days.where(days >= 0)
            if days.notna().sum() == 0:
                continue
            data[mid] = days.astype(float)
            m.update(agg="mean", column=mid, sources=[start, end])
        elif kind in ("ratio", "net"):
            num = _sum_groups(data, spec.get("numerator", []), used)
            if num is None:
                continue
            if spec.get("minus"):
                minus = _sum_groups(data, spec["minus"], used)
                if minus is None:
                    continue
                num = num - minus
            if kind == "net":
                data[mid] = num
                m.update(agg="sum", column=mid, sources=sorted(used))
            else:
                den = _sum_groups(data, spec.get("denominator", []), used)
                if den is None or not den.abs().sum():
                    continue
                data[mid + "num"], data[mid + "den"] = num, den
                scale = 100 if m["format"] == "percent" else float(spec.get("scale", 1))
                m.update(agg="ratio", num=mid + "num", den=mid + "den", den_agg="sum", scale=scale, sources=sorted(used))
        elif kind == "per_distinct":
            key = find(data, spec["distinct"], numeric=False, used=used)
            if key is None:
                continue
            used.add(key)
            groups = spec.get("numerator", [])
            num = _sum_groups(data, groups, used) if groups else pd.Series(1.0, index=data.index)
            if num is None:
                continue
            data[mid + "num"], data[mid + "den"] = num, data[key]
            m.update(agg="ratio" if groups else "distinct", num=mid + "num", den=mid + "den", den_agg="nunique",
                     scale=1, sources=sorted(used))
        else:
            continue
        if m.get("format") == "percent" and m["agg"] == "mean" and "scale" not in m:
            m["scale"] = 100 if data[m["column"]].abs().max() <= 1 else 1   # 0.82 -> 82%; 82 stays 82
        out.append(m)
    return out
