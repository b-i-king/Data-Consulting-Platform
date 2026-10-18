"""
build-dashboard — KPI dashboard driven by transaction TYPES (Data Visualization).

Python implementation of README.md in this folder, extended with Copilot
Money-style time views (see data_skills/_time.py).

HOW THE DASHBOARD IS BUILT
--------------------------
1. KPIs come from a "type" column, not from picking metrics.
   Transaction data almost always has a column saying what KIND of record
   each row is: event_type / transaction_type in the platform's stored data
   (db/schema.sql), order_type in a point-of-sale export, and so on.
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

5. ABOVE THE FOLD, then containers (layout.py, templates/layouts.json —
   built from the Canva design): a KPI row of 1–4 headline cards (the main
   total, the INDUSTRY KPIs this data supports — industry.py reads
   industry_metrics.json — transactions, average per transaction), then rows
   of containers: trends as line charts first, categories as horizontal bar
   charts next, then complementary visuals. No fixed template: the rows are
   mixed and matched for the data.
6. Each metric has a direction (higher or lower is better) and a format
   (number, currency, percent, days), so the site colors a change green
   (good), gray (about the same) or red (bad).

OUTPUT
------
data.kpis                     metric ids of the KPI cards (one per top type)
data.overview                 metric ids of the overview: total value, transactions
data.ranges[<range>]          window dates, KPI numbers, line series per comparison
data.filter_options           {column: [values]} for the filter dropdowns
data.breakdown[<range>]       top categories of `dimension_column` vs. previous period
data.layout                   {"kpi_row": [...], "kpi_side": visual | None, "rows": [...]}
data.breakdowns[<dim>][<range>] top categories of each breakdown column (type, product, store...)
data.metric_formats / metric_directions / metric_benchmarks   per metric
data.industry                 {"sector", "name", "metrics": [...found...], "generic": bool}
artifacts["html_dashboard"]   a self-contained HTML dashboard (templates/page.html, include_html=True)
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pandas as pd

from data_skills import _time
from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, as_datetime, categorical_columns,
    date_columns, fmt_num, fmt_pct, json_safe, line_chart, numeric_columns, require_column,
)

ROWS = "__rows__"          # every row counted ("Transactions")
TOTAL = "__total__"        # the value column over all rows ("Total amount")
AVG = "__avg__"            # total ÷ transactions ("Average per transaction")
MAX_DAILY_POINTS = 400     # skip day-over-day charts on windows longer than this
MAX_FILTERS = 9            # filter dropdowns offered (the site lays them out in rows of up to 3)
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
        "filter_columns": Param("columns", None, "Columns offered as filters, up to 9 (default: customer, "
                                                "demographic and place columns, auto-detected)"),
        "value_columns": Param("columns", None, "KPIs when there is no type column: number columns to chart "
                                               "(default: up to 3 detected)"),
        "dimension_column": Param("column", None, "Category column for a breakdown (e.g. product)"),
        "agg": Param("choice", "sum", "How to combine values in a period", ("sum", "mean", "count")),
        "range": Param("choice", "last_3_months", "Default range shown", tuple(_time.RANGES)),
        "comparison": Param("choice", "auto", "Default comparison", ("auto", *(_time.COMPARISONS))),
        "as_of": Param("str", None, "End date for ranges, YYYY-MM-DD (default: latest date in the data)"),
        "title": Param("str", "At a glance", "The heading above the KPI cards"),
        "subtitle": Param("str", None, "Line under the title (default: what the dashboard covers)"),
        "industry": Param("str", None, "NAICS sector for industry KPIs, e.g. 72 or 44-45 (the site sends the "
                                      "client's industry; empty = generic KPIs only)"),
        "business_name": Param("str", None, "Business name for the header of the downloaded HTML"),
        "viewer_name": Param("str", None, "Name for the downloaded HTML's greeting (\"Hello! Ana\")"),
        "kpi_style": Param("choice", "auto", "KPI card style for the whole row: auto, number (just the number), "
                                            "change (number + change vs. before) or gauge (0–100% dials for the "
                                            "percentage KPIs only)", ("auto", "number", "change", "gauge")),
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


def _sibling(name: str):
    """Load a module that sits next to this file (the folder name has a dash, so no normal import)."""
    key = f"data_skills_build_dashboard_{name}"
    if key not in sys.modules:
        spec = importlib.util.spec_from_file_location(key, Path(__file__).with_name(f"{name}.py"))
        module = importlib.util.module_from_spec(spec)
        sys.modules[key] = module
        spec.loader.exec_module(module)
    return sys.modules[key]


industry = _sibling("industry")
layout = _sibling("layout")
MONEY = re.compile(r"amount|revenue|sales|total|cost|spend|balance|income|expense|pay|price|fee", re.I)
COSTLY = re.compile(r"cost|expense|spend|refund|return|loss|discount|churn|complaint|defect|waste", re.I)


def _label(col: str) -> str:
    """
    Turn a column name into a display label: 'unit_price' -> 'Unit Price'; 'csat' -> 'CSAT'.

    Underscores become spaces, each word is capitalized, and words in ACRONYMS
    (csat, nps, sku, id, roi, usd, kpi, aov, ltv) are written in capitals.

    Args:
        col: the column name.

    Returns:
        str: the label.

    Example:
        >>> _label("avg_csat")
        'Avg CSAT'
    """
    return " ".join(w.upper() if w.lower() in ACRONYMS else w.capitalize() for w in str(col).replace("_", " ").split())


def _is_demographic(col: str) -> bool:
    """
    Tell whether a column name describes WHO the customer is or WHERE (a filter column).

    True when the name matches any FILTER_GROUPS pattern: customer groupings
    (customer, segment, tier...), demographics (gender, age group, income...) or
    place (region, state, store, channel...).

    Args:
        col: the column name.

    Returns:
        bool: True if the name looks like a customer, demographic or place column.

    Example:
        >>> _is_demographic("customer_type"), _is_demographic("order_type")
        (True, False)
    """
    return any(g.search(str(col)) for g in FILTER_GROUPS)


def find_kpi_column(df: pd.DataFrame, exclude: set[str]) -> str | None:
    """
    Find the column whose values are record TYPES (2–50 distinct values), or None.

    Search order, first usable column wins:
    1. exact names in KPI_EXACT (event_type, transaction_type, type, ...), any case
    2. names ending in _type, _kind, _class or _category that are NOT customer,
       demographic or place columns (customer_type is a filter, order_type is a KPI)
    3. plain names category, kind or class
    A column is usable when it is not in `exclude`, is not numeric, and has 2–50
    distinct values.

    Args:
        df: the table.
        exclude: column names that can't be the KPI column (e.g. the date column).

    Returns:
        str | None: the KPI column name, or None if the data has no type column.

    Example:
        >>> find_kpi_column(pd.read_csv("samples/sales.csv"), exclude={"order_date"})
        'order_type'
    """
    def ok(c):
        """
        Check whether a column can serve as the KPI (type) column.

        Args:
            c: the column name.

        Returns:
            bool: True if `c` is not excluded, is not numeric, and has 2–50 distinct values.
        """
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
    """
    Find customer / demographic / place columns with 2–30 values, best groups first.

    Candidates come from `categorical_columns(max_unique=30)`; customer groupings are
    listed first, then demographics, then place, and at most MAX_FILTERS (9) are kept.

    Args:
        df: the table.
        exclude: columns that can't be filters (e.g. the date and KPI columns).

    Returns:
        list[str]: filter column names, best first.

    Example:
        >>> find_filter_columns(pd.read_csv("samples/sales.csv"), exclude={"order_date", "order_type"})
        ['customer_type', 'age_group', 'store']
    """
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

    Args:
        df: the table.
        agg: how values will be combined ("sum", "mean" or "count").

    Returns:
        list[str]: up to 3 number column names (may be empty).

    Example:
        >>> default_metrics(pd.read_csv("samples/sales.csv"), "sum")
        ['amount', 'quantity']
    """
    money = re.compile(r"amount|revenue|sales|total|cost|spend|balance|income|expense|pay|fee|charge", re.I)
    count = re.compile(r"qty|quantity|units|count|orders|visits|hours|minutes|days", re.I)
    is_money = lambda c: bool(money.search(c)) and not count.search(c)   # "total_hours" is a count, not dollars  # noqa: E731
    per_unit = re.compile(r"price|rate|pct|percent|score|rating|csat|nps|avg|average|age", re.I)
    cols = [c for c in numeric_columns(df) if not (agg == "sum" and per_unit.search(c) and not money.search(c.replace("price", "")))]
    rank = lambda c: 0 if is_money(c) else 1 if count.search(c) else 2  # noqa: E731
    return sorted(cols, key=rank)[:3]


