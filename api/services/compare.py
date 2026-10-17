"""
"Compare over time" (beta): two tables that share nothing but TIME, side by side.

WHY THIS EXISTS
---------------
Some sources have no IDs or names in common — a game tracker and a business's
invoices, say — so there is nothing to join on. What they can share is the
calendar: "in weeks I played more, did my invoiced sales go down?". This adds
up each table by week or month and lines the two up on the same periods:

    period      left (hours played)   right (sales, $)
    2026-01     42                    3,120
    2026-02     18                    4,410
    ...

and reports how closely they move together (a Pearson correlation, -1 to 1),
with two cautions always in view: few periods make it unreliable, and moving
together never proves that one causes the other.

Nothing is stored: the tables are the ones open in the tab (sent again, like
every analysis).
"""

from __future__ import annotations

import math

import pandas as pd

from data_skills._core import SkillInputError, as_datetime

PERIODS = {"week": "W-SUN", "month": "M"}   # weeks start on Monday (pandas: end on Sunday)
AGGS = ("sum", "mean", "count")
MIN_OVERLAP = 3        # fewer shared periods: no correlation at all
RELIABLE_OVERLAP = 12  # fewer: a correlation, with a caution


def series(df: pd.DataFrame, date_column: str, value_column: str | None, agg: str, period: str) -> pd.Series:
    """
    One table added up by period.

    Args:
        df: the cleaned table.
        date_column: its date column.
        value_column: the number to add up (ignored for "count").
        agg: "sum", "mean" or "count" (rows per period).
        period: "week" or "month".

    Returns:
        pd.Series: indexed by the period's first day ("2026-01-01"), sorted.

    Raises:
        SkillInputError: an unknown column, no dates, or no numbers.

    Example:
        >>> series(pd.DataFrame({"d": ["2026-01-05", "2026-01-20"], "v": [1, 2]}), "d", "v", "sum", "month").to_dict()
        {'2026-01-01': 3.0}
    """
    if agg not in AGGS or period not in PERIODS:
        raise SkillInputError("Choose sum, average or count, by week or by month.")
    if date_column not in df.columns:
        raise SkillInputError(f"'{date_column}' isn't a column of that table.")
    dates = as_datetime(df[date_column])
    if getattr(dates.dt, "tz", None) is not None:
        dates = dates.dt.tz_convert(None)
    if agg == "count":
        values = pd.Series(1.0, index=df.index)
    else:
        if value_column not in df.columns:
            raise SkillInputError(f"'{value_column}' isn't a column of that table.")
        values = pd.to_numeric(df[value_column], errors="coerce")
    keep = dates.notna() & values.notna()
    if not keep.any():
        raise SkillInputError(f"No rows have both a date in '{date_column}' and a number to add up.")
    start = dates[keep].dt.to_period(PERIODS[period]).dt.start_time.dt.strftime("%Y-%m-%d")
    grouped = values[keep].groupby(start.values)
    out = grouped.sum() if agg in ("sum", "count") else grouped.mean()
    return out.sort_index().astype(float)


def _strength(r: float) -> str:
    """Plain words for a correlation: |r| < 0.2 none, < 0.4 weak, < 0.7 moderate, else strong."""
    a = abs(r)
    if a < 0.2:
        return "no clear"
    word = "a weak" if a < 0.4 else "a moderate" if a < 0.7 else "a strong"
    return f"{word} {'positive' if r > 0 else 'negative'}"


def compare(left: pd.Series, right: pd.Series, left_label: str, right_label: str, period: str) -> dict:
    """
    Line two period series up and say how closely they move together.

    Returns:
        dict: {"periods": [...], "left": {"label", "values"}, "right": {"label", "values"}
        (None where a table has nothing that period), "overlap" (periods both have),
        "correlation" (r or None), "summary", "cautions": [...]}.

    Example:
        >>> compare(pd.Series({"2026-01-01": 1.0}), pd.Series({"2026-01-01": 5.0}), "A", "B", "month")["overlap"]
        1
    """
    periods = sorted(set(left.index) | set(right.index))
    both = [p for p in periods if p in left.index and p in right.index]
    value = lambda s, p: None if p not in s.index or math.isnan(s[p]) else round(float(s[p]), 4)   # noqa: E731
    r = None
    cautions = []
    word = "weeks" if period == "week" else "months"
    if len(both) >= MIN_OVERLAP:
        a, b = left[both], right[both]
        if a.std() > 0 and b.std() > 0:
            r = round(float(a.corr(b)), 3)
    if r is None:
        summary = (f"The two tables share only {len(both)} {word}, too few to say whether they move together."
                   if len(both) < MIN_OVERLAP else
                   f"One of them doesn't change across the {len(both)} shared {word}, so there's nothing to compare.")
    else:
        summary = (f"Across {len(both)} {word} that both tables cover, {left_label} and {right_label} show "
                   f"{_strength(r)} relationship (r = {r:+.2f}).")
        if len(both) < RELIABLE_OVERLAP:
            cautions.append(f"Only {len(both)} shared {word}: a pattern this short can easily be chance.")
    cautions.append("Moving together doesn't mean one causes the other — something else may drive both.")
    return {"periods": periods, "left": {"label": left_label, "values": [value(left, p) for p in periods]},
            "right": {"label": right_label, "values": [value(right, p) for p in periods]},
            "overlap": len(both), "correlation": r, "summary": summary, "cautions": cautions, "period": period}
