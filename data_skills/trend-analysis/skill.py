"""
trend-analysis — the headline story over time (Data Exploration & Analysis).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). For a date + number column:

  1. Monthly series, with month-over-month and year-over-year growth
  2. Overall direction — a straight-line fit through the monthly totals,
     reported as average change per month and over the whole span
  3. Seasonality — average by month of year and by day of week, relative to
     the overall average (e.g. "December runs 28% above average")
  4. Best and worst months
  5. Segment shifts — if a category column is given, which categories grew
     their share between the first and last 6 months
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data_skills import _time
from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, as_datetime, date_columns, fmt_num, fmt_pct, line_chart,
    numeric_columns, records, require_column,
)

SKILL = SkillSpec(
    id="trend-analysis",
    title="Key trends & growth rates",
    description="Monthly trend, MoM/YoY growth, seasonality by month and weekday, and segment shifts.",
    params={
        "date_column": Param("column", None, "Date column (auto-detected)"),
        "value_column": Param("column", None, "Number to trend (default: first number column)"),
        "segment_column": Param("column", None, "Optional category to check for share shifts"),
        "agg": Param("choice", "sum", "How to combine", ("sum", "mean", "count")),
    },
)


def run(df, date_column=None, value_column=None, segment_column=None, agg="sum") -> SkillResult:
    """
    Tell the headline story of a number over time: direction, growth, seasonality, segments.

    Steps:
    - Monthly series via _time.period_series (partial months are left out); needs 3+ full months
    - Direction: slope of a straight-line fit through the monthly values, as change per month,
      % of an average month, and % over the whole span
    - Latest full month with month-over-month and (with > 12 months) year-over-year change
    - Best and weakest months
    - Seasonality by month of year (12+ months) and by day of week, vs. the average
    - Segment shifts (when segment_column is given): change in each segment's share between
      the first 6 and last 6 months, in percentage points

    Args:
        df: pd.DataFrame, the table.
        date_column: str | None, date column (auto-detected when None).
        value_column: str | None, number to trend (default: first number column).
        segment_column: str | None, optional category to check for share shifts.
        agg: str, how to combine values per period: "sum" (default), "mean", or "count".

    Returns:
        SkillResult: summary is the direction finding; other findings; tables "monthly" and,
        with a segment column, "segment_share_shift"; one line chart by month.

    Raises:
        SkillInputError: if no date/number column is found, no dates parse, fewer than 3 full
            months exist, or segment_column is not in the table.

    Example:
        >>> res = run(pd.read_csv("samples/sales.csv"), value_column="amount", segment_column="store")
        >>> res.summary   # e.g. "Direction: up about 1,234.56 per month (...)"
    """
    dcol = require_column(df, date_column, "date", date_columns(df))
    vcol = require_column(df, value_column, "number", numeric_columns(df))
    data = df.assign(**{dcol: as_datetime(df[dcol])}).dropna(subset=[dcol])
    if data.empty:
        raise SkillInputError(f"No usable dates in '{dcol}'.")

    monthly = _time.period_series(data, dcol, vcol, "mom", agg=agg)
    full = monthly[~monthly["partial"]]
    if len(full) < 3:
        raise SkillInputError("Trend analysis needs at least 3 full months of data.")
    values = full["value"].to_numpy(dtype=float)
    full = full.assign(yoy_pct=full["value"].pct_change(12) if len(full) > 12 else np.nan)

    slope = np.polyfit(np.arange(len(values)), values, 1)[0]
    avg = values.mean()
    findings = [f"Direction: {'up' if slope > 0 else 'down'} about {fmt_num(abs(slope))} per month "
                f"({fmt_pct(slope / avg) if avg else 'n/a'} of an average month), "
                f"{fmt_pct(slope * (len(values) - 1) / values[0]) if values[0] else 'n/a'} over {len(values)} months."]
    last = full.iloc[-1]
    findings.append(f"Latest full month ({last['period'].date()}): {fmt_num(last['value'])}, "
                    f"{fmt_pct(last['pct_change'])} month over month"
                    + (f", {fmt_pct(last['yoy_pct'])} year over year." if pd.notna(last['yoy_pct']) else "."))
    best, worst = full.loc[full["value"].idxmax()], full.loc[full["value"].idxmin()]
    findings.append(f"Best month: {best['period'].strftime('%b %Y')} ({fmt_num(best['value'])}); "
                    f"weakest: {worst['period'].strftime('%b %Y')} ({fmt_num(worst['value'])}).")

    season = full.groupby(full["period"].dt.month)["value"].mean() / avg - 1
    if len(full) >= 12:
        hi, lo = season.idxmax(), season.idxmin()
        findings.append(f"Seasonality: {pd.Timestamp(2000, hi, 1):%B} runs {fmt_pct(season[hi])} vs. average; "
                        f"{pd.Timestamp(2000, lo, 1):%B} {fmt_pct(season[lo])}.")
    daily = data.groupby(data[dcol].dt.normalize())[vcol].agg(agg)
    dow = daily.groupby(daily.index.dayofweek).mean()
    dow = dow / dow.mean() - 1
    findings.append(f"Busiest weekday: {pd.Timestamp(2024, 1, 1 + int(dow.idxmax())):%A} ({fmt_pct(dow.max())} vs. average day); "
                    f"slowest: {pd.Timestamp(2024, 1, 1 + int(dow.idxmin())):%A} ({fmt_pct(dow.min())}).")

    tables = {"monthly": records(full.assign(period=full["period"].dt.date.astype(str)), None)}
    if segment_column:
        if segment_column not in data.columns:
            raise SkillInputError(f"Column '{segment_column}' was not found.")
        cutoff_early = data[dcol].min() + pd.DateOffset(months=6)
        cutoff_late = data[dcol].max() - pd.DateOffset(months=6)
        early = data[data[dcol] < cutoff_early].groupby(segment_column)[vcol].agg(agg)
        late = data[data[dcol] > cutoff_late].groupby(segment_column)[vcol].agg(agg)
        shift = (late / late.sum() - early / early.sum()).dropna().sort_values()
        tables["segment_share_shift"] = [{"segment": str(k), "share_change_pts": round(v * 100, 2)} for k, v in shift.items()]
        findings.append(f"Share gainer: {shift.index[-1]} ({shift.iloc[-1] * 100:+.1f} pts); "
                        f"share loser: {shift.index[0]} ({shift.iloc[0] * 100:+.1f} pts) — first vs. last 6 months.")

    chart = line_chart(f"{vcol} by month", full["period"].dt.date.astype(str).tolist(), {vcol: full["value"].tolist()})
    return SkillResult(skill=SKILL.id, summary=findings[0], findings=findings[1:], tables=tables, charts=[chart])
