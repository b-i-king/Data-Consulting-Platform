"""
analyze — answer a business question with a breakdown (Data Exploration & Analysis).

Python implementation of SKILL.md in this folder (Anthropic data plugin,
Apache-2.0). The SKILL.md describes the full analyst workflow; this code runs
its core move: "what is <metric> by <dimension>, what drives it, and how has
it changed?"

  1. Total and breakdown of the metric by the dimension (share of total)
  2. Concentration — how many categories make up 80% of the total (Pareto)
  3. Change — if there's a date column, each category's last-period vs.
     previous-period value, and which categories drove the overall change
"""

from __future__ import annotations

import pandas as pd

from data_skills._core import (
    Param, SkillResult, SkillSpec, as_datetime, bar_chart, categorical_columns, date_columns, fmt_num, fmt_pct,
    numeric_columns, require_column,
)

SKILL = SkillSpec(
    id="analyze",
    title="Answers to business questions",
    description="Metric by dimension: breakdown, concentration (80/20), and what drove the latest change.",
    params={
        "metric": Param("column", None, "Number to analyze (e.g. amount)"),
        "dimension": Param("column", None, "Category to break it down by (e.g. product)"),
        "agg": Param("choice", "sum", "How to combine", ("sum", "mean", "count")),
        "date_column": Param("column", None, "Date column for the change analysis"),
        "period": Param("choice", "month", "Period for the change analysis", ("week", "month", "quarter", "year")),
    },
)

_FREQ = {"week": "W-MON", "month": "MS", "quarter": "QS", "year": "YS"}


def run(df, metric=None, dimension=None, agg="sum", date_column=None, period="month") -> SkillResult:
    metric = require_column(df, metric, "number", numeric_columns(df))
    dimension = require_column(df, dimension, "category", categorical_columns(df))
    by = df.groupby(dimension, dropna=False)[metric].agg(agg).sort_values(ascending=False)
    total = df[metric].agg(agg)
    share = by / by.sum() if agg in ("sum", "count") and by.sum() else by * 0
    breakdown = [{"category": str(k), "value": float(v), "share_pct": round(float(share[k]) * 100, 2)} for k, v in by.items()]
    findings = [f"Top {dimension}: {by.index[0]} with {fmt_num(by.iloc[0])}"
                + (f" ({share.iloc[0]:.0%} of the total)." if agg != "mean" else ".")]
    if agg in ("sum", "count") and len(by) > 2:
        k80 = int((share.cumsum() < 0.8).sum()) + 1
        findings.append(f"{k80} of {len(by)} {dimension} values make up 80% of the total.")

    tables = {"breakdown": breakdown}
    dcol = date_column or next(iter(date_columns(df)), None)
    if dcol:
        d = df.assign(_p=as_datetime(df[dcol]).dt.to_period(_FREQ[period][0] if period != "week" else "W").dt.start_time)
        pivot = d.pivot_table(index="_p", columns=dimension, values=metric, aggfunc=agg).sort_index()
        if len(pivot) >= 3:
            last, prev = pivot.iloc[-2], pivot.iloc[-3]   # skip the newest period (likely partial)
            delta = (last - prev).sort_values()
            overall = last.sum() - prev.sum()
            tables["change_drivers"] = [{"category": str(k), "previous": float(prev[k]), "latest": float(last[k]),
                                         "change": float(v)} for k, v in delta.items()]
            findings.append(f"Latest full {period} ({pivot.index[-2].date()}): total changed by {fmt_num(overall)} "
                            f"({fmt_pct(overall / prev.sum()) if prev.sum() else 'n/a'}).")
            findings.append(f"Biggest increase: {delta.index[-1]} ({fmt_num(delta.iloc[-1])}); "
                            f"biggest decrease: {delta.index[0]} ({fmt_num(delta.iloc[0])}).")

    return SkillResult(
        skill=SKILL.id,
        summary=f"{agg.title()} of {metric}: {fmt_num(total)} across {len(by)} {dimension} values.",
        findings=findings, tables=tables,
        charts=[bar_chart(f"{metric} by {dimension}", [b["category"] for b in breakdown[:15]],
                          [b["value"] for b in breakdown[:15]], metric)],
    )
