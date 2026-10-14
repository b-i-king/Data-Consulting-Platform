"""
create-viz — make a specific chart from the data (Data Visualization).

Python implementation of README.md in this folder (Anthropic data plugin,
Apache-2.0). The original draws with matplotlib/plotly; here charts are
produced as small JSON chart specs (see data_skills/_core.py `line_chart`,
`bar_chart`) that the web app draws in the platform's colors, in light and
dark mode — so there is no image library on the server.

Chart types:
  line   — x = date column bucketed by day/week/month, y = aggregated number
  bar    — x = category, y = aggregated number (sorted, top N + "Other")
  histogram — a number column split into equal-width bins
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data_skills._core import (
    Param, SkillResult, SkillSpec, as_datetime, bar_chart, categorical_columns, date_columns, line_chart,
    numeric_columns, require_column,
)

SKILL = SkillSpec(
    id="create-viz",
    title="Charts from your data",
    description="Build a line, bar, or histogram chart spec from chosen columns.",
    params={
        "chart": Param("choice", "auto", "Chart type", ("auto", "line", "bar", "histogram")),
        "x": Param("column", None, "Date column (line) or category column (bar)"),
        "y": Param("column", None, "Number column"),
        "agg": Param("choice", "sum", "How to combine", ("sum", "mean", "count")),
        "period": Param("choice", "month", "Bucket for line charts", ("day", "week", "month")),
        "top_n": Param("int", 8, "Bars to show before folding the rest into 'Other'"),
    },
)

_RULE = {"day": "D", "week": "W-MON", "month": "MS"}


def run(df, chart="auto", x=None, y=None, agg="sum", period="month", top_n=8) -> SkillResult:
    y = require_column(df, y, "number", numeric_columns(df))
    if chart == "auto":
        chart = "line" if (x in date_columns(df) if x else date_columns(df)) else "bar" if categorical_columns(df) else "histogram"

    if chart == "line":
        x = require_column(df, x, "date", date_columns(df))
        s = df.assign(_d=as_datetime(df[x])).dropna(subset=["_d"]).set_index("_d")[y].resample(_RULE[period]).agg(agg)
        spec = line_chart(f"{agg.title()} of {y} by {period}", s.index.date.astype(str).tolist(), {y: s.tolist()}, x, y)
        summary = f"Line chart: {y} by {period}, {len(s)} points."
    elif chart == "bar":
        x = require_column(df, x, "category", categorical_columns(df, max_unique=500))
        s = df.groupby(x)[y].agg(agg).sort_values(ascending=False)
        if len(s) > top_n:
            s = pd.concat([s.head(top_n - 1), pd.Series({"Other": s.iloc[top_n - 1:].sum() if agg != "mean" else s.iloc[top_n - 1:].mean()})])
        spec = bar_chart(f"{agg.title()} of {y} by {x}", [str(i) for i in s.index], s.tolist(), y)
        summary = f"Bar chart: {y} by {x}, {len(s)} bars."
    else:
        vals = df[y].dropna().astype(float)
        counts, edges = np.histogram(vals, bins=min(20, max(5, int(np.sqrt(len(vals))))))
        spec = bar_chart(f"Distribution of {y}", [f"{edges[i]:,.1f}–{edges[i + 1]:,.1f}" for i in range(len(counts))],
                         counts.tolist(), "rows")
        summary = f"Histogram of {y} in {len(counts)} bins."
    return SkillResult(skill=SKILL.id, summary=summary, charts=[spec])