def _apply_filters(data: pd.DataFrame, filters: dict | None) -> tuple[pd.DataFrame, dict[str, list[str]]]:
    """
    Keep rows matching every filter. Values are compared as text.

    Each filter is {column: value} or {column: [values]}; a row must match one of the
    values in every column given. Empty values (None or "") are ignored.

    Args:
        data: the table.
        filters: e.g. {"customer_type": "Wholesale"} or {"age_group": ["25–34", "35–44"]};
            None or {} keeps every row.

    Returns:
        tuple[pd.DataFrame, dict[str, list[str]]]: the matching rows, and the filters
        actually applied as {column: [values as text]}.

    Raises:
        SkillInputError: `filters` is not a dict, names a column that doesn't exist,
            or no rows match.

    Example:
        >>> rows, applied = _apply_filters(df, {"customer_type": "Wholesale"})
        >>> applied
        {'customer_type': ['Wholesale']}
    """
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
        range="last_3_months", comparison="auto", as_of=None, title="At a glance",
        include_html=True, subtitle=None, industry=None, business_name=None, viewer_name=None,
        kpi_style="auto") -> SkillResult:
    """
    Build the KPI dashboard: cards per top transaction type, time ranges, comparisons and filters.

    Steps:
    1. Parse the date column and drop rows without a date.
    2. Choose roles: the value column (given, else the best money/count column; scores
       such as CSAT are averaged), the KPI (type) column, and the filter columns.
    3. Rank the top `top_kpis` types on ALL rows, then apply `filters`, so the cards
       stay the same when a filter is chosen. With no type column, number columns
       (`value_columns` or auto-picked) become the KPIs.
    4. Add "Transactions" (row count) and, in type mode, the overall total.
    5. For every range in _time.RANGES, compute KPI numbers vs. the previous window,
       chart series for each supported comparison (day over day is skipped on windows
       longer than MAX_DAILY_POINTS days) and a breakdown of `dimension_column`.
    6. Pick the default range/comparison from what the data supports, write the
       summary and findings, and (if `include_html`) render templates/page.html (render_html).

    Args:
        df: the transaction table.
        date_column: date column (default: auto-detected).
        kpi_column: column whose most common values become the KPIs (default: auto-detected).
        value_column: number to total for each KPI (default: the money column; none = count transactions).
        top_kpis: how many KPI types to show, clamped to 1–6 (default 4).
        filters: only use matching rows, e.g. {"customer_type": "Wholesale"}.
        filter_columns: columns offered as filters, up to 9 (default: auto-detected).
        value_columns: KPIs when there is no type column (default: up to 3 detected).
        dimension_column: category column for the breakdown (default: first other category column).
        agg: "sum" (default), "mean" or "count".
        range: default range shown (default "last_3_months"; falls back if the data is too short).
        comparison: "auto" (default), "dod", "wow" or "mom".
        as_of: end date for the ranges, YYYY-MM-DD (default: the latest date in the data).
        title: the heading above the KPI cards (default "At a glance"; not the tab name, so they never repeat).
        include_html: also build the self-contained HTML dashboard (default True).

    Returns:
        SkillResult: summary and findings in plain English; one line chart of
        transactions over time; table "kpis_<range>"; artifacts["html_dashboard"] when
        requested; data holding the full payload (kpis, overview, ranges, breakdown,
        filter_options, available_ranges, available_comparisons, labels, ...).

    Raises:
        SkillInputError: no usable dates, a named column is missing or not numeric,
            more than 9 filter columns, bad filters, or no rows match the filters.

    Example:
        >>> df = pd.read_csv("samples/sales.csv")
        >>> result = run(df, range="last_1_year", include_html=False)
        >>> result.data["kpis"]   # e.g. ['__kpi_0__', '__kpi_1__', ...], labeled in data["metric_labels"]
    """
    industry_code = industry
    industry_mod = globals()["industry"]   # the module (the parameter has the same name)
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
        if len(filter_columns) > MAX_FILTERS:
            raise SkillInputError(f"Choose up to {MAX_FILTERS} filters.")
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

    # How each metric is computed (_totals / _frame): "sum" / "mean" of a column,
    # or "ratio" (sum ÷ sum, or ÷ a distinct count) / "distinct" (a distinct count).
    specs: dict[str, dict] = {m: {"agg": metric_agg[m], "column": m, "scale": 1} for m in metrics}
    money = bool(value_column and MONEY.search(value_column))
    formats = {m: ("currency" if money and m != ROWS else "number") for m in metrics}
    # Costs, refunds and losses going UP is bad news (red); everything else up is good (green).
    directions = {m: ("lower" if COSTLY.search(f"{m} {labels.get(m, '')}") and m != ROWS else "higher") for m in metrics}
    benchmarks: dict[str, str] = {}
    targets: dict[str, list] = {}   # metric -> [low, high] healthy band (the gauge card marks it)
    if value_column and value_agg == "sum":
        avg_num = TOTAL if TOTAL in data.columns else value_column
        specs[AVG] = {"agg": "ratio", "num": avg_num, "den": ROWS, "den_agg": "sum", "scale": 1}
        labels[AVG] = "Average per transaction"
        metric_agg[AVG] = "ratio"
        formats[AVG], directions[AVG] = ("currency" if money else "number"), "higher"
        metrics.append(AVG)
    # Industry KPIs this table can compute (industry_metrics.json); none = generic only.
    found = industry_mod.plan(data, industry_code) if industry_code else []
    for spec in found:
        m = spec["id"]
        specs[m] = {k: spec[k] for k in ("agg", "column", "num", "den", "den_agg", "scale") if k in spec}
        specs[m].setdefault("scale", 1)
        labels[m], metric_agg[m] = spec["label"], spec["agg"]
        formats[m], directions[m] = spec["format"], spec["direction"]
        if spec.get("benchmark"):
            benchmarks[m] = spec["benchmark"]
        if spec.get("target"):
            targets[m] = spec["target"]
        metrics.append(m)

    # ── Time windows ────────────────────────────────────────────────────────
    first, last = data[date_col].min(), data[date_col].max()
    anchor = pd.Timestamp(as_of) if as_of else last
    # Only offer what the data can support (see _time.available_ranges): a
    # week of data gets "Last week" + day over day; "All time" needs > 1 year.
    offered_ranges = _time.available_ranges(first, anchor)
    offered_cmps = _time.available_comparisons(first, anchor)
    if range not in offered_ranges:
        # Fall back to the longest rolling window available, else everything.
        fallback = [k for k in ("last_3_months", "last_1_year", "last_4_weeks", "last_1_week") if k in offered_ranges]
        range = fallback[0] if fallback else (offered_ranges[0] if offered_ranges else "all")
    dw = _time.window(range, anchor, first)
    range_cmps = _time.available_comparisons(first, anchor, dw.start, dw.end)
    if comparison == "auto" or comparison not in range_cmps:
        # Granularity suited to the range on screen: 3 months -> weekly points, 1 year -> monthly.
        best = _time.best_comparison(dw.start, dw.end)
        default_cmp = best if best in range_cmps else (range_cmps[-1] if range_cmps else best)
    else:
        default_cmp = comparison

    if dimension_column is None:
        cats = [c for c in categorical_columns(data, max_unique=30)
                if c not in {date_col, kpi_column, *filter_columns}]
        dimension_column = cats[0] if cats else None
    breakdown_metric = TOTAL if TOTAL in metrics else (kpis[0] if kpis and not kpi_column else ROWS)

    ranges: dict[str, dict] = {}
    breakdown: dict[str, list] = {}
    # Categories worth a horizontal bar chart each: the type column, the breakdown
    # column, then the first two filter columns (store, customer type...).
    dims = list(dict.fromkeys(c for c in (kpi_column, dimension_column, *filter_columns[:2]) if c))[:4]
    breakdowns: dict[str, dict] = {d: {} for d in dims}
    for key in _time.RANGES:
        w = _time.window(key, anchor, first)
        kpi_numbers = {m: _totals(data, date_col, specs[m], w) for m in metrics}
        span_days = (w.end - w.start).days + 1
        series = {}
        # Comparisons with at least two periods in this range (and in the data).
        for cmp_key in _time.available_comparisons(first, anchor, w.start, w.end):
            if cmp_key == "dod" and span_days > MAX_DAILY_POINTS:
                continue
            series[cmp_key] = {m: _range_series(data, date_col, specs[m], cmp_key, w) for m in metrics}
        ranges[key] = {"window": w.as_dict(), "kpis": kpi_numbers, "series": series}
        if dimension_column:
            breakdown[key] = _breakdown(data, date_col, dimension_column, breakdown_metric, w, metric_agg[breakdown_metric])
        for dim in dims:
            breakdowns[dim][key] = _breakdown(data, date_col, dim, breakdown_metric, w, metric_agg[breakdown_metric])

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
        if key not in offered_ranges:
            continue   # not enough history for a fair comparison
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

    # ── Layout: KPI row above the fold, then containers (layout.py) ─────────
    hero = TOTAL if TOTAL in metrics else (kpis[0] if kpis and not kpi_column else ROWS)
    ind_ids = [s["id"] for s in found]
    # A daily summary (one row per date): "number of records" is just the number of
    # days, and "average per transaction" the average day — neither says anything.
    summary_table = data[date_col].dt.normalize().nunique() >= 0.95 * len(data)
    per_record = [] if summary_table and hero != ROWS else [ROWS, *([AVG] if AVG in metrics else [])]
    kpi_candidates = [hero, *ind_ids, *per_record, *([] if kpi_column else kpis)]
    breakdown_label = labels[breakdown_metric]
    visuals = [{"kind": "line", "metric": hero, "title": f"{labels[hero]} over time"}]
    visuals += [{"kind": "hbar", "dimension": d, "metric": breakdown_metric,
                 "title": f"{breakdown_label} by {_label(d).lower()}"} for d in dims]
    visuals += [{"kind": "line", "metric": m, "title": f"{labels[m]} over time"} for m in ind_ids]
    if hero != ROWS and ROWS in per_record:
        visuals.append({"kind": "line", "metric": ROWS, "title": f"{labels[ROWS]} over time"})
    visuals += [{"kind": "line", "metric": m, "title": f"{labels[m]} over time"}
                for m in (kpis[:2] if kpi_column else kpis[1:3])]
    # ONE KPI card style for the whole row (layout.kpi_style): number, change or gauge.
    # Gauge rows hold only percentages; the main total still leads the containers.
    has_previous = any(ranges[k]["window"].get("prev_start") for k in offered_ranges if k in ranges)
    percent_kpis = [m for m in dict.fromkeys(kpi_candidates) if formats.get(m) == "percent"]
    card_style = layout.kpi_style(kpi_style, percent_kpis, targets, has_previous)
    if card_style == "gauge":
        kpi_candidates = percent_kpis
    dash_layout = layout.plan(kpi_candidates, visuals)
    if subtitle is None:
        subtitle = _subtitle(dash_layout["kpi_row"], labels, dims,
                             industry_mod.sector_name(industry_code) if found else None)

    payload = json_safe({
        "title": title, "subtitle": subtitle, "date_column": date_col,
        "business_name": business_name, "viewer_name": viewer_name,
        "layout": dash_layout, "breakdowns": breakdowns,
        "metric_formats": formats, "metric_directions": directions, "metric_benchmarks": benchmarks,
        "metric_targets": targets, "kpi_style": card_style,
        "industry": {"sector": industry_code, "name": industry_mod.sector_name(industry_code),
                     "metrics": [s["key"] for s in found], "sources": {s["id"]: s.get("sources", []) for s in found},
                     "generic": not found},
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
        # What the data supports: the time bar offers only these (may be empty —
        # then it's hidden). "days_of_data" counts first to last date.
        "available_ranges": offered_ranges,
        "available_comparisons": offered_cmps,
        "days_of_data": int((anchor.normalize() - first.normalize()).days + 1),
        "comparison_short_labels": _time.COMPARISON_SHORT,
        "ranges": ranges, "breakdown": breakdown,
    })
    artifacts = {}
    if include_html:
        artifacts["html_dashboard"] = render_html(payload)

    return SkillResult(
        skill=SKILL.id, summary=summary, findings=findings, charts=[chart],
        tables={f"kpis_{range}": [{"kpi": labels[m], **v} for m, v in ranges[range]["kpis"].items()]},
        artifacts=artifacts, data=payload,
    )


