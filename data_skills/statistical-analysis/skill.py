"""
statistical-analysis — descriptive statistics done right (Data Exploration & Analysis).

Python implementation of README.md in this folder (Anthropic data plugin,
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
    """
    Descriptive statistics for one number column.

    Missing values are dropped first. Std uses ddof=1 (NaN with one value); skew is NaN
    with fewer than 3 values. A 95% confidence interval for the mean (t-distribution) is
    added when n > 1. "normal" is the D'Agostino test result (p ≥ 0.05), or None when
    n < 20 or there are 3 or fewer distinct values.

    Args:
        s: pd.Series, the numeric values.

    Returns:
        dict: keys n, mean, median, std, min, p05, p95, max, skew, ci95_low and ci95_high
        (only when n > 1), and normal (bool or None).

    Example:
        >>> r = describe(pd.Series([1, 2, 3, 4]))
        >>> round(r["ci95_low"], 3), round(r["ci95_high"], 3), r["normal"]
        (0.446, 4.554, None)
    """
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
    """
    Descriptive statistics, skew, normality, and confidence intervals for number columns.

    Runs describe() per column (and per group when group_column is given; floats rounded to
    4 decimals). Ungrouped findings flag skewed columns (|skew| > 1 -> report the median) and
    non-normal ones (use rank-based tests). Grouped findings compare the highest and lowest
    group means: overlapping 95% intervals are called "similar".

    Args:
        df: pd.DataFrame, the table.
        columns: list[str] | None, number columns (default: all numeric_columns).
        group_column: str | None, optional category to compute stats per group.

    Returns:
        SkillResult: summary with the column (and group) count; findings; table "statistics".

    Raises:
        SkillInputError: if there are no number columns to analyze.

    Example:
        >>> res = run(df, columns=["amount"])
        >>> res.summary
        'Statistics for 1 column(s).'
    """
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
