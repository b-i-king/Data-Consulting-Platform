"""
standard-deviation — spread and variability (Data Exploration & Analysis).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). For each number column:

  * sample SD (ddof=1 — use for a sample of a bigger population) and
    population SD (ddof=0 — use when you have every record)
  * variance, IQR (middle 50%), MAD (median absolute deviation, robust)
  * CV = SD ÷ mean — spread relative to size, comparable across columns

Flags columns where SD misleads: heavy skew or outliers (use IQR/MAD),
near-constant values (CV < 1%), or a mean near zero (CV meaningless).
"""

from __future__ import annotations

import pandas as pd
from scipy import stats

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, fmt_num, numeric_columns

SKILL = SkillSpec(
    id="standard-deviation",
    title="Spread & variability",
    description="Sample vs. population SD, variance, IQR, MAD, and CV, with warnings where SD misleads.",
    params={
        "columns": Param("columns", None, "Number columns (default: all)"),
        "group_column": Param("column", None, "Optional category to compute spread per group"),
    },
)


def spread(s: pd.Series) -> dict:
    """
    Compute every spread measure for one number column.

    Missing values are dropped first. Sample SD/variance use ddof=1 (NaN with one value);
    population SD uses ddof=0. CV is SD ÷ |mean| × 100, or None when the mean is 0.
    Skew is 0.0 with fewer than 3 values.

    Args:
        s: pd.Series, the numeric values.

    Returns:
        dict: keys n, mean, sd_sample, sd_population, variance, iqr, mad (median absolute
        deviation), cv_pct, skew.

    Example:
        >>> r = spread(pd.Series([1, 2, 3, 4]))
        >>> round(r["sd_sample"], 3), r["iqr"], r["mad"]
        (1.291, 1.5, 1.0)
    """
    s = s.dropna().astype(float)
    mean = s.mean()
    sd = s.std(ddof=1) if len(s) > 1 else float("nan")
    q1, q3 = s.quantile([0.25, 0.75])
    return {"n": len(s), "mean": mean, "sd_sample": sd, "sd_population": s.std(ddof=0), "variance": s.var(ddof=1),
            "iqr": q3 - q1, "mad": float(stats.median_abs_deviation(s)) if len(s) else float("nan"),
            "cv_pct": (sd / abs(mean) * 100) if mean else None, "skew": float(stats.skew(s)) if len(s) > 2 else 0.0}


def run(df, columns=None, group_column=None) -> SkillResult:
    """
    Report spread and variability for number columns, with warnings where SD misleads.

    Computes spread() per column (and per group when group_column is given) and adds a note:
    "skewed — prefer IQR/MAD" when |skew| > 1, "nearly constant" when CV < 1%, or
    "mean ≈ 0, CV not meaningful". Findings cover the first 8 rows; floats are rounded to 4
    decimals.

    Args:
        df: pd.DataFrame, the table.
        columns: list[str] | None, number columns (default: all numeric_columns).
        group_column: str | None, optional category to compute spread per group.

    Returns:
        SkillResult: summary naming the column with the highest CV; findings per column;
        table "spread".

    Raises:
        SkillInputError: if there are no number columns to analyze.

    Example:
        >>> res = run(df, columns=["amount"], group_column="store")
        >>> res.tables["spread"][0]["group"]   # first store name
    """
    cols = columns or numeric_columns(df)
    if not cols:
        raise SkillInputError("No number columns to analyze.")
    rows, findings = [], []
    groups = df.groupby(group_column) if group_column else [("all rows", df)]
    for gname, g in groups:
        for c in cols:
            r = {"group": str(gname), "column": c, **spread(g[c])}
            note = ""
            if abs(r["skew"]) > 1:
                note = "skewed — prefer IQR/MAD"
            elif r["cv_pct"] is not None and r["cv_pct"] < 1:
                note = "nearly constant"
            elif r["cv_pct"] is None or abs(r["mean"]) < 1e-9:
                note = "mean ≈ 0, CV not meaningful"
            r["note"] = note
            rows.append({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    for r in rows[:8]:
        findings.append(f"{r['group'] + ' · ' if group_column else ''}{r['column']}: SD {fmt_num(r['sd_sample'])} "
                        f"(CV {fmt_num(r['cv_pct'], 1) if r['cv_pct'] is not None else 'n/a'}%), IQR {fmt_num(r['iqr'])}"
                        + (f" — {r['note']}" if r["note"] else ""))
    most = max((r for r in rows if r["cv_pct"] is not None), key=lambda r: r["cv_pct"], default=None)
    return SkillResult(skill=SKILL.id,
                       summary=(f"Most variable relative to its size: {most['column']} (CV {most['cv_pct']:.0f}%)." if most
                                else "Spread computed."),
                       findings=findings, tables={"spread": rows})