def _subtitle(kpi_row: list[str], labels: dict, dims: list[str], sector: str | None) -> str:
    """
    The line under the heading, written from what THIS dashboard shows (works for
    a business or an individual — it never says "your business").

    Args:
        kpi_row: metric ids in the KPI row, in order.
        labels: metric id -> label ("Total amount").
        dims: the category columns the bar charts break down by.
        sector: the NAICS sector name when industry KPIs were found, else None.

    Returns:
        str: e.g. "Total amount, transactions and average per transaction over time,
        by order type, product and 2 more"; with industry KPIs it starts
        "Accommodation and Food Services KPIs: ...".

    Example:
        >>> _subtitle(["__total__"], {"__total__": "Total amount"}, ["store"], None)
        'Total amount over time, by store'
    """
    def lower_first(s: str) -> str:
        # "Transactions" -> "transactions", but short forms stay ("KPI", "NPS").
        return s if s[1:2].isupper() else s[:1].lower() + s[1:]

    def join(items: list[str]) -> str:
        return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]

    names = [labels.get(m, m) for m in kpi_row] or ["Your numbers"]
    text = join([names[0], *map(lower_first, names[1:])]) + " over time"
    if dims:
        shown = [_label(d).lower() for d in dims[:2]]
        if len(dims) > 2:
            shown.append(f"{len(dims) - 2} more")
        text += ", by " + join(shown)
    return f"{sector} KPIs: {lower_first(text)}" if sector else text


