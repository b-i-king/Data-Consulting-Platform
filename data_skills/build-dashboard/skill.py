"""
build-dashboard — time-aware KPI dashboard (Data Visualization).

Python implementation of SKILL.md in this folder, extended with Copilot
Money-style time views (see data_skills/_time.py):

  * Date ranges: Last 4 weeks · Last 3 months · Last year · Month to date ·
    Year to date · All time — each compared with the period before it.
  * Comparisons for every line chart: day over day (DoD), week over week (WoW),
    month over month (MoM).

EVERYTHING IS CALCULATED IN ONE PASS
------------------------------------
The result holds every range × every comparison, so the web app (and the
HTML file this skill writes) can switch views instantly without sending the
data again — the server never keeps client data, so a re-request would mean
re-uploading the file.

OUTPUT
------
data.ranges[<range>]           window dates, KPI cards, line series per comparison
data.breakdown[<range>]        top categories of `dimension_column` vs. previous period
artifacts["html_dashboard"]    a self-contained interactive HTML dashboard
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from data_skills import _time
from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, as_datetime, categorical_columns,
    date_columns, fmt_num, fmt_pct, json_safe, line_chart, numeric_columns, require_column,
)

ROWS = "__rows__"          # the built-in "number of records" metric
MAX_DAILY_POINTS = 400     # skip day-over-day charts on windows longer than this

SKILL = SkillSpec(
    id="build-dashboard",
    title="Build dashboard",
    description="KPI cards and line charts with date ranges (last 4 weeks … year to date) "
                "and day/week/month-over-period comparisons.",
    params={
        "date_column": Param("column", None, "Date column (auto-detected if empty)"),
        "value_columns": Param("columns", None, "Number columns to chart (default: up to 3 detected)"),
        "dimension_column": Param("column", None, "Category column for a breakdown (e.g. product, store)"),
        "agg": Param("choice", "sum", "How to combine values in a period", ("sum", "mean", "count")),
        "range": Param("choice", "last_3_months", "Default range shown", tuple(_time.RANGES)),
        "comparison": Param("choice", "auto", "Default comparison", ("auto", *(_time.COMPARISONS))),
        "as_of": Param("str", None, "End date for ranges, YYYY-MM-DD (default: latest date in the data)"),
        "title": Param("str", "Dashboard", "Dashboard title"),
    },
)


ACRONYMS = {"csat", "nps", "sku", "id", "roi", "usd", "kpi", "aov", "ltv"}


def _label(col: str) -> str:
    """'unit_price' -> 'Unit Price'; 'csat' -> 'CSAT'."""
    if col == ROWS:
        return "Number of records"
    return " ".join(w.upper() if w.lower() in ACRONYMS else w.capitalize() for w in col.replace("_", " ").split())


def run(df: pd.DataFrame, date_column=None, value_columns=None, dimension_column=None, agg="sum",
        range="last_3_months", comparison="auto", as_of=None, title="Dashboard") -> SkillResult:
    date_col = require_column(df, date_column, "date", date_columns(df))
    data = df.copy()
    data[date_col] = as_datetime(data[date_col])
    data = data.dropna(subset=[date_col])
    if data.empty:
        raise SkillInputError(f"No usable dates in '{date_col}'.")

    averaged: set[str] = set()
    if value_columns:
        values = value_columns
    else:
        values = default_metrics(data, agg)
        if not values and agg == "sum":
            # Nothing sensible to add up (e.g. survey scores): chart averages instead.
            values = default_metrics(data, "mean")
            averaged = set(values)
    for v in values:
        if v not in data.columns or not pd.api.types.is_numeric_dtype(data[v]):
            raise SkillInputError(f"'{v}' is not a number column.")
    data[ROWS] = 1                      # summing this counts records (e.g. orders)
    metrics = [*values, ROWS]
    metric_agg = {m: ("sum" if m == ROWS else "mean" if m in averaged else agg) for m in metrics}
    labels = {m: ("Average " if metric_agg[m] == "mean" and m != ROWS else "") + _label(m) for m in metrics}

    first, last = data[date_col].min(), data[date_col].max()
    anchor = pd.Timestamp(as_of) if as_of else last
    if comparison == "auto":
        # Granularity suited to the range on screen: 3 months -> weekly points, 1 year -> monthly.
        dw = _time.window(range, anchor, first)
        default_cmp = _time.best_comparison(dw.start, dw.end)
    else:
        default_cmp = comparison

    if dimension_column is None:
        cats = [c for c in categorical_columns(data, max_unique=30) if c != date_col]
        dimension_column = cats[0] if cats else None

    ranges: dict[str, dict] = {}
    breakdown: dict[str, list] = {}
    for key in _time.RANGES:
        w = _time.window(key, anchor, first)
        kpis = {m: _time.window_totals(data, date_col, [m], w, metric_agg[m])[m] for m in metrics}
        span_days = (w.end - w.start).days + 1
        series = {}
        for cmp_key in _time.COMPARISONS:
            if cmp_key == "dod" and span_days > MAX_DAILY_POINTS:
                continue
            series[cmp_key] = {m: _range_series(data, date_col, m, cmp_key, w, metric_agg[m]) for m in metrics}
        ranges[key] = {"window": w.as_dict(), "kpis": kpis, "series": series}

        if dimension_column and values:
            breakdown[key] = _breakdown(data, date_col, dimension_column, values[0], w, metric_agg[values[0]])

    # Headline: the default range, first value column.
    head_metric = values[0] if values else ROWS
    k = ranges[range]["kpis"][head_metric]
    w = ranges[range]["window"]
    summary = (f"{labels[head_metric]} for {_time.RANGES[range].lower()} ({w['start']} to {w['end']}): "
               f"{fmt_num(k['current'])}")
    if k.get("pct_change") is not None:
        summary += f", {fmt_pct(k['pct_change'])} vs. the previous period."

    findings = []
    for key in ("last_4_weeks", "month_to_date", "year_to_date"):
        kk = ranges[key]["kpis"][head_metric]
        if kk.get("pct_change") is not None:
            findings.append(f"{_time.RANGES[key]}: {fmt_num(kk['current'])} ({fmt_pct(kk['pct_change'])} vs. "
                            f"{'the same days last month' if key == 'month_to_date' else 'the same days last year' if key == 'year_to_date' else 'the prior 4 weeks'}).")
    cmp_rows = ranges[range]["series"].get(default_cmp, {}).get(head_metric, [])
    if len(cmp_rows) >= 2:
        latest = cmp_rows[-1] if not cmp_rows[-1]["partial"] else cmp_rows[-2]
        if latest.get("pct_change") is not None:
            findings.append(f"Latest full {_time.COMPARISONS[default_cmp][2]} ({latest['period']}): "
                            f"{fmt_num(latest['value'])}, {fmt_pct(latest['pct_change'])} "
                            f"{_time.COMPARISONS[default_cmp][0].lower()}.")

    chart_rows = ranges[range]["series"].get(default_cmp, {}).get(head_metric, [])
    chart = line_chart(f"{labels[head_metric]} — {_time.COMPARISONS[default_cmp][0]}",
                       [r["period"] for r in chart_rows],
                       {"This period": [r["value"] for r in chart_rows],
                        "Previous period": [r["prior_range"] for r in chart_rows]})

    payload = json_safe({
        "title": title, "date_column": date_col, "metrics": metrics,
        "metric_labels": labels, "dimension_column": dimension_column,
        "as_of": anchor.date().isoformat(), "first_date": first.date().isoformat(),
        "default_range": range, "default_comparison": default_cmp,
        "range_labels": _time.RANGES,
        "comparison_labels": {k: v[0] for k, v in _time.COMPARISONS.items()},
        "ranges": ranges, "breakdown": breakdown,
    })
    html = (Path(__file__).with_name("dashboard_template.html").read_text(encoding="utf-8")
            .replace("__TITLE__", title).replace("__DATA__", json.dumps(payload).replace("</", "<\\/")))

    return SkillResult(
        skill=SKILL.id, summary=summary, findings=findings, charts=[chart],
        tables={f"kpis_{range}": [{"metric": labels[m], **v} for m, v in ranges[range]["kpis"].items()]},
        artifacts={"html_dashboard": html}, data=payload,
    )


def _range_series(data, date_col, metric, cmp_key, w, agg) -> list[dict]:
    """
    Rows for one chart. Each row has:
      value        this period (day / week / month) inside the selected range
      previous     the period just before it   -> day/week/month-over-period %
      prior_range  the matching period of the PREVIOUS range, by position
                   (day 1 of the prior 4 weeks under day 1 of these 4 weeks,
                   Jan last year under Jan this year...) -> the comparison line
    """
    cur = _time.period_series(data, date_col, metric, cmp_key, w.start, w.end, agg)
    cur["prior_range"] = None
    cur["prior_period"] = None
    if w.prev_start is not None:
        prev = _time.period_series(data, date_col, metric, cmp_key, w.prev_start, w.prev_end, agg)
        n = min(len(cur), len(prev))
        cur.loc[cur.index[:n], "prior_range"] = prev["value"].iloc[:n].to_numpy()
        cur.loc[cur.index[:n], "prior_period"] = prev["period"].iloc[:n].dt.date.astype(str).to_numpy()
    cur["period"] = cur["period"].dt.date.astype(str)
    return cur.to_dict(orient="records")


def default_metrics(df: pd.DataFrame, agg: str) -> list[str]:
    """
    Pick up to 3 columns worth charting, best first:
      1. money totals   (amount, revenue, sales, total, cost, spend, balance)
      2. counts         (quantity, units, count, orders)
      3. other numbers
    When adding up (agg="sum"), per-unit or rate columns are skipped — the
    sum of unit prices, ratings, or percentages means nothing.
    """
    import re
    money = re.compile(r"amount|revenue|sales|total|cost|spend|balance|income|expense|pay", re.I)
    count = re.compile(r"qty|quantity|units|count|orders|visits", re.I)
    per_unit = re.compile(r"price|rate|pct|percent|score|rating|csat|nps|avg|average|age", re.I)
    cols = [c for c in numeric_columns(df) if not (agg == "sum" and per_unit.search(c) and not money.search(c.replace("price", "")))]
    rank = lambda c: 0 if money.search(c) else 1 if count.search(c) else 2  # noqa: E731
    return sorted(cols, key=rank)[:3]


def _breakdown(data, date_col, dim, value, w, agg) -> list[dict]:
    """Top 8 categories in the window, with their previous-period value."""
    cur = data[(data[date_col] >= w.start) & (data[date_col] < w.end + pd.Timedelta(days=1))]
    out = cur.groupby(dim, dropna=True)[value].agg(agg).sort_values(ascending=False).head(8)
    prev = pd.Series(dtype=float)
    if w.prev_start is not None:
        p = data[(data[date_col] >= w.prev_start) & (data[date_col] < w.prev_end + pd.Timedelta(days=1))]
        prev = p.groupby(dim, dropna=True)[value].agg(agg)
    rows = []
    for name, val in out.items():
        pv = prev.get(name)
        rows.append({"category": str(name), "current": float(val),
                     "previous": None if pv is None or pd.isna(pv) else float(pv),
                     "pct_change": None if not pv else (val - pv) / abs(pv)})
    return rows
