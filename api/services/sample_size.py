"""
Sample-size snapshot: "Is there enough data to answer my question?"

WHERE THIS COMES FROM
---------------------
Adapted from the `sample-size` skill in Daniel Rosehill's
Claude-Data-Analyst-plugin (MIT License):
https://github.com/danielrosehill/Claude-Data-Analyst-plugin/tree/master/skills/sample-size
The full adapted skill lives at `data_skills/sample-size/SKILL.md`.

The skill's core idea: ROW COUNT IS NOT SAMPLE SIZE. This module implements
the parts of the skill that can run automatically on any uploaded table
(Steps 1, 4 and 5 of the skill). The parts that need the user's specific
question — unit of analysis, per-group power calculations — are left to the
"Data exploration & analysis" service (a consultant or a later feature).

WHAT IT REPORTS
---------------
1. Size snapshot: rows, columns, unique rows, memory footprint, and the
   time span covered if there is a date column.
2. Effective n: how many rows have NO missing values at all ("complete
   rows"). Many analyses silently drop incomplete rows, so this is the
   honest n for a multi-column analysis.
3. Rules-of-thumb verdicts (green / yellow / red) for common questions,
   using the thresholds from the skill's adequacy table.
"""

from __future__ import annotations

import pandas as pd

# (question, minimum rows, explanation) — from the skill's Step 5 table.
_RULES_OF_THUMB = [
    ("Estimate a percentage within ±5 points (95% confidence)", 385,
     "The classic survey number. Fewer rows means a wider margin of error."),
    ("Compare two groups — medium-sized difference (Cohen's d = 0.5)", 128,
     "About 64 per group for 80% power at the usual 5% significance level."),
    ("Compare two groups — small difference (Cohen's d = 0.2)", 788,
     "About 394 per group for 80% power."),
    ("Estimate an average with a margin of about ±10% of the spread", 100,
     "Standard error ≈ standard deviation / 10."),
]


def _verdict(n: int, needed: int) -> str:
    """green = enough, yellow = within half of the target, red = well short."""
    if n >= needed:
        return "green"
    if n >= needed / 2:
        return "yellow"
    return "red"


def _time_span(df: pd.DataFrame) -> dict | None:
    """Find the first column that is mostly dates and describe its range."""
    for col in df.columns:
        s = df[col].dropna()
        if s.empty:
            continue
        if pd.api.types.is_datetime64_any_dtype(s):
            parsed = s
        elif s.dtype == object and any(k in str(col).lower() for k in ("date", "time", "created", "day", "month")):
            parsed = pd.to_datetime(s.astype(str), errors="coerce", format="mixed")
            if parsed.notna().mean() < 0.8:
                continue
            parsed = parsed.dropna()
        else:
            continue
        first, last = parsed.min(), parsed.max()
        days = max((last - first).days, 1)
        return {
            "column": str(col),
            "first": first.isoformat(),
            "last": last.isoformat(),
            "days_covered": int(days),
            "rows_per_day": round(len(parsed) / days, 2),
        }
    return None


def sample_size_snapshot(df: pd.DataFrame) -> dict:
    """Build the automatic part of the sample-size assessment."""
    n_rows = int(len(df))
    n_cols = int(df.shape[1])
    complete_rows = int(df.dropna(how="any").shape[0])
    drop_rate = (1 - complete_rows / n_rows) if n_rows else 0.0

    # Regression rule of thumb (Harrell): 10–20 rows per predictor.
    max_predictors_10 = complete_rows // 10
    max_predictors_20 = complete_rows // 20

    checks = [
        {"question": q, "rows_needed": need, "rows_available": complete_rows,
         "verdict": _verdict(complete_rows, need), "note": note}
        for q, need, note in _RULES_OF_THUMB
    ]

    warnings = []
    if drop_rate > 0.2:
        warnings.append(
            f"{drop_rate:.0%} of rows have at least one missing value. Any analysis that needs "
            "every column will only see the complete rows."
        )
    warnings.append(
        "Row count is not always sample size: if several rows belong to the same customer, store "
        "or account, the honest n for questions about them is the number of distinct ones."
    )

    return {
        "rows": n_rows,
        "columns": n_cols,
        "unique_rows": int(len(df.drop_duplicates())),
        "complete_rows": complete_rows,
        "incomplete_row_pct": round(drop_rate * 100, 2),
        "memory_mb": round(df.memory_usage(deep=True).sum() / 1024 / 1024, 3),
        "time_span": _time_span(df),
        "regression_predictor_guidance": (
            f"With {complete_rows:,} complete rows, a regression model can reasonably use about "
            f"{max(max_predictors_20, 1):,}–{max_predictors_10:,} predictor columns (10–20 rows per predictor)."
            if max_predictors_10 >= 1
            else f"With {complete_rows:,} complete rows there is not enough data for a regression model yet "
                 "(rule of thumb: 10–20 complete rows per predictor column)."
        ),
        "checks": checks,
        "warnings": warnings,
    }
