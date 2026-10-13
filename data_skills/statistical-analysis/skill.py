"""
statistical-analysis — descriptive statistics done right (Data Exploration & Analysis).

Python implementation of SKILL.md in this folder (Anthropic data plugin,
Apache-2.0). For each number column:

  * count, mean, median, standard deviation, min/max, 5th/95th percentiles
  * skewness — |skew| > 1 means a long tail: report the MEDIAN, not the mean
  * normality — D'Agostino test (n ≥ 20); non-normal data → prefer
    rank-based tests (Mann-Whitney) over t-tests
  * a 95% confidence interval for the mean (t-distribution)

Optionally grouped by a category column (e.g. stats per store).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, fmt_num, numeric_columns

SKILL = SkillSpec(
    id="statistical-analysis",
    title="Statistical analysis",
    description="Descriptive stats, skew, normality, and confidence intervals per number column.",
    params={
        "columns": Param("columns", None, "Number columns (default: all)"),
        "group_column": Param("column", None, "Optional category to compute stats per group"),
    },
)


def describe(s: pd.Series) -> dict:
    s = s.dropna().astype(float)
    n = len(s)
    row = {"n": n, "mean": s.mean(), "median": s.median(), "std": s.std(ddof=1) if n > 1 else np.nan,
           "min": s.min(), "p05": s.quantile(0.05), "p95": s.quantile(0.95), "max": s.max(),
           "skew": float(stats.skew(s)) if n > 2 else np.nan}
    if n > 1:
        half = stats.t.ppf(0.975, n - 1) * row["std"] / np.sqrt(n)
        row["ci95_low"], row["ci95_high"] = row["mean"] - half, row["mean"] + half
    row["normal"] = bool(stats.normaltest(s).pvalue >= 0.05) if n >= 20 and s.nunique() > 3 else None
    return row


def run(df, columns=None, group_column=None) -> SkillResult:
    cols = columns or numeric_columns(df)
    if not cols:
        raise SkillInputError("No number columns to analyze.")
    rows, findings = [], []
    groups = df.groupby(group_column) if group_column else [("all rows", df)]
    for gname, g in groups:
        for c in cols:
            d = describe(g[c])
            rows.append({"group": str(gname), "column": c, **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in d.items()}})
    for r in rows if not group_column else []:
        if r["skew"] is not None and abs(r["skew"]) > 1:
            findings.append(f"{r['column']} is skewed (skew {r['skew']:.1f}): typical value is the median "
                            f"{fmt_num(r['median'])}, not the mean {fmt_num(r['mean'])}.")
        if r["normal"] is False:
            findings.append(f"{r['column']} is not normally distributed — use rank-based tests (Mann-Whitney) for comparisons.")
    if group_column:
        for c in cols:
            sub = [r for r in rows if r["column"] == c]
            hi, lo = max(sub, key=lambda r: r["mean"]), min(sub, key=lambda r: r["mean"])
            # Do the groups' 95% confidence intervals overlap? Then call them similar.
            if hi.get("ci95_low") is not None and lo.get("ci95_high") is not None and hi["ci95_low"] <= lo["ci95_high"]:
                findings.append(f"{c}: similar across groups (means {fmt_num(lo['mean'])}–{fmt_num(hi['mean'])}, overlapping 95% intervals).")
            else:
                findings.append(f"{c}: highest mean in {hi['group']} ({fmt_num(hi['mean'])}), lowest in {lo['group']} ({fmt_num(lo['mean'])}).")
    return SkillResult(skill=SKILL.id, summary=f"Statistics for {len(cols)} column(s)"
                       + (f" across {df[group_column].nunique()} {group_column} groups." if group_column else "."),
                       findings=findings, tables={"statistics": rows})
