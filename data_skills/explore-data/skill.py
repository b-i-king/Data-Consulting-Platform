"""
explore-data — profile a dataset (Data Exploration & Analysis).

Python implementation of README.md in this folder (Anthropic data plugin,
Apache-2.0). For every column: type, missing %, distinct values, and either
number stats (min, quartiles, mean, max), date range, or top values. Then
suggests which columns look like dimensions (group by), metrics (add up),
dates (trend over), and identifiers (join on).
"""

from __future__ import annotations

import pandas as pd

from data_skills._core import (
    SkillResult, SkillSpec, as_datetime, bar_chart, categorical_columns, date_columns, fmt_num, numeric_columns,
)

SKILL = SkillSpec(
    id="explore-data",
    title="Dataset profiling",
    description="Column-by-column profile plus suggested dimensions, metrics, dates, and keys.",
)


def run(df: pd.DataFrame) -> SkillResult:
    n = len(df)
    dates = set(date_columns(df))
    metrics = set(numeric_columns(df))
    dims = set(categorical_columns(df))
    profile = []
    for col in df.columns:
        s = df[col]
        c = str(col)
        row = {"column": c, "dtype": str(s.dtype), "missing_pct": round(s.isna().mean() * 100, 2),
               "distinct": int(s.nunique(dropna=True))}
        if c in dates:
            d = as_datetime(s).dropna()
            row.update(role="date", summary=f"{d.min().date()} → {d.max().date()}" if len(d) else "")
        elif pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
            desc = s.describe()
            row.update(role="metric" if c in metrics else "identifier",
                       summary=f"min {fmt_num(desc['min'])} · median {fmt_num(desc['50%'])} · "
                               f"mean {fmt_num(desc['mean'])} · max {fmt_num(desc['max'])}")
        else:
            top = s.value_counts().head(3)
            role = "dimension" if c in dims else ("identifier" if row["distinct"] > 0.9 * n else "text")
            row.update(role=role, summary=", ".join(f"{k} ({v:,})" for k, v in top.items()))
        profile.append(row)

    by_role = lambda r: [p["column"] for p in profile if p["role"] == r]  # noqa: E731
    findings = [
        f"Dimensions to group by: {', '.join(by_role('dimension')) or 'none'}.",
        f"Metrics to add up: {', '.join(by_role('metric')) or 'none'}.",
        f"Dates to trend over: {', '.join(by_role('date')) or 'none'}.",
        f"Identifiers to join on: {', '.join(by_role('identifier')) or 'none'}.",
    ]
    gappy = [p for p in profile if p["missing_pct"] > 20]
    if gappy:
        findings.append("Columns more than 20% empty: " + ", ".join(f"{p['column']} ({p['missing_pct']}%)" for p in gappy))
    charts = [bar_chart("Missing values by column (%)", [p["column"] for p in profile], [p["missing_pct"] for p in profile], "%")]
    return SkillResult(skill=SKILL.id, summary=f"{fmt_num(n)} rows × {df.shape[1]} columns profiled.",
                       findings=findings, tables={"profile": profile}, charts=charts)
