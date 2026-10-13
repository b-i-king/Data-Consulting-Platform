"""
build-dashboard — KPI dashboard driven by transaction TYPES (Data Visualization).

Python implementation of SKILL.md in this folder, extended with Copilot
Money-style time views (see data_skills/_time.py).

HOW THE DASHBOARD IS BUILT
--------------------------
1. KPIs come from a "type" column, not from picking metrics.
   Transaction data almost always has a column saying what KIND of record
   each row is: event_type / transaction_type in the platform's warehouse
   (db/warehouse.sql), order_type in a point-of-sale export, and so on.
   The 3–4 types with the MOST rows become the KPIs (e.g. In-store, Online
   pickup, Delivery, Catering). Each KPI gets its own card and line chart,
   showing the type's total of the money column (or its count, if there is
   no money column).
       No type column? The dashboard falls back to the best number columns
       (money first, then counts) as the KPIs.
2. "Transactions over time": every row counted, plus the overall total.
3. Filters are about WHO, not WHICH METRIC: customer type, segment, age
   group, gender, region, store... whatever such columns the data has.
   Choosing a filter re-runs this skill on the matching rows only.
4. Every range × comparison is calculated in one pass, so switching
       Last 4 weeks · Last 3 months · Last year · Month to date · Year to date · All time
       Day over day · Week over week · Month over month
   is instant (the server never keeps client data, so asking again would mean
   re-uploading the file). The KPI list is ranked on ALL rows, so it stays
   the same when a filter is applied.

OUTPUT
------
data.kpis                     metric ids of the KPI cards (one per top type)
data.overview                 metric ids of the overview: total value, transactions
data.ranges[<range>]          window dates, KPI numbers, line series per comparison
data.filter_options           {column: [values]} for the filter dropdowns
data.breakdown[<range>]       top categories of `dimension_column` vs. previous period
artifacts["html_dashboard"]   a self-contained interactive HTML dashboard (include_html=True)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from data_skills import _time
from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, as_datetime, categorical_columns,
    date_columns, fmt_num, fmt_pct, json_safe, line_chart, numeric_columns, require_column,
)

ROWS = "__rows__"          # every row counted ("Transactions")
TOTAL = "__total__"        # the value column over all rows ("Total amount")
MAX_DAILY_POINTS = 400     # skip day-over-day charts on windows longer than this
MAX_FILTERS = 4            # filter dropdowns offered
MAX_FILTER_VALUES = 12     # values per filter dropdown (most common first)

SKILL = SkillSpec(
    id="build-dashboard",
    title="Build dashboard",
    description="KPI cards for the most common transaction types, each with a line chart, "
                "transactions over time, customer/demographic filters, date ranges "
                "(last 4 weeks … year to date) and day/week/month-over-period comparisons.",
    params={
        "date_column": Param("column", None, "Date column (auto-detected if empty)"),
        "kpi_column": Param("column", None, "Column whose most common values become the KPIs "
                                           "(e.g. transaction_type; auto-detected)"),
        "value_column": Param("column", None, "Number to total for each KPI (default: the money column; "
                                             "none = count transactions)"),
        "top_kpis": Param("int", 4, "How many KPIs (types) to show, 1–6"),
        "filters": Param("json", None, 'Only use matching rows, e.g. {"customer_type": "Wholesale"} '
                                       'or {"age_group": ["25–34", "35–44"]}'),
        "filter_columns": Param("columns", None, "Columns offered as filters (default: customer and "
                                                "demographic columns, auto-detected)"),
        "value_columns": Param("columns", None, "KPIs when there is no type column: number columns to chart "
                                               "(default: up to 3 detected)"),
        "dimension_column": Param("column", None, "Category column for a breakdown (e.g. product)"),
        "agg": Param("choice", "sum", "How to combine values in a period", ("sum", "mean", "count")),
        "range": Param("choice", "last_3_months", "Default range shown", tuple(_time.RANGES)),
        "comparison": Param("choice", "auto", "Default comparison", ("auto", *(_time.COMPARISONS))),
        "as_of": Param("str", None, "End date for ranges, YYYY-MM-DD (default: latest date in the data)"),
        "title": Param("str", "Dashboard", "Dashboard title"),
        "include_html": Param("bool", True, "Also build the downloadable HTML dashboard (the web app "
                                            "asks for it only when you click Download)"),
    },
)

ACRONYMS = {"csat", "nps", "sku", "id", "roi", "usd", "kpi", "aov", "ltv"}

# Column names that say "what kind of record is this?" (best first).
KPI_EXACT = ("event_type", "transaction_type", "txn_type", "type", "event_subtype", "record_type",
             "entry_type", "activity_type")
KPI_SUFFIX = re.compile(r"(_type|_kind|_class|_category)$", re.I)
KPI_LOOSE = re.compile(r"^(category|kind|class)$", re.I)

# Column names that describe WHO the customer is, or WHERE — used as filters.
# Three groups, best first: customer groupings, demographics, place.
FILTER_GROUPS = (
    re.compile(r"customer|client|member|segment|tier|loyal|cohort|persona", re.I),
    re.compile(r"gender|sex|(^|_)age($|_)|age_?(group|range|band|bracket)|generation|income|education|"
               r"ethnic|race|marital|household|language|demograph", re.I),
    re.compile(r"region|state|city|county|zip|postal|country|location|store|branch|channel|market", re.I),
)


def _label(col: str) -> str:
    """'unit_price' -> 'Unit Price'; 'csat' -> 'CSAT'."""
    return " ".join(w.upper() if w.lower() in ACRONYMS else w.capitalize() for w in str(col).replace("_", " ").split())


def _is_demographic(col: str) -> bool:
    return any(g.search(str(col)) for g in FILTER_GROUPS)


def find_kpi_column(df: pd.DataFrame, exclude: set[str]) -> str | None:
    """The column whose values are record TYPES (2–50 distinct values), or None."""
    def ok(c):
        n = df[c].nunique(dropna=True)
        return c not in exclude and not pd.api.types.is_numeric_dtype(df[c]) and 2 <= n <= 50
    cols = [str(c) for c in df.columns]
    by_lower = {c.lower(): c for c in cols}
    for name in KPI_EXACT:
        if name in by_lower and ok(by_lower[name]):
            return by_lower[name]
    for c in cols:   # order_type, payment_kind... but not customer_type (that's WHO, a filter)
        if KPI_SUFFIX.search(c) and not _is_demographic(c) and ok(c):
            return c
    for c in cols:
        if KPI_LOOSE.search(c) and ok(c):
            return c
    return None


def find_filter_columns(df: pd.DataFrame, exclude: set[str]) -> list[str]:
    """Customer / demographic / place columns with 2–30 values, best groups first."""
    cats = [c for c in categorical_columns(df, max_unique=30) if c not in exclude]
    out: list[str] = []
    for group in FILTER_GROUPS:
        out += [c for c in cats if group.search(c) and c not in out]
    return out[:MAX_FILTERS]


def default_metrics(df: pd.DataFrame, agg: str) -> list[str]:
    """
    Pick up to 3 columns worth charting, best first:
      1. money totals   (amount, revenue, sales, total, cost, spend, balance)
      2. counts         (quantity, units, count, orders)
      3. other numbers
    When adding up (agg="sum"), per-unit or rate columns are skipped — the
    sum of unit prices, ratings, or percentages means nothing.
    """
    money = re.compile(r"amount|revenue|sales|total|cost|spend|balance|income|expense|pay", re.I)
    count = re.compile(r"qty|quantity|units|count|orders|visits", re.I)
    per_unit = re.compile(r"price|rate|pct|percent|score|rating|csat|nps|avg|average|age", re.I)
    cols = [c for c in numeric_columns(df) if not (agg == "sum" and per_unit.search(c) and not money.search(c.replace("price", "")))]
    rank = lambda c: 0 if money.search(c) else 1 if count.search(c) else 2  # noqa: E731
    return sorted(cols, key=rank)[:3]


def _apply_filters(data: pd.DataFrame, filters: dict | None) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """Keep rows matching every filter. Values are compared as text."""
    applied: dict[str, list[str]] = {}
    if not filters:
        return data, applied
    if not isinstance(filters, dict):
        raise SkillInputError('Filters must look like {"customer_type": "Wholesale"}.')
    mask = pd.Series(True, index=data.index)
    for col, wanted in filters.items():
        if col not in data.columns:
            raise SkillInputError(f"Filter column '{col}' was not found.")
        values = [str(v) for v in (wanted if isinstance(wanted, list) else [wanted]) if v not in (None, "")]
        if values:
            mask &= data[col].astype("string").isin(values).fillna(False)
            applied[col] = values
    out = data[mask]
    if out.empty:
        raise SkillInputError("No rows match those filters.")
    return out, applied


def run(df: pd.DataFrame, date_column=None, kpi_column=None, value_column=None, top_kpis=4, filters=None,
        filter_columns=None, value_columns=None, dimension_column=None, agg="sum",
        range="last_3_months", comparison="auto", as_of=None, title="Dashboard",
        include_html=True) -> SkillResult:
    date_col = require_column(df, date_column, "date", date_columns(df))
    data = df.copy()
    data[date_col] = as_datetime(data[date_col])
    data = data.dropna(subset=[date_col])
    if data.empty:
        raise SkillInputError(f"No usable dates in '{date_col}'.")
    top_kpis = max(1, min(int(top_kpis), 6))

    # ── Which columns play which role ───────────────────────────────────────
    if value_column:
        require_column(data, value_column, "value", [])
        if not pd.api.types.is_numeric_dtype(data[value_column]):
            raise SkillInputError(f"'{value_column}' is not a number column.")
    averaged = False
    if value_column is None and agg != "count":
        found = default_metrics(data, agg)
        if not found and agg == "sum":
            found = default_metrics(data, "mean")   # only scores (e.g. CSAT): average them
            averaged = bool(found)
        value_column = found[0] if found else None
    value_agg = "mean" if averaged else agg
    value_label = (("Average " if value_agg == "mean" else "") + _label(value_column)) if value_column else "Transactions"

    if kpi_column:
        require_column(data, kpi_column, "KPI", [])
    elif not value_columns:
        kpi_column = find_kpi_column(data, exclude={date_col})
    if filter_columns:
        for c in filter_columns:
            require_column(data, c, "filter", [])
    else:
        filter_columns = find_filter_columns(data, exclude={date_col, kpi_column or ""})

    # Filter dropdown choices come from ALL rows, so they don't shrink as you filter.
    filter_options = {c: [str(v) for v in data[c].dropna().astype(str).value_counts().head(MAX_FILTER_VALUES).index]
                      for c in filter_columns}

    # KPIs (types) are ranked on ALL rows too, so the cards don't jump around.
    labels: dict[str, str] = {ROWS: "Transactions" if kpi_column else "Number of records"}
    metric_agg: dict[str, str] = {ROWS: "sum"}
    kpi_values: list[str] = []
    if kpi_column:
        counts = data[kpi_column].dropna().astype(str).value_counts()
        kpi_values = list(counts.head(top_kpis).index)

    data, applied = _apply_filters(data, filters)

    kpis: list[str] = []
    if kpi_column:
        kinds = data[kpi_column].astype("string")
        measure = data[value_column].astype(float) if value_column else pd.Series(1.0, index=data.index)
        for i, v in enumerate(kpi_values):
            m = f"__kpi_{i}__"
            # The type's value on its own rows, NaN elsewhere (so averages stay right).
            data[m] = measure.where(kinds == v)
            labels[m] = v
            metric_agg[m] = value_agg if value_column else "sum"
            kpis.append(m)
    else:
        averaged = False
        values = value_columns or default_metrics(data, agg)
        if not values and agg == "sum":
            values = default_metrics(data, "mean")   # only scores: chart their averages
            averaged = bool(values)
        for v in values:
            if v not in data.columns or not pd.api.types.is_numeric_dtype(data[v]):
                raise SkillInputError(f"'{v}' is not a number column.")
            metric_agg[v] = "mean" if averaged else agg
            labels[v] = ("Average " if metric_agg[v] == "mean" else "") + _label(v)
            kpis.append(v)
        value_column = value_column if value_column in values else (values[0] if values else None)

    # Added only now, so the automatic metric picking above never chooses it.
    data[ROWS] = 1.0                      # summing this counts transactions
    overview = [ROWS]
    if kpi_column and value_column:
        data[TOTAL] = data[value_column].astype(float)
        labels[TOTAL] = ("Average " if value_agg == "mean" else "Total ") + _label(value_column).lower()
        metric_agg[TOTAL] = value_agg
        overview = [TOTAL, ROWS]
    metrics = [*kpis, *[m for m in overview if m not in kpis]]

    # ── Time windows ────────────────────────────────────────────────────────
    first, last = data[date_col].min(), data[date_col].max()
    anchor = pd.Timestamp(as_of) if as_of else last
    if comparison == "auto":
        # Granularity suited to the range on screen: 3 months -> weekly points, 1 year -> monthly.
        dw = _time.window(range, anchor, first)
        default_cmp = _time.best_comparison(dw.start, dw.end)
    else:
        default_cmp = comparison

    if dimension_column is None:
        cats = [c for c in categorical_columns(data, max_unique=30)
                if c not in {date_col, kpi_column, *filter_columns}]
        dimension_column = cats[0] if cats else None
    breakdown_metric = TOTAL if TOTAL in metrics else (kpis[0] if kpis and not kpi_column else ROWS)

    ranges: dict[str, dict] = {}
    breakdown: dict[str, list] = {}
    for key in _time.RANGES:
        w = _time.window(key, anchor, first)
        kpi_numbers = {m: _time.window_totals(data, date_col, [m], w, metric_agg[m])[m] for m in metrics}
        span_days = (w.end - w.start).days + 1
        series = {}
        for cmp_key in _time.COMPARISONS:
            if cmp_key == "dod" and span_days > MAX_DAILY_POINTS:
                continue
            series[cmp_key] = {m: _range_series(data, date_col, m, cmp_key, w, metric_agg[m]) for m in metrics}
        ranges[key] = {"window": w.as_dict(), "kpis": kpi_numbers, "series": series}
        if dimension_column:
            breakdown[key] = _breakdown(data, date_col, dimension_column, breakdown_metric, w, metric_agg[breakdown_metric])

    # ── Summary and findings (plain English) ───────────────────────────────
    w = ranges[range]["window"]
    head = kpis[0] if kpis else ROWS
    k = ranges[range]["kpis"][head]
    lead = (f"Top {len(kpis)} {_label(kpi_column).lower()} KPIs: {', '.join(labels[m] for m in kpis)}. "
            if kpi_column else "")
    summary = (f"{lead}{labels[head]}{' (' + value_label.lower() + ')' if kpi_column else ''} for "
               f"{_time.RANGES[range].lower()} ({w['start']} to {w['end']}): {fmt_num(k['current'])}")
    if k.get("pct_change") is not None:
        summary += f", {fmt_pct(k['pct_change'])} vs. the previous period."
    if applied:
        summary += " Filtered to " + "; ".join(f"{_label(c)} = {', '.join(v)}" for c, v in applied.items()) + "."

    findings = []
    for m in kpis:
        kk = ranges[range]["kpis"][m]
        if kk.get("pct_change") is not None:
            findings.append(f"{labels[m]}: {fmt_num(kk['current'])} {'(' + value_label.lower() + ') ' if kpi_column else ''}"
                            f"{fmt_pct(kk['pct_change'])} vs. the previous {_time.RANGES[range].lower()}.")
    overall = overview[0]
    for key in ("month_to_date", "year_to_date"):
        kk = ranges[key]["kpis"][overall]
        if kk.get("pct_change") is not None:
            same = "the same days last month" if key == "month_to_date" else "the same days last year"
            findings.append(f"{labels[overall]}, {_time.RANGES[key].lower()}: {fmt_num(kk['current'])} "
                            f"({fmt_pct(kk['pct_change'])} vs. {same}).")
    cmp_rows = ranges[range]["series"].get(default_cmp, {}).get(ROWS, [])
    if len(cmp_rows) >= 2:
        latest = cmp_rows[-1] if not cmp_rows[-1]["partial"] else cmp_rows[-2]
        if latest.get("pct_change") is not None:
            findings.append(f"Transactions in the latest full {_time.COMPARISONS[default_cmp][2]} ({latest['period']}): "
                            f"{fmt_num(latest['value'], 0)}, {fmt_pct(latest['pct_change'])} "
                            f"{_time.COMPARISONS[default_cmp][0].lower()}.")

    chart_rows = ranges[range]["series"].get(default_cmp, {}).get(ROWS, [])
    chart = line_chart(f"{labels[ROWS]} over time — {_time.COMPARISONS[default_cmp][0]}",
                       [r["period"] for r in chart_rows],
                       {"This period": [r["value"] for r in chart_rows],
                        "Previous period": [r["prior_range"] for r in chart_rows]})

    payload = json_safe({
        "title": title, "date_column": date_col,
        "mode": "kpi_types" if kpi_column else "metrics",
        "kpi_column": kpi_column, "kpi_column_label": _label(kpi_column) if kpi_column else None,
        "value_column": value_column, "value_label": value_label,
        "kpis": kpis, "overview": overview, "metrics": metrics, "metric_labels": labels,
        "metric_agg": metric_agg,
        "filter_columns": filter_columns, "filter_labels": {c: _label(c) for c in filter_columns},
        "filter_options": filter_options, "filters": applied, "rows": int(len(data)),
        "dimension_column": dimension_column, "breakdown_metric": breakdown_metric,
        "as_of": anchor.date().isoformat(), "first_date": first.date().isoformat(),
        "default_range": range, "default_comparison": default_cmp,
        "range_labels": _time.RANGES,
        "comparison_labels": {k: v[0] for k, v in _time.COMPARISONS.items()},
        # Used when the time bar doesn't fit on one line (phones): 4W, 3M, 1Y, MTD, YTD; DoD, WoW, MoM.
        "range_short_labels": _time.RANGE_SHORT,
        "comparison_short_labels": _time.COMPARISON_SHORT,
        "ranges": ranges, "breakdown": breakdown,
    })
    artifacts = {}
    if include_html:
        artifacts["html_dashboard"] = (
            Path(__file__).with_name("dashboard_template.html").read_text(encoding="utf-8")
            .replace("__TITLE__", title).replace("__DATA__", json.dumps(payload).replace("</", "<\\/")))

    return SkillResult(
        skill=SKILL.id, summary=summary, findings=findings, charts=[chart],
        tables={f"kpis_{range}": [{"kpi": labels[m], **v} for m, v in ranges[range]["kpis"].items()]},
        artifacts=artifacts, data=payload,
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
    # Round so the JSON stays small (cents are plenty; % to 0.01%).
    for col in ("value", "previous", "change", "prior_range"):
        cur[col] = pd.to_numeric(cur[col], errors="coerce").round(2)
    cur["pct_change"] = cur["pct_change"].round(4)
    return cur.to_dict(orient="records")


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
                     "pct_change": None if not pv or pd.isna(pv) else (val - pv) / abs(pv)})
    return rows
