"""
sample-size — "is there enough data to answer this?" (Data Strategy & Discovery)

Python implementation of SKILL.md in this folder (adapted from Daniel
Rosehill's skill, MIT). Steps:

  1. Size snapshot + rules of thumb  (api/services/sample_size.py, also shown on the website)
  2. Unit of analysis — if you name the unit column (e.g. customer_id), the
     honest n is the number of DISTINCT units, not rows
  3. Group cells — for a grouping column: size of each group, the smallest
     cell, and the imbalance ratio (largest ÷ smallest)
  4. Power — the smallest difference between two groups (Cohen's d) you can
     detect with 80% power at α = 0.05, given the smallest two groups:
         d ≈ (z₀.₉₇₅ + z₀.₈₀) × √(1/n₁ + 1/n₂)
  5. Verdict per question: green / yellow / red
"""

from __future__ import annotations

import math

import pandas as pd
from scipy.stats import norm

from api.services.sample_size import sample_size_snapshot
from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, bar_chart, fmt_num

SKILL = SkillSpec(
    id="sample-size",
    title='"Is there enough data?" assessment',
    description="Effective sample size, group cells, imbalance, and the smallest difference you can detect.",
    params={
        "unit_column": Param("column", None, "What one independent unit is (e.g. customer_id)"),
        "group_column": Param("column", None, "Grouping to compare (e.g. store, plan)"),
    },
)

Z = norm.ppf(0.975) + norm.ppf(0.80)   # ≈ 2.80 for 80% power, two-sided α = 0.05


def detectable_d(n1: int, n2: int) -> float:
    """Smallest standardized difference (Cohen's d) detectable with 80% power."""
    return Z * math.sqrt(1 / n1 + 1 / n2)


def run(df: pd.DataFrame, unit_column=None, group_column=None) -> SkillResult:
    snap = sample_size_snapshot(df)
    findings = list(snap["warnings"][:1])
    tables = {"rules_of_thumb": snap["checks"]}
    charts = []
    honest_n = snap["complete_rows"]

    if unit_column:
        if unit_column not in df.columns:
            raise SkillInputError(f"Column '{unit_column}' was not found.")
        units = int(df[unit_column].nunique())
        honest_n = units
        findings.append(f"{fmt_num(len(df))} rows but only {fmt_num(units)} distinct '{unit_column}' values — "
                        f"for questions about {unit_column}s, n = {fmt_num(units)}.")

    if group_column:
        if group_column not in df.columns:
            raise SkillInputError(f"Column '{group_column}' was not found.")
        sizes = (df.groupby(group_column)[unit_column].nunique() if unit_column
                 else df[group_column].value_counts()).sort_values(ascending=False)
        tables["group_cells"] = [{"group": str(k), "n": int(v)} for k, v in sizes.items()]
        charts.append(bar_chart(f"Group sizes — {group_column}", [str(k) for k in sizes.index], sizes.tolist(), "n"))
        smallest, largest = int(sizes.min()), int(sizes.max())
        ratio = largest / smallest if smallest else math.inf
        findings.append(f"Smallest group: '{sizes.idxmin()}' with n = {fmt_num(smallest)}; imbalance {ratio:.1f}:1"
                        + (" — above 10:1, comparisons will be unreliable." if ratio > 10 else "."))
        if len(sizes) >= 2 and smallest >= 2:
            two_smallest = sizes.nsmallest(2)
            d = detectable_d(int(two_smallest.iloc[0]), int(two_smallest.iloc[1]))
            size = "small" if d <= 0.3 else "medium" if d <= 0.6 else "large"
            findings.append(f"Comparing the two smallest groups, you can reliably detect a difference of "
                            f"d = {d:.2f} or larger ({size} effect, 80% power). Smaller differences will be inconclusive.")

    # Re-judge the rules of thumb against the HONEST n (distinct units when given).
    checks = [{**c, "rows_available": honest_n,
               "verdict": "green" if honest_n >= c["rows_needed"] else "yellow" if honest_n >= c["rows_needed"] / 2 else "red"}
              for c in snap["checks"]]
    tables["rules_of_thumb"] = checks
    greens = [c["question"] for c in checks if c["verdict"] == "green"]
    reds = [c["question"] for c in checks if c["verdict"] == "red"]
    summary = (f"Honest sample size: {fmt_num(honest_n)}. "
               + (f"Enough for: {greens[0].lower()}." if greens else "Not enough for the standard checks yet.")
               + (f" Not enough for: {reds[0].lower()}." if reds else ""))
    return SkillResult(skill=SKILL.id, summary=summary, findings=findings, tables=tables, charts=charts,
                       data={"snapshot": snap, "honest_n": honest_n})