def _totals(data: pd.DataFrame, date_col: str, spec: dict, w) -> dict:
    """
    One metric's KPI numbers for a window: current vs. previous, change, % change.

    Args:
        data: the table (date column parsed).
        date_col: the date column.
        spec: {"agg": "sum" | "mean", "column", "scale"} or {"agg": "ratio" | "distinct",
            "num", "den", "den_agg", "scale"}.
        w: the _time.Window.

    Returns:
        dict: {"current"} plus, with a previous window, {"previous", "change", "pct_change"}.

    Example:
        >>> _totals(data, "date", {"agg": "ratio", "num": "food", "den": "sales", "den_agg": "sum", "scale": 100}, w)
        {'current': 61.2, 'previous': 63.0, 'change': -1.8, 'pct_change': -0.0286}
    """
    scale = spec.get("scale", 1)
    if spec["agg"] in ("sum", "mean", "count"):
        t = _time.window_totals(data, date_col, [spec["column"]], w, spec["agg"])[spec["column"]]
        return {k: (v * scale if k != "pct_change" and v is not None else v) for k, v in t.items()}

    def value(start, end):
        mask = (data[date_col] >= start) & (data[date_col] < end + pd.Timedelta(days=1))
        den = data.loc[mask, spec["den"]].agg(spec.get("den_agg", "sum"))
        if spec["agg"] == "distinct":
            return float(den) * scale
        num = data.loc[mask, spec["num"]].sum()
        return float(num) / float(den) * scale if den else None

    cur = value(w.start, w.end)
    out = {"current": cur if cur is not None else 0.0}
    if w.prev_start is not None:
        prev = value(w.prev_start, w.prev_end)
        p = prev if prev is not None else 0.0
        out.update(previous=p, change=out["current"] - p, pct_change=((out["current"] - p) / abs(p)) if p else None)
    return out


