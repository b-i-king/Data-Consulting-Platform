"""
eda-report — an Exploratory Data Analysis (EDA) report for the client (Data Exploration & Analysis).

Python implementation of README.md in this folder (written for this platform;
the section flow follows the classic EDA walk-through in GeeksforGeeks'
"EDA - Exploratory Data Analysis in Python").

The report reads top to bottom like a consultant's first look at a dataset.
Every section has a HEADLINE, charts, and a plain-English explanation written
from THIS data (not boilerplate):

    Data Overview          rows, columns, column types, dates covered
    Summary Statistics     count / mean / std / min / quartiles / max per number column
    Missing Values         % empty per column
    Duplicate Values       exact duplicate rows, repeated IDs
    Data Distributions     histograms + skewness in words
    Univariate Analysis    top values of category columns
    Bivariate Analysis     main metric by the most useful category (box plot) + strongest pair (scatter)
    Multivariate Analysis  correlation heatmap
    Outliers               box plots with the 1.5 × IQR rule
    Key Takeaways          3–6 bullet points

A section that can't apply (e.g. no category columns) stays in the report with
a short "Not applicable because ..." note, so every report has the same shape.

The result keeps the usual SkillResult shape (summary, findings, tables, a flat
`charts` list) so every existing screen still works, and adds
`data["report"]` (the sections, drawn by web/components/explore/EdaReport.tsx)
and `artifacts["html_report"]` (the same report as one downloadable HTML file,
rendered by data_skills/_report.py).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from data_skills._core import (
    SkillResult, SkillSpec, _ID_NAME, as_datetime, bar_chart, box_chart, box_stats, categorical_columns,
    date_columns, fmt_num, heatmap_chart, histogram_chart, numeric_columns, records, scatter_chart,
)
from data_skills._report import render_html_report

SKILL = SkillSpec(
    id="eda-report",
    title="EDA report",
    description="A full exploratory data analysis of your table: overview, statistics, missing values, "
                "duplicates, distributions, relationships and outliers, each with charts and a "
                "plain-English explanation. Downloadable as an HTML report.",
)

MAX_HISTOGRAMS = 6      # number columns charted in "Data Distributions"
MAX_CATEGORIES = 4      # category columns charted in "Univariate Analysis"
MAX_GROUPS = 8          # boxes in the bivariate box plot
MAX_POINTS = 500        # dots in the scatter plot (a random sample of rows)
MAX_HEATMAP = 10        # columns in the correlation heatmap
MAX_OUTLIER_BOXES = 6   # box plots in "Outliers"

# Words that hint at what a table is about (checked against column names).
_TOPICS = [
    (("amount", "price", "revenue", "sales", "quantity", "order", "invoice"), "sales or transactions"),
    (("expense", "cost", "vendor", "spend", "bill"), "expenses or spending"),
    (("rating", "csat", "nps", "feedback", "review", "survey"), "customer feedback or survey responses"),
    (("salary", "employee", "hire", "department", "payroll"), "employees or payroll"),
    (("customer", "email", "phone", "address", "signup"), "customers"),
    (("inventory", "stock", "sku", "warehouse"), "inventory"),
]
# Preferred "main metric" name parts, best first ("amount" beats "price").
_METRIC_WORDS = ("amount", "revenue", "sales", "total", "profit", "value", "spend", "cost", "price", "score")


# ── Small helpers ────────────────────────────────────────────────────────────


def _us_date(ts: pd.Timestamp) -> str:
    """
    Format a date the US way for report text: MM/DD/YYYY.

    Args:
        ts: the date.

    Returns:
        str: e.g. "10/13/2025".

    Example:
        >>> _us_date(pd.Timestamp("2025-10-13"))
        '10/13/2025'
    """
    return ts.strftime("%m/%d/%Y")


def _pct(part: int | float, whole: int | float) -> str:
    """
    Format part / whole as a plain percentage with one decimal ("12.5%"); 0 rows -> "0%".

    Args:
        part: the count of interest.
        whole: the total.

    Returns:
        str: the percentage text.

    Example:
        >>> _pct(1, 8)
        '12.5%'
    """
    return "0%" if not whole else f"{part / whole * 100:.1f}%"


def _n(count: int, word: str) -> str:
    """
    A count with its noun, pluralized the simple English way: "1 row", "2 rows".

    Args:
        count: how many.
        word: the singular noun.

    Returns:
        str: e.g. "18,210 rows".

    Example:
        >>> _n(1, "row"), _n(1234, "column")
        ('1 row', '1,234 columns')
    """
    return f"{fmt_num(count)} {word}{'' if count == 1 else 's'}"


def _strength(r: float) -> str:
    """
    Describe a correlation coefficient in words (same cut-offs as correlation-analysis).

    Args:
        r: correlation between -1 and 1.

    Returns:
        str: "strong", "moderate", "weak" or "negligible".

    Example:
        >>> _strength(-0.8)
        'strong'
    """
    a = abs(r)
    return "strong" if a >= 0.7 else "moderate" if a >= 0.4 else "weak" if a >= 0.2 else "negligible"


def _usable_numbers(df: pd.DataFrame) -> list[str]:
    """
    Number columns (IDs excluded) that have at least two different values.

    Constant columns can't have a distribution, a correlation or outliers, so
    the chart sections skip them (the report mentions them instead).

    Args:
        df: the table.

    Returns:
        list[str]: column names in table order.

    Example:
        >>> _usable_numbers(pd.DataFrame({"a": [1, 2], "b": [5, 5]}))
        ['a']
    """
    return [c for c in numeric_columns(df) if df[c].nunique(dropna=True) >= 2]


def _main_metric(cols: list[str]) -> str | None:
    """
    Pick the column the report treats as the main metric.

    Name parts are tried in priority order (amount, revenue, sales, total, profit,
    value, spend, cost, price, score), so "amount" wins over "unit_price" wherever
    the columns sit; with no match, the first number column is used.

    Args:
        cols: candidate number columns.

    Returns:
        str | None: the chosen column, or None when there are none.

    Example:
        >>> _main_metric(["quantity", "amount"])
        'amount'
    """
    for word in _METRIC_WORDS:
        for c in cols:
            if word in c.lower():
                return c
    return cols[0] if cols else None


def _column_kind(df: pd.DataFrame, col: str, dates: set[str], metrics: set[str], cats: set[str]) -> str:
    """
    Sort one column into a plain-English type for the overview.

    Args:
        df: the table.
        col: the column name.
        dates: date columns (from date_columns).
        metrics: measure columns (from numeric_columns, IDs excluded).
        cats: grouping columns (from categorical_columns).

    Returns:
        str: "Date", "True/false", "Number", "Identifier", "Category" or "Text".

    Example:
        >>> df = pd.DataFrame({"amount": [1.5, 2.0]})
        >>> _column_kind(df, "amount", set(), {"amount"}, set())
        'Number'
    """
    s = df[col]
    if col in dates:
        return "Date"
    if pd.api.types.is_bool_dtype(s):
        return "True/false"
    if pd.api.types.is_numeric_dtype(s):
        return "Number" if col in metrics else "Identifier"
    if col in cats:
        return "Category"
    if _ID_NAME.search(col) or s.nunique(dropna=True) > 0.9 * max(len(s), 1):
        return "Identifier"
    return "Text"


def _skew_words(col: str, s: pd.Series) -> str:
    """
    Describe a number column's shape in one sentence, from its skewness.

    Skewness near 0 means roughly symmetric; above +0.5 a long tail of large values
    (right-skewed); below -0.5 a long tail of small values (left-skewed). Over 1 in
    either direction counts as strong.

    Args:
        col: column name (used in the sentence).
        s: the column's values (NaN ignored).

    Returns:
        str: e.g. "amount is strongly right-skewed (skewness 2.10): most values are small, with
        a long tail of a few large ones, so the average (28.40) sits above the typical value
        (median 21)."

    Example:
        >>> _skew_words("x", pd.Series([1, 2, 3, 4, 5])).startswith("x is roughly symmetric")
        True
    """
    s = s.dropna()
    sk = float(s.skew()) if len(s) >= 3 else float("nan")
    mean, med = float(s.mean()), float(s.median())
    if np.isnan(sk):
        return f"{col} has too few values to judge its shape (average {fmt_num(mean)})."
    if abs(sk) < 0.5:
        return (f"{col} is roughly symmetric (skewness {sk:.2f}): values spread evenly around the middle, "
                f"so the average ({fmt_num(mean)}) and the typical value (median {fmt_num(med)}) are close.")
    strong = "strongly" if abs(sk) >= 1 else "moderately"
    if sk > 0:
        return (f"{col} is {strong} right-skewed (skewness {sk:.2f}): most values are small, with a long tail "
                f"of a few large ones, so the average ({fmt_num(mean)}) sits above the typical value "
                f"(median {fmt_num(med)}).")
    return (f"{col} is {strong} left-skewed (skewness {sk:.2f}): most values are large, with a tail of a few "
            f"small ones, so the average ({fmt_num(mean)}) sits below the typical value (median {fmt_num(med)}).")


def _section(sid: str, title: str, explanation: str, bullets: list[str] | None = None,
             charts: list[dict] | None = None, table: str | None = None, applicable: bool = True) -> dict:
    """
    Build one report section in the shape the web app and the HTML renderer expect.

    Args:
        sid: short id, e.g. "missing_values".
        title: the headline, e.g. "Missing Values".
        explanation: the plain-English paragraph under the headline.
        bullets: optional bullet points.
        charts: optional chart specs.
        table: optional name of a table in SkillResult.tables to show in this section.
        applicable: False when the section could not apply to this data.

    Returns:
        dict: {"id", "title", "explanation", "bullets", "charts", "table", "applicable"}.

    Example:
        >>> _section("x", "Data Overview", "Hi")["bullets"]
        []
    """
    return {"id": sid, "title": title, "explanation": explanation, "bullets": bullets or [],
            "charts": charts or [], "table": table, "applicable": applicable}


# ── The sections, one function each ─────────────────────────────────────────


def _overview(df: pd.DataFrame, kinds: dict[str, str], dates: list[str]) -> dict:
    """
    "Data Overview": size, column types (chart), the likely topic and the dates covered.

    Args:
        df: the table.
        kinds: column -> plain type (from _column_kind).
        dates: date columns.

    Returns:
        dict: the section (see _section).

    Example:
        >>> _overview(pd.DataFrame({"a": [1]}), {"a": "Number"}, [])["title"]
        'Data Overview'
    """
    n, k = df.shape
    counts = pd.Series(kinds).value_counts()
    type_text = ", ".join(f"{v} {t.lower()} column{'s' if v != 1 else ''}" for t, v in counts.items())
    names = " ".join(df.columns.astype(str)).lower()
    topic = next((label for words, label in _TOPICS if any(w in names for w in words)), None)
    expl = f"The table has {fmt_num(n)} row{'s' if n != 1 else ''} and {k} column{'s' if k != 1 else ''}: {type_text}."
    if topic:
        expl += f" Judging by the column names, it appears to be about {topic}."
    bullets = []
    for c in dates[:2]:
        d = as_datetime(df[c]).dropna()
        if len(d):
            days = (d.max() - d.min()).days
            bullets.append(f"{c} covers {_us_date(d.min())} to {_us_date(d.max())} ({fmt_num(days)} days).")
    ids = [c for c, t in kinds.items() if t == "Identifier"]
    for c in ids[:1]:
        uniq = df[c].nunique(dropna=True)
        grain = "one row per " + c if uniq == n else f"{fmt_num(uniq)} distinct {c} values across {fmt_num(n)} rows"
        bullets.append(f"Identifier column {c}: {grain}.")
    if not dates:
        bullets.append("No date column was found, so the report can't say which period the data covers.")
    chart = bar_chart("Columns by type", counts.index.tolist(), counts.values.tolist(), "columns")
    return _section("overview", "Data Overview", expl, bullets, [chart], table="column_types")


def _summary_stats(df: pd.DataFrame, nums: list[str], tables: dict) -> dict:
    """
    "Summary Statistics": the describe() table for number columns, explained.

    Args:
        df: the table.
        nums: measure columns (IDs excluded).
        tables: SkillResult.tables, filled with "summary_statistics".

    Returns:
        dict: the section.

    Example:
        >>> t = {}
        >>> _summary_stats(pd.DataFrame({"a": [1, 2, 3]}), ["a"], t)["table"]
        'summary_statistics'
    """
    if not nums:
        return _section("summary_statistics", "Summary Statistics",
                        "Not applicable because the table has no number columns to summarize "
                        "(ID-like columns such as order numbers are not treated as measures).", applicable=False)
    desc = df[nums].describe().T
    rows = []
    for c, r in desc.iterrows():
        rows.append({"column": c, "count": int(r["count"]), "mean": r["mean"], "std": r["std"], "min": r["min"],
                     "25%": r["25%"], "50% (median)": r["50%"], "75%": r["75%"], "max": r["max"]})
    tables["summary_statistics"] = records(pd.DataFrame(rows), limit=None)
    main = _main_metric(nums)
    m = desc.loc[main]
    expl = (f"This table summarizes the {len(nums)} number column{'s' if len(nums) != 1 else ''}. "
            f"Count is how many rows have a value, mean is the average, std (standard deviation) is how far values "
            f"typically sit from the average, and the 25%, 50% and 75% columns are the quartiles: a quarter of rows "
            f"fall below the 25% value, half below the median. For {main}, the typical value is "
            f"{fmt_num(m['50%'])} and the average is {fmt_num(m['mean'])}, ranging from {fmt_num(m['min'])} "
            f"to {fmt_num(m['max'])}.")
    bullets = []
    for c in nums[:6]:
        r = desc.loc[c]
        if r["min"] == r["max"]:
            bullets.append(f"{c} is the same in every row ({fmt_num(r['min'])}), so it doesn't vary at all.")
            continue
        gap = "" if not r["50%"] else (
            " The average is well above the median, a sign of a few large values pulling it up."
            if r["mean"] > 1.25 * r["50%"] and r["50%"] > 0 else "")
        bullets.append(f"{c}: median {fmt_num(r['50%'])}, average {fmt_num(r['mean'])}, middle half between "
                       f"{fmt_num(r['25%'])} and {fmt_num(r['75%'])}.{gap}")
    return _section("summary_statistics", "Summary Statistics", expl, bullets, table="summary_statistics")


def _missing(df: pd.DataFrame, tables: dict) -> tuple[dict, str | None]:
    """
    "Missing Values": % empty per column (chart of only the columns with gaps).

    Args:
        df: the table.
        tables: SkillResult.tables, filled with "missing_values" when there are gaps.

    Returns:
        tuple[dict, str | None]: the section, and a takeaway sentence (None when nothing is missing).

    Example:
        >>> sec, note = _missing(pd.DataFrame({"a": [1, None]}), {})
        >>> note.startswith("a is")
        True
    """
    pct = (df.isna().mean() * 100).round(2)
    gaps = pct[pct > 0].sort_values(ascending=False)
    if gaps.empty:
        return _section("missing_values", "Missing Values",
                        "No missing values: every cell in every column is filled in, so no gaps need explaining "
                        "or filling before analysis."), None
    tables["missing_values"] = [{"column": str(c), "missing_rows": int(df[c].isna().sum()), "missing_pct": float(v)}
                                for c, v in gaps.items()]
    worst, wv = str(gaps.index[0]), float(gaps.iloc[0])
    expl = (f"{len(gaps)} of {df.shape[1]} columns have empty cells. The chart shows the share of rows missing a "
            f"value in each of them; {worst} has the most gaps ({wv:.1f}% of rows). Missing values can mean the "
            "information was never collected, doesn't apply (for example, no customer ID for a walk-in sale), "
            "or was lost along the way, so it's worth knowing which before filling or dropping them.")
    bullets = [f"{c}: {v:.1f}% missing ({fmt_num(int(df[c].isna().sum()))} rows)." for c, v in gaps.head(6).items()]
    if (gaps > 50).any():
        bullets.append("Columns more than half empty may not be usable for analysis without another source.")
    chart = bar_chart("Missing values by column (%)", [str(c) for c in gaps.index], gaps.tolist(), "% of rows")
    return (_section("missing_values", "Missing Values", expl, bullets, [chart], table="missing_values"),
            f"{worst} is {wv:.1f}% empty, the largest gap in the table.")


def _duplicates(df: pd.DataFrame, kinds: dict[str, str]) -> tuple[dict, str | None]:
    """
    "Duplicate Values": exact duplicate rows (chart: unique vs duplicate) and repeated IDs.

    Args:
        df: the table.
        kinds: column -> plain type, used to find an identifier column.

    Returns:
        tuple[dict, str | None]: the section, and a takeaway when duplicates were found.

    Example:
        >>> sec, note = _duplicates(pd.DataFrame({"a": [1, 1]}), {"a": "Number"})
        >>> sec["bullets"][0].startswith("1 row")
        True
    """
    try:
        dup = int(df.duplicated().sum())
    except TypeError:  # unhashable cells (e.g. lists from JSON): compare their text instead
        dup = int(df.astype(str).duplicated().sum())
    n = len(df)
    chart = bar_chart("Unique vs. duplicate rows", ["Unique rows", "Duplicate rows"], [n - dup, dup], "rows")
    if dup:
        expl = (f"{fmt_num(dup)} of {fmt_num(n)} rows ({_pct(dup, n)}) are exact copies of an earlier row, the same "
                "value in every column. Duplicates inflate totals and counts; check whether they are real repeat "
                "events or the same record loaded twice before removing them.")
        bullets = [f"{fmt_num(dup)} row{'s are' if dup != 1 else ' is'} exact duplicate{'s' if dup != 1 else ''}."]
        note = f"{_n(dup, 'exact duplicate row')} ({_pct(dup, n)}) should be reviewed."
    else:
        expl = (f"None of the {fmt_num(n)} rows is an exact copy of another, so totals and counts are not inflated "
                "by repeated records. (If this table went through the platform's automatic cleaning, exact "
                "duplicates were already removed there.)")
        bullets, note = [], None
    for c in [c for c, t in kinds.items() if t == "Identifier"][:1]:
        repeats = int(df[c].dropna().duplicated().sum())
        if repeats:
            bullets.append(f"{c} repeats in {fmt_num(repeats)} rows. That is expected if one {c} can have several "
                           "lines (for example, several items in one order), but a problem if it should be unique.")
        else:
            bullets.append(f"Every {c} value is unique, so it can serve as the table's key.")
    return _section("duplicates", "Duplicate Values", expl, bullets, [chart]), note


def _distributions(df: pd.DataFrame, nums: list[str], usable: list[str]) -> tuple[dict, str | None]:
    """
    "Data Distributions": one histogram per number column (up to 6), skewness in words.

    Args:
        df: the table.
        nums: all measure columns (to mention constant ones).
        usable: measure columns with at least two different values.

    Returns:
        tuple[dict, str | None]: the section, and a takeaway about the most skewed column.

    Example:
        >>> sec, _ = _distributions(pd.DataFrame({"a": [1, 2, 3, 10]}), ["a"], ["a"])
        >>> sec["charts"][0]["type"]
        'histogram'
    """
    constant = [c for c in nums if c not in usable]
    if not usable:
        why = ("the table has no number columns" if not nums
               else f"every number column has a single value ({', '.join(constant)})")
        return _section("distributions", "Data Distributions", f"Not applicable because {why}, so there is no "
                        "spread of values to chart.", applicable=False), None
    charts, bullets, skews = [], [], {}
    for c in usable[:MAX_HISTOGRAMS]:
        s = df[c].dropna().astype(float)
        bins = int(min(20, max(5, np.sqrt(len(s)))))
        counts, edges = np.histogram(s, bins=bins)
        charts.append(histogram_chart(f"Distribution of {c}", edges.tolist(), counts.tolist(), c, "rows"))
        bullets.append(_skew_words(c, s))
        if len(s) >= 3:
            skews[c] = float(s.skew())
    for c in constant:
        bullets.append(f"{c} has the same value in every row, so it has no distribution to chart.")
    right = [c for c, v in skews.items() if v >= 0.5]
    expl = ("Each histogram groups a column's values into ranges (bins) and shows how many rows fall in each, "
            "so you can see where most values sit and how far they stretch. ")
    if right:
        expl += (f"{len(right)} of {len(charts)} charted columns ({', '.join(right)}) are right-skewed: most rows "
                 "have modest values and a few have much larger ones. For those, the median is a better "
                 "\"typical\" value than the average.")
    else:
        expl += "None of the charted columns has a strong long tail of large values."
    note = None
    if skews:
        top = max(skews, key=lambda c: abs(skews[c]))
        if abs(skews[top]) >= 1:
            side = "large" if skews[top] > 0 else "small"
            note = f"{top} is heavily skewed (a long tail of {side} values), so report its median, not just its average."
    return _section("distributions", "Data Distributions", expl, bullets, charts), note


def _univariate(df: pd.DataFrame, cats: list[str]) -> tuple[dict, str | None]:
    """
    "Univariate Analysis": top values of up to 4 category columns, one bar chart each.

    Args:
        df: the table.
        cats: grouping-friendly text columns (from categorical_columns).

    Returns:
        tuple[dict, str | None]: the section, and a takeaway about the most lopsided column.

    Example:
        >>> sec, _ = _univariate(pd.DataFrame({"s": ["A", "A", "B", "A"]}), ["s"])
        >>> sec["charts"][0]["categories"]
        ['A', 'B']
    """
    if not cats:
        return _section("univariate", "Univariate Analysis",
                        "Not applicable because the table has no category columns (text columns with a "
                        "manageable number of repeating values, such as store or product).",
                        applicable=False), None
    charts, bullets, shares = [], [], {}
    for c in cats[:MAX_CATEGORIES]:
        vc = df[c].value_counts()
        top = vc.head(10)
        charts.append(bar_chart(f"Most common values of {c}", [str(v) for v in top.index], top.tolist(), "rows"))
        share = vc.iloc[0] / vc.sum()
        shares[c] = share
        line = (f"{c}: {vc.size} distinct values; the most common is {vc.index[0]} "
                f"({_pct(vc.iloc[0], vc.sum())} of rows)")
        if vc.size > 1:
            line += f", the least common is {vc.index[-1]} ({_pct(vc.iloc[-1], vc.sum())})"
        bullets.append(line + ".")
    expl = ("Univariate analysis looks at one column at a time. Each bar chart counts how many rows have each "
            "value (top 10 shown), which shows how the business is split, for example which store or product "
            "appears most often. ")
    lopsided = max(shares, key=shares.get)
    if shares[lopsided] >= 0.6:
        expl += (f"{lopsided} is dominated by a single value ({_pct(shares[lopsided], 1)} of rows), so comparisons "
                 "across its other values rest on few rows.")
    else:
        expl += "No single value dominates any of these columns, so comparisons across groups are well supported."
    top_c = cats[0]
    vc0 = df[top_c].value_counts()
    note = f"In {top_c}, {vc0.index[0]} is the most common value ({_pct(vc0.iloc[0], vc0.sum())} of rows)."
    return _section("univariate", "Univariate Analysis", expl, bullets, charts), note


def _best_category(df: pd.DataFrame, metric: str, cats: list[str]) -> str | None:
    """
    Pick the category column that best separates the main metric (highest eta squared).

    Eta squared = variance between group averages / total variance: the share of the
    metric's variation explained by the groups. Columns with more than 20 groups are skipped.

    Args:
        df: the table.
        metric: the main number column.
        cats: candidate category columns.

    Returns:
        str | None: the best column, or None when no candidate works.

    Example:
        >>> df = pd.DataFrame({"g": ["a", "a", "b", "b"], "h": ["x", "y", "x", "y"], "v": [1, 1, 9, 9]})
        >>> _best_category(df, "v", ["h", "g"])
        'g'
    """
    best, best_eta = None, -1.0
    sub_all = df[[metric]].dropna()
    total = float(((sub_all[metric] - sub_all[metric].mean()) ** 2).sum())
    if total == 0:
        return cats[0] if cats else None
    for c in cats:
        if df[c].nunique(dropna=True) > 20:
            continue
        sub = df[[c, metric]].dropna()
        g = sub.groupby(c, observed=True)[metric]
        between = float((g.count() * (g.mean() - sub[metric].mean()) ** 2).sum())
        eta = between / total
        if eta > best_eta:
            best, best_eta = c, eta
    return best


def _bivariate(df: pd.DataFrame, usable: list[str], cats: list[str]) -> tuple[dict, str | None]:
    """
    "Bivariate Analysis": main metric by the most useful category (box plot) and the
    most strongly correlated pair of number columns (scatter, sampled to 500 rows).

    Args:
        df: the table.
        usable: number columns with at least two different values.
        cats: category columns.

    Returns:
        tuple[dict, str | None]: the section, and a takeaway about the group difference.

    Example:
        >>> df = pd.DataFrame({"g": ["a", "b"] * 5, "v": range(10), "w": range(10)})
        >>> [c["type"] for c in _bivariate(df, ["v", "w"], ["g"])[0]["charts"]]
        ['box', 'scatter']
    """
    charts, bullets, parts, note = [], [], [], None
    metric = _main_metric(usable)
    cat = _best_category(df, metric, cats) if metric and cats else None
    if metric and cat:
        top_groups = df[cat].value_counts().head(MAX_GROUPS).index
        boxes = [box_stats(str(g), df.loc[df[cat] == g, metric]) for g in top_groups]
        # Highest median first; ties broken by the upper quartile so the order is stable and meaningful.
        boxes = sorted([b for b in boxes if b], key=lambda b: (b["median"], b["q3"]), reverse=True)
        charts.append(box_chart(f"{metric} by {cat}", boxes, metric, cat))
        hi, lo = boxes[0], boxes[-1]
        parts.append(f"The box plot compares {metric} across the {len(boxes)} most common values of {cat} (the "
                     "column that separates it best). Each box spans the middle half of the rows, the line inside "
                     "is the median, and dots are unusually high or low values.")
        if len(boxes) > 1 and hi["median"] == lo["median"]:
            bullets.append(f"The typical {metric} is the same in every {cat} group (median {fmt_num(hi['median'])}); "
                           "differences show only in the spread of the boxes.")
        elif len(boxes) > 1:
            bullets.append(f"{hi['name']} has the highest typical {metric} (median {fmt_num(hi['median'])}); "
                           f"{lo['name']} the lowest (median {fmt_num(lo['median'])}).")
            note = (f"{metric} differs by {cat}: {hi['name']} has the highest median ({fmt_num(hi['median'])}), "
                    f"{lo['name']} the lowest ({fmt_num(lo['median'])}).")
    elif metric:
        bullets.append("No category column was found to split the main metric by, so there is no box plot.")
    pair = None
    if len(usable) >= 2:
        corr = df[usable].corr(method="pearson")
        best = 0.0
        for i, a in enumerate(usable):
            for b in usable[i + 1:]:
                r = corr.loc[a, b]
                if pd.notna(r) and abs(r) > abs(best):
                    best, pair = float(r), (a, b)
    if pair:
        a, b = pair
        sub = df[[a, b]].dropna()
        if len(sub) > MAX_POINTS:
            sub = sub.sample(MAX_POINTS, random_state=0)  # fixed seed: the same report every run
        charts.append(scatter_chart(f"{a} vs. {b}", sub[a].tolist(), sub[b].tolist(), a, b))
        direction = "rise together" if best > 0 else "move in opposite directions"
        parts.append(f"The scatter plot shows the most strongly related pair of number columns, {a} and {b} "
                     f"(one dot per row{', a sample of ' + str(MAX_POINTS) if len(df) > MAX_POINTS else ''}).")
        bullets.append(f"{a} and {b} {direction}: Pearson r = {best:+.2f}, a {_strength(best)} straight-line "
                       "relationship.")
    elif len(usable) < 2:
        bullets.append("A scatter plot needs two number columns that vary; this table has "
                       f"{len(usable)}, so it is skipped.")
    if not charts:
        return _section("bivariate", "Bivariate Analysis",
                        "Not applicable because bivariate analysis compares two columns at a time and this table "
                        "lacks a number column with a category to split it by, or a second number column.",
                        bullets, applicable=False), None
    expl = "Bivariate analysis looks at two columns together to see how one changes with the other. " + " ".join(parts)
    return _section("bivariate", "Bivariate Analysis", expl, bullets, charts), note


def _multivariate(df: pd.DataFrame, usable: list[str], tables: dict) -> tuple[dict, str | None]:
    """
    "Multivariate Analysis": Pearson correlation heatmap of up to 10 number columns.

    Args:
        df: the table.
        usable: number columns with at least two different values.
        tables: SkillResult.tables, filled with "correlations".

    Returns:
        tuple[dict, str | None]: the section, and a takeaway about the strongest pair.

    Example:
        >>> sec, _ = _multivariate(pd.DataFrame({"a": [1, 2, 3], "b": [2, 4, 7]}), ["a", "b"], {})
        >>> sec["charts"][0]["type"]
        'heatmap'
    """
    cols = usable[:MAX_HEATMAP]
    corr = df[cols].corr(method="pearson") if len(cols) >= 2 else None
    if corr is None or corr.isna().all().all() or len(df) < 3:
        return _section("multivariate", "Multivariate Analysis",
                        "Not applicable because a correlation matrix needs at least two number columns that "
                        "vary and at least three rows.", applicable=False), None
    tables["correlations"] = [{"column": i, **{c: (None if pd.isna(v) else round(float(v), 3)) for c, v in row.items()}}
                              for i, row in corr.iterrows()]
    pairs = [(a, b, float(corr.loc[a, b])) for i, a in enumerate(cols) for b in cols[i + 1:]
             if pd.notna(corr.loc[a, b])]
    pos = max(pairs, key=lambda p: p[2]) if pairs else None
    neg = min(pairs, key=lambda p: p[2]) if pairs else None
    chart = heatmap_chart("Correlation between number columns (Pearson r)", cols, cols,
                          [[None if pd.isna(v) else round(float(v), 3) for v in row] for row in corr.values])
    expl = (f"The heatmap shows the correlation between every pair of the {len(cols)} number columns at once. "
            "Values run from -1 to +1: darker warm cells mean the two columns rise together, darker cool cells "
            "mean one falls as the other rises, and pale cells mean little straight-line relationship. ")
    bullets, note = [], None
    if pos and pos[2] > 0:
        bullets.append(f"Strongest positive pair: {pos[0]} and {pos[1]} (r = {pos[2]:+.2f}, {_strength(pos[2])}).")
        if abs(pos[2]) >= 0.4:
            note = f"{pos[0]} and {pos[1]} move together (r = {pos[2]:+.2f})."
    if neg and neg[2] <= -0.2:
        bullets.append(f"Strongest negative pair: {neg[0]} and {neg[1]} (r = {neg[2]:+.2f}, {_strength(neg[2])}).")
    else:
        bullets.append("No pair moves in opposite directions to any meaningful degree (no r below -0.20).")
    strong = [p for p in pairs if abs(p[2]) >= 0.9]
    if strong:
        bullets.append("Pairs above 0.9 may hold nearly the same information (for example, a total computed from "
                       "the others): " + ", ".join(f"{a} / {b}" for a, b, _ in strong[:3]) + ".")
    expl += ("Correlation is not causation: two columns can move together because a third factor drives both, "
             "so treat strong pairs as leads to investigate, not proof.")
    return _section("multivariate", "Multivariate Analysis", expl, bullets, [chart], table="correlations"), note


def _outliers(df: pd.DataFrame, usable: list[str], tables: dict) -> tuple[dict, str | None]:
    """
    "Outliers": a box plot per number column (up to 6) with 1.5 × IQR fences, and counts.

    Args:
        df: the table.
        usable: number columns with at least two different values.
        tables: SkillResult.tables, filled with "outliers".

    Returns:
        tuple[dict, str | None]: the section, and a takeaway about the column with the most outliers.

    Example:
        >>> sec, note = _outliers(pd.DataFrame({"a": [1, 2, 3, 4, 100]}), ["a"], {})
        >>> sec["bullets"][0].startswith("a: 1 value")
        True
    """
    if not usable:
        return _section("outliers", "Outliers", "Not applicable because the table has no number columns that "
                        "vary, and outliers are unusually high or low numbers.", applicable=False), None
    rows, charts, bullets = [], [], []
    for c in usable:
        b = box_stats(c, df[c])
        below = int((df[c] < b["lower_fence"]).sum())
        above = int((df[c] > b["upper_fence"]).sum())
        n = int(df[c].notna().sum())
        rows.append({"column": c, "q1": b["q1"], "q3": b["q3"], "iqr": b["q3"] - b["q1"],
                     "lower_fence": b["lower_fence"], "upper_fence": b["upper_fence"],
                     "below": below, "above": above, "outlier_pct": round((below + above) / max(n, 1) * 100, 2)})
        if len(charts) < MAX_OUTLIER_BOXES:
            charts.append(box_chart(f"Outliers in {c}", [b], c))
    tables["outliers"] = records(pd.DataFrame(rows), limit=None)
    flagged = sorted([r for r in rows if r["below"] + r["above"]], key=lambda r: r["outlier_pct"], reverse=True)
    for r in flagged[:6]:
        cnt = r["below"] + r["above"]
        bullets.append(f"{r['column']}: {fmt_num(cnt)} value{'s' if cnt != 1 else ''} ({r['outlier_pct']}%) outside "
                       f"{fmt_num(r['lower_fence'])} to {fmt_num(r['upper_fence'])}"
                       f" ({fmt_num(r['above'])} high, {fmt_num(r['below'])} low).")
    expl = ("An outlier is a value far from the rest. Using the 1.5 × IQR rule, the interquartile range (IQR) is the "
            "spread of the middle half of the values; anything more than 1.5 IQRs below the 25th percentile or "
            "above the 75th is flagged and drawn as a dot beyond the whiskers. ")
    if flagged:
        expl += (f"{len(flagged)} of {len(usable)} number columns have flagged values. Outliers may be data-entry "
                 "errors or real but rare events (a large catering order, a holiday rush), so check them with "
                 "someone who knows the business rather than removing them automatically.")
        note = (f"{flagged[0]['column']} has the most outliers ({flagged[0]['outlier_pct']}% of rows beyond the "
                "1.5 × IQR fences); check them before relying on its averages.")
    else:
        expl += "No values fall outside the fences in any number column, so no extreme values stand out."
        note = None
    return _section("outliers", "Outliers", expl, bullets, charts, table="outliers"), note


# ── The skill ────────────────────────────────────────────────────────────────


def run(df: pd.DataFrame) -> SkillResult:
    """
    Build the full EDA report: ten headlined sections, each with charts and a plain-English explanation.

    Steps (each one a section, in this order): Data Overview, Summary Statistics,
    Missing Values, Duplicate Values, Data Distributions, Univariate Analysis,
    Bivariate Analysis, Multivariate Analysis, Outliers, Key Takeaways. A section
    that can't apply to this table explains why instead of being dropped. The
    key takeaways (3 to 6) are collected from the other sections' most notable result.

    Args:
        df: pd.DataFrame, the (cleaned) table to explore.

    Returns:
        SkillResult: summary (size plus the first takeaway); findings = the key takeaways;
        tables "column_types", "summary_statistics", "missing_values", "correlations",
        "outliers" (when they apply); charts = every chart in report order;
        data["report"] = {"title", "subtitle", "sections": [...]};
        artifacts["html_report"] = the same report as a self-contained HTML page.

    Example:
        >>> res = run(pd.read_csv("samples/sales.csv"))
        >>> [s["title"] for s in res.data["report"]["sections"]][:2]
        ['Data Overview', 'Summary Statistics']
    """
    n, k = df.shape
    dates = date_columns(df)
    nums = numeric_columns(df)
    usable = _usable_numbers(df)
    cats = [c for c in categorical_columns(df) if c not in dates]
    kinds = {str(c): _column_kind(df, str(c), set(dates), set(nums), set(cats)) for c in df.columns}
    tables: dict[str, list[dict]] = {
        "column_types": [{"column": str(c), "type": kinds[str(c)], "dtype": str(df[c].dtype),
                          "distinct": int(df[c].nunique(dropna=True)),
                          "missing_pct": round(float(df[c].isna().mean() * 100), 2)} for c in df.columns],
    }

    sections = [_overview(df, kinds, dates), _summary_stats(df, nums, tables)]
    notes: list[str] = []
    for builder in (lambda: _missing(df, tables), lambda: _duplicates(df, kinds),
                    lambda: _distributions(df, nums, usable), lambda: _univariate(df, cats),
                    lambda: _bivariate(df, usable, cats), lambda: _multivariate(df, usable, tables),
                    lambda: _outliers(df, usable, tables)):
        section, note = builder()
        sections.append(section)
        if note:
            notes.append(note)

    # Key takeaways: the size first, then the most notable result of each section (3–6 bullets).
    takeaways = [f"The table has {_n(n, 'row')} and {_n(k, 'column')} "
                 f"({len(nums)} number, {len(cats)} category, {len(dates)} date)."]
    takeaways += notes[:5]
    if not any("missing" in t or "empty" in t for t in takeaways) and not df.isna().any().any():
        takeaways.append("There are no missing values, so every column can be used as is.")
    if len(takeaways) < 3:
        takeaways.append("Next step: run the other Exploration & Analysis skills (trends, correlations, "
                         "hypothesis tests) on the columns highlighted above.")
    if len(takeaways) < 3:
        takeaways.append("With this little numeric or categorical data, collecting more rows or columns would "
                         "make deeper analysis possible.")
    takeaways = takeaways[:6]
    sections.append(_section(
        "takeaways", "Key Takeaways",
        "The most important points from this report, in plain English. Each one comes from a section above.",
        takeaways))

    report = {"title": "Exploratory Data Analysis (EDA) Report",
              "subtitle": f"{_n(n, 'row')} × {_n(k, 'column')} · sections follow the classic EDA flow",
              "sections": sections}
    charts = [c for s in sections for c in s["charts"]]
    summary = f"EDA report for {_n(n, 'row')} × {_n(k, 'column')}. " + (
        notes[0] if notes else "No gaps, duplicates, strong skews or outliers stood out.")
    result = SkillResult(skill=SKILL.id, summary=summary, findings=takeaways, tables=tables, charts=charts,
                         data={"report": report})
    # Render the HTML from the JSON-safe copy so NaN/numpy values never reach the page.
    safe = result.to_dict()
    result.artifacts["html_report"] = render_html_report(safe["data"]["report"], safe["tables"])
    return result
