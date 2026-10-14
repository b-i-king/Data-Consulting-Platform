"""
data-visualization — pick the right chart for the data (Data Visualization).

Python implementation of README.md in this folder (Anthropic data plugin,
Apache-2.0). Looks at the column roles and recommends charts by the job the
data does, following the platform's chart rules:

  change over time       → line chart (date × number)
  compare categories     → horizontal bar chart, sorted (category × number)
  distribution           → histogram / box plot (one number)
  relationship           → scatter plot (number × number)
  part of a whole        → stacked bar (category × category × number); not pie
  one headline number    → a KPI tile, not a chart

Rules it enforces in the advice: one y-axis (never dual-axis), ≤ 8 colors
(fold the rest into "Other"), sorted bars, start bar axes at zero.
"""

from __future__ import annotations

import pandas as pd

from data_skills._core import (
    SkillResult, SkillSpec, categorical_columns, date_columns, numeric_columns,
)

SKILL = SkillSpec(
    id="data-visualization",
    title="Chart recommendations",
    description="Recommend the right chart types for this dataset, following accessible chart design rules.",
)


def run(df: pd.DataFrame) -> SkillResult:
    dates, nums, cats = date_columns(df), numeric_columns(df), categorical_columns(df, max_unique=30)
    recs = []
    if nums:
        recs.append({"chart": "KPI tile", "job": "headline number", "columns": nums[0],
                     "why": f"Total or average {nums[0]} — one number reads faster than any chart."})
    for d in dates[:1]:
        for n in nums[:2]:
            recs.append({"chart": "Line chart", "job": "change over time", "columns": f"{d} × {n}",
                         "why": f"Shows how {n} moves over time; add week-over-week or month-over-month comparison."})
    for c in cats[:2]:
        for n in nums[:1]:
            many = df[c].nunique() > 8
            recs.append({"chart": "Horizontal bar chart (sorted)", "job": "compare categories", "columns": f"{c} × {n}",
                         "why": f"Ranks {c} by {n}." + (" More than 8 values: show the top 7 + 'Other'." if many else "")})
    for n in nums[:2]:
        recs.append({"chart": "Histogram", "job": "distribution", "columns": n,
                     "why": f"Shows the spread of {n} and any long tail or outliers."})
    if len(nums) >= 2:
        recs.append({"chart": "Scatter plot", "job": "relationship", "columns": f"{nums[0]} × {nums[1]}",
                     "why": "Shows whether the two move together; add a trend line, not a second axis."})
    if len(cats) >= 2 and nums:
        recs.append({"chart": "Stacked bar", "job": "part of a whole", "columns": f"{cats[0]} × {cats[1]} × {nums[0]}",
                     "why": f"How each {cats[1]} contributes within each {cats[0]} (instead of several pie charts)."})
    rules = ["Never use two y-axes — make two charts or index both series to 100.",
             "Keep to 8 colors or fewer; fold small categories into 'Other'.",
             "Sort bars by value and start bar axes at zero.",
             "Label lines directly or with a legend so color is never the only cue."]
    return SkillResult(skill=SKILL.id, summary=f"{len(recs)} chart(s) recommended for this data.",
                       findings=rules, tables={"recommendations": recs})