def _frame(data: pd.DataFrame, date_col: str, spec: dict, cmp_key: str, start, end) -> pd.DataFrame:
    """
    One metric per day / week / month (columns period, value, previous, change, pct_change, partial).

    Sums and means come straight from _time.period_series; a ratio is its two
    parts per period (numerator ÷ denominator), so 30% of $100 and 50% of $10
    average to 32%, not 40%.
    """
    scale = spec.get("scale", 1)
    if spec["agg"] in ("sum", "mean", "count"):
        out = _time.period_series(data, date_col, spec["column"], cmp_key, start, end, spec["agg"])
        if scale != 1:
            for col in ("value", "previous", "change"):
                out[col] = out[col] * scale
        return out
    den = _time.period_series(data, date_col, spec["den"], cmp_key, start, end, spec.get("den_agg", "sum"))
    out = den[["period", "partial"]].copy()
    if spec["agg"] == "distinct":
        out["value"] = den["value"].astype(float) * scale
    else:
        num = _time.period_series(data, date_col, spec["num"], cmp_key, start, end, "sum")
        merged = den[["period", "value"]].merge(num[["period", "value"]], on="period", how="left", suffixes=("_d", "_n"))
        d = merged["value_d"].astype(float)
        out["value"] = (merged["value_n"].astype(float) / d.where(d != 0) * scale).to_numpy()
    out["previous"] = out["value"].shift(1)
    out["change"] = out["value"] - out["previous"]
    out["pct_change"] = out["change"] / out["previous"].abs().where(out["previous"] != 0)
    return out[["period", "value", "previous", "change", "pct_change", "partial"]].reset_index(drop=True)


