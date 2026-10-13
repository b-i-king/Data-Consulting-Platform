"""
Time intelligence: date ranges and period-over-period comparisons.

Used by the dashboard (build-dashboard) and trend skills. It answers two
Copilot Money-style questions for any table with a date column:

1. "Show me a time window."  — RANGES
       last_1_week     the 7 days ending on the "as of" date
       last_4_weeks    the 28 days ending on the "as of" date
       last_3_months   3 calendar months back from the "as of" date
       last_1_year     12 months back from the "as of" date
       month_to_date   the 1st of this month through the "as of" date
       year_to_date    January 1 through the "as of" date
       all             everything

   Each window is compared with the PREVIOUS window of the same kind:
       last week     vs. the 7 days before that
       last 4 weeks  vs. the 4 weeks before that
       last 3 months vs. the 3 months before that
       last 1 year   vs. the year before that
       month to date vs. the same days of last month  (Oct 1–13 vs. Sep 1–13)
       year to date  vs. the same days of last year   (Jan 1–Oct 13, 2025 vs. 2024)

2. "How did each period change?"  — COMPARISONS
       dod  day over day     each day vs. the day before
       wow  week over week   each week (Mon–Sun) vs. the week before
       mom  month over month each calendar month vs. the month before

"AS OF" DATE
------------
Windows end on the LATEST date in the data (not today), so a historical export
still shows full windows. Pass `as_of` to choose a different end date.

PARTIAL PERIODS
---------------
If the data stops mid-week or mid-month, that last week/month is marked
`partial` so a half-finished period isn't mistaken for a drop.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

RANGES: dict[str, str] = {
    "last_1_week": "Last week",
    "last_4_weeks": "Last 4 weeks",
    "last_3_months": "Last 3 months",
    "last_1_year": "Last year",
    "month_to_date": "Month to date",
    "year_to_date": "Year to date",
    "all": "All time",
}

# Short labels for narrow screens (phones), Copilot Money style.
RANGE_SHORT: dict[str, str] = {
    "last_1_week": "1W", "last_4_weeks": "4W", "last_3_months": "3M", "last_1_year": "1Y",
    "month_to_date": "MTD", "year_to_date": "YTD", "all": "All",
}
COMPARISON_SHORT: dict[str, str] = {"dod": "DoD", "wow": "WoW", "mom": "MoM"}

# comparison id -> (label, pandas resample rule, short name of the period)
COMPARISONS: dict[str, tuple[str, str, str]] = {
    "dod": ("Day over day", "D", "day"),
    "wow": ("Week over week", "W-MON", "week"),
    "mom": ("Month over month", "MS", "month"),
}


@dataclass
class Window:
    """A date window and the window it is compared with (inclusive dates)."""

    key: str
    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    prev_start: pd.Timestamp | None
    prev_end: pd.Timestamp | None

    def as_dict(self) -> dict:
        fmt = lambda d: d.date().isoformat() if d is not None else None  # noqa: E731
        return {"key": self.key, "label": self.label, "start": fmt(self.start), "end": fmt(self.end),
                "prev_start": fmt(self.prev_start), "prev_end": fmt(self.prev_end)}


def window(range_key: str, as_of: pd.Timestamp, first_date: pd.Timestamp) -> Window:
    """Work out the start/end of a range and of the period it is compared with."""
    if range_key not in RANGES:
        raise ValueError(f"Unknown range '{range_key}'. Use one of: {', '.join(RANGES)}")
    end = as_of.normalize()
    day = pd.Timedelta(days=1)
    label = RANGES[range_key]

    if range_key == "all":
        return Window(range_key, label, first_date.normalize(), end, None, None)
    if range_key == "last_1_week":
        start = end - pd.Timedelta(days=6)
        return Window(range_key, label, start, end, start - pd.Timedelta(days=7), start - day)
    if range_key == "last_4_weeks":
        start = end - pd.Timedelta(days=27)
        return Window(range_key, label, start, end, start - pd.Timedelta(days=28), start - day)
    if range_key in ("last_3_months", "last_1_year"):
        months = 3 if range_key == "last_3_months" else 12
        start = end - pd.DateOffset(months=months) + day
        prev_end = start - day
        return Window(range_key, label, start, end, prev_end - pd.DateOffset(months=months) + day, prev_end)
    if range_key == "month_to_date":
        start = end.replace(day=1)
        prev_start = start - pd.DateOffset(months=1)
        # Same number of days into last month (Oct 31 -> Sep 30, not Oct 1).
        prev_end = min(prev_start + (end - start), start - day)
        return Window(range_key, label, start, end, prev_start, prev_end)
    # year_to_date
    start = end.replace(month=1, day=1)
    prev_start = start - pd.DateOffset(years=1)
    prev_end = end - pd.DateOffset(years=1)
    return Window(range_key, label, start, end, prev_start, prev_end)


def _in(dates: pd.Series, start, end) -> pd.Series:
    return (dates >= start) & (dates < end + pd.Timedelta(days=1))


def window_totals(df: pd.DataFrame, date_col: str, value_cols: list[str], w: Window, agg: str = "sum") -> dict:
    """KPI numbers for a window: current vs. previous total, change and % change."""
    dates = df[date_col]
    cur = df.loc[_in(dates, w.start, w.end), value_cols].agg(agg)
    out = {"rows": int(_in(dates, w.start, w.end).sum())}
    if w.prev_start is not None:
        prev_mask = _in(dates, w.prev_start, w.prev_end)
        prev = df.loc[prev_mask, value_cols].agg(agg)
        out["prev_rows"] = int(prev_mask.sum())
    for col in value_cols:
        c = float(cur[col]) if pd.notna(cur[col]) else 0.0
        item = {"current": c}
        if w.prev_start is not None:
            p = float(prev[col]) if pd.notna(prev[col]) else 0.0
            item.update(previous=p, change=c - p, pct_change=((c - p) / abs(p)) if p else None)
        out[col] = item
    return out


def period_series(
    df: pd.DataFrame, date_col: str, value_col: str, comparison: str,
    start: pd.Timestamp | None = None, end: pd.Timestamp | None = None, agg: str = "sum",
) -> pd.DataFrame:
    """
    One row per day / week / month with the value, the previous period's value,
    the change and % change. Empty periods count as 0 for sums and counts.

    Columns: period, value, previous, change, pct_change, partial
    """
    label, rule, _ = COMPARISONS[comparison]
    data = df[[date_col, value_col]].dropna(subset=[date_col]).set_index(date_col).sort_index()
    if comparison == "wow":
        series = data[value_col].resample(rule, label="left", closed="left").agg(agg)  # weeks start Monday
    else:
        series = data[value_col].resample(rule).agg(agg)
    if agg in ("sum", "count"):
        series = series.fillna(0)

    out = pd.DataFrame({"period": series.index, "value": series.values})
    out["previous"] = out["value"].shift(1)
    out["change"] = out["value"] - out["previous"]
    out["pct_change"] = out["change"] / out["previous"].abs().where(out["previous"] != 0)

    # Are the FIRST or LAST periods cut short by where the data starts/ends?
    # (Data starting Oct 11 makes October look like a weak month.)
    first_date, last_date = data.index.min().normalize(), data.index.max().normalize()
    out["partial"] = False
    if len(out) and comparison != "dod":
        period_end = (out["period"].iloc[-1] + (pd.Timedelta(days=6) if comparison == "wow"
                                                 else pd.offsets.MonthEnd(0)))
        out.loc[out.index[-1], "partial"] = bool(last_date < period_end.normalize())
        out.loc[out.index[0], "partial"] = bool(out.loc[out.index[0], "partial"] or first_date > out["period"].iloc[0])

    if start is not None:
        # Keep periods that overlap the window (a week starting before it still counts).
        keep_from = start - (pd.Timedelta(days=6) if comparison == "wow" else
                             pd.Timedelta(days=0) if comparison == "dod" else pd.Timedelta(days=start.day - 1))
        out = out[out["period"] >= keep_from]
    if end is not None:
        out = out[out["period"] <= end]
    return out.reset_index(drop=True)


def best_comparison(first: pd.Timestamp, last: pd.Timestamp) -> str:
    """Pick a sensible default granularity for the span of the data."""
    days = (last - first).days
    return "dod" if days <= 45 else "wow" if days <= 200 else "mom"


# ── What the data can support ────────────────────────────────────────────────
# Ranges and comparisons are offered only when there is enough history, so a
# brand-new export doesn't show "Last year" with one week of data in it:
#
#   last week / 4 weeks / 3 months / year   the data covers the whole window
#   month to date, year to date             the data also covers the SAME DAYS
#                                           last month / last year (they exist
#                                           to be compared, so that must be fair)
#   all time                                more than a year of data
#
#   day over day     at least 2 days      (in the data and in the range)
#   week over week   at least 2 weeks
#   month over month at least 2 months

COMPARISON_MIN_DAYS = {"dod": 2, "wow": 14, "mom": 59}


def available_ranges(first_date: pd.Timestamp, as_of: pd.Timestamp) -> list[str]:
    """Range ids the data supports, in RANGES order (may be empty)."""
    first = first_date.normalize()
    out = []
    for key in RANGES:
        w = window(key, as_of, first_date)
        if key == "all":
            ok = (as_of.normalize() - first).days + 1 > 365
        elif key in ("month_to_date", "year_to_date"):
            ok = first <= w.prev_start
        else:
            ok = first <= w.start
        if ok:
            out.append(key)
    return out


def available_comparisons(first_date: pd.Timestamp, last_date: pd.Timestamp,
                          start: pd.Timestamp | None = None, end: pd.Timestamp | None = None) -> list[str]:
    """
    Comparison ids that make sense for the data (and, if given, for one range):
    each needs at least two periods' worth of days.
    """
    days = (last_date.normalize() - first_date.normalize()).days + 1
    if start is not None and end is not None:
        days = min(days, (end - start).days + 1)
    return [k for k, need in COMPARISON_MIN_DAYS.items() if days >= need]