def _range_series(data, date_col, spec, cmp_key, w) -> list[dict]:
    """
    Rows for one chart. Each row has:
      value        this period (day / week / month) inside the selected range
      previous     the period just before it   -> day/week/month-over-period %
      prior_range  the matching period of the PREVIOUS range, by position
                   (day 1 of the prior 4 weeks under day 1 of these 4 weeks,
                   Jan last year under Jan this year...) -> the comparison line

    Also: change, pct_change, partial, prior_period (the date of the matched prior
    period) and period as a "YYYY-MM-DD" string. Numbers are rounded (2 decimals;
    pct_change 4) to keep the JSON small.

    Args:
        data: the table, with `date_col` parsed as datetimes.
        date_col: name of the date column.
        spec: how the metric is computed (see _totals).
        cmp_key: "dod", "wow" or "mom".
        w: the _time.Window for the selected range.

    Returns:
        list[dict]: one dict per period with the keys described above.

    Example:
        >>> rows = _range_series(data, "order_date", {"agg": "sum", "column": ROWS}, "wow", w)
        >>> rows[0]["period"], rows[0]["value"], rows[0]["prior_range"]
    """
    cur = _frame(data, date_col, spec, cmp_key, w.start, w.end)
    cur["prior_range"] = None
    cur["prior_period"] = None
    if w.prev_start is not None:
        prev = _frame(data, date_col, spec, cmp_key, w.prev_start, w.prev_end)
        n = min(len(cur), len(prev))
        cur.loc[cur.index[:n], "prior_range"] = prev["value"].iloc[:n].to_numpy()
        cur.loc[cur.index[:n], "prior_period"] = prev["period"].iloc[:n].dt.date.astype(str).to_numpy()
    cur["period"] = cur["period"].dt.date.astype(str)
    # Round so the JSON stays small (cents are plenty; % to 0.01%).
    for col in ("value", "previous", "change", "prior_range"):
        cur[col] = pd.to_numeric(cur[col], errors="coerce").round(2)
    cur["pct_change"] = pd.to_numeric(cur["pct_change"], errors="coerce").round(4)
    return cur.to_dict(orient="records")


def render_html(payload: dict) -> str:
    """
    The downloadable dashboard: templates/page.html with the KPI card and container
    blocks (templates/kpi_card.html, templates/container.html) and the data filled in.

    Args:
        payload: the skill's data (everything the page draws).

    Returns:
        str: one self-contained HTML file (no outside files: it opens offline).
    """
    safe = json.dumps(payload).replace("</", "<\\/")   # a "</script>" inside the data can't end the script early
    title = (payload.get("title") or "At a glance").replace("<", "&lt;")
    return page_template().replace("__TITLE__", title).replace("__DATA__", safe)


def page_template() -> str:
    """
    templates/page.html with the KPI card and container blocks put in, but the
    title and data still blank (the __TITLE__ and __DATA__ placeholders).

    The web app downloads dashboards in the browser from a copy of this
    (web/public/dashboard-template.html, written by scripts/sync_dashboard_template.py;
    pytest fails if the copy is out of date), so a download never calls the server.

    Returns:
        str: the HTML page with two placeholders left.
    """
    folder = Path(__file__).with_name("templates")
    page = folder.joinpath("page.html").read_text(encoding="utf-8")
    blocks = "\n".join(folder.joinpath(name).read_text(encoding="utf-8") for name in ("kpi_card.html", "container.html"))
    return page.replace("__BLOCKS__", blocks)


def _breakdown(data, date_col, dim, value, w, agg) -> list[dict]:
    """
    Top 8 categories in the window, with their previous-period value.

    Args:
        data: the table, with `date_col` parsed as datetimes.
        date_col: name of the date column.
        dim: the category column to break down by.
        value: the number column to combine.
        w: the _time.Window (inclusive dates).
        agg: pandas aggregation name ("sum", "mean", ...).

    Returns:
        list[dict]: up to 8 rows, largest first: {"category", "current", "previous"
        (None if the category had no value before or there is no previous window),
        "pct_change" (None when the previous value is missing or 0)}.

    Example:
        >>> _breakdown(data, "order_date", "product", "amount", w, "sum")[0]["category"]
        # the top product in the window
    """
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
