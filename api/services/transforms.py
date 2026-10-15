"""
Transform steps and data-quality checks — "Transform data" like Power BI's
Power Query, plus the "wrong data" and "wrong format" checks.

WHAT THIS FILE DOES
-------------------
1. CHECKS (used by the cleaning report, api/services/cleaning.py):
     * outlier_check()  "wrong data": numbers FAR outside the usual range,
                        found with Tukey's "far out" fences (3 × IQR):
                            low limit  = Q1 − 3 × (Q3 − Q1)   (never below 0
                                         when the column has no negatives)
                            high limit = Q3 + 3 × (Q3 − Q1)
                        and dates in the future or before 1900. (The usual
                        box-plot rule, 1.5 × IQR, flagged ~13% of a bakery's
                        prices — its cakes — so we only flag the extremes.)
     * format_check()   "wrong format": a text column whose values are really
                        numbers ("$1,200") or dates ("2020/12/26"), and which
                        values would NOT convert (they'd become blank).
   Nothing is changed by a check — it only reports and suggests a step.

2. STEPS the client chooses (sent as `transforms`, an ordered list per table,
   like Power Query's "Applied steps"). Each one is plain pandas:

   Before column names are cleaned (they change the header row itself):
     promote_headers   "Use first row as headers"   df.columns = df.iloc[0]; df = df.iloc[1:]
     remove_top_rows   "Remove top rows" (n)        df = df.iloc[n:]

   After column names are cleaned, in the order given:
     change_type       "Change data type"           pd.to_numeric / pd.to_datetime / astype(str)
                       (values that don't convert become blank — then use
                       "Remove rows where this is blank", i.e. dropna(subset=[col]))
     replace_values    "Replace values"             df.loc[df[col] == find, col] = replace
                       (one wrong value, e.g. 450 -> 45; "" finds blanks)
     outliers          a rule for wrong data:
                         cap    -> df[col].clip(low, high)   (over 120 becomes 120)
                         remove -> drop rows outside low..high
                         blank  -> set them to missing (then fill or remove)
     unpivot           "Unpivot columns"            pd.melt  (Jan, Feb, Mar columns -> month + value rows)
     pivot             "Pivot column"               groupby + unstack (rows -> one column per value)

WHY A LIST OF STEPS: the client can see every change in order, remove one,
and the same steps re-run on the server for the preview, the dashboard, the
skills and the download (files are never stored, so the steps travel with
each request).
"""

from __future__ import annotations

import datetime as dt
import re

import numpy as np
import pandas as pd

RAW_OPS = ("promote_headers", "remove_top_rows")
OPS = ("change_type", "replace_values", "outliers", "unpivot", "pivot")
TYPES = ("number", "date", "text")
OUTLIER_METHODS = ("cap", "remove", "blank")
AGGS = ("sum", "mean", "count", "first")

IQR_FACTOR = 3.0            # Tukey's "far out" fences (1.5 = the ordinary box-plot rule)
MIN_VALUES_FOR_OUTLIERS = 10
FORMAT_SHARE = 0.8          # 80%+ of values convert -> suggest changing the type
EARLIEST_DATE = pd.Timestamp("1900-01-01")

_MONEY = re.compile(r"[$,%\s]")


# ── Checks ────────────────────────────────────────────────────────────────────

def _is_number(s: pd.Series) -> bool:
    """
    Say whether a column holds real numbers (true/false columns do not count).

    pandas treats bool columns as numeric, but averaging or range-checking
    True/False makes no sense to people, so they are excluded.

    Args:
        s: pd.Series: the column to test.

    Returns:
        bool: True for int/float columns, False for bool, text, dates, etc.

    Example:
        >>> _is_number(pd.Series([1.5, 2.0])), _is_number(pd.Series([True, False]))
        (True, False)
    """
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def _fmt(v) -> str:
    """
    Format one value for the report text, US style.

    - dates / timestamps -> "YYYY-MM-DD" (the time part is dropped)
    - floats -> thousands separators, at most 2 decimals, trailing zeros removed
    - ints -> thousands separators
    - anything else -> str(value)

    Args:
        v: the value to show (number, date, Timestamp, or anything else).

    Returns:
        str: the formatted value.

    Example:
        >>> _fmt(1234.5), _fmt(1200), _fmt(2.0), _fmt(pd.Timestamp("2024-03-01 10:00"))
        ('1,234.5', '1,200', '2', '2024-03-01')
    """
    if isinstance(v, (pd.Timestamp, dt.date)):
        return str(v)[:10]
    if isinstance(v, (float, np.floating)):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,}" if isinstance(v, (int, np.integer)) else str(v)


def outlier_check(series: pd.Series, is_id: bool = False) -> dict | None:
    """
    "Wrong data": find values far outside the usual range, or None when there are none.

    Rules:
    - Skipped (None) for ID columns and columns with fewer than
      `MIN_VALUES_FOR_OUTLIERS` (10) non-missing values.
    - Dates: anything before 1900-01-01 or after today is flagged.
    - Numbers: Tukey's "far out" fences, low = Q1 − 3 × IQR and high = Q3 + 3 × IQR
      (IQR = Q3 − Q1). If the column has no negatives, low is never below 0. If
      IQR is 0 (most values identical) nothing is flagged.
    - Text and true/false columns: None.
    Nothing is changed; the result only feeds the report and the suggested step.

    Args:
        series: pd.Series: the column to check (missing values are ignored).
        is_id: bool: True if the column looks like an ID or code; IDs are never
            checked because their size carries no meaning.

    Returns:
        dict | None: None when nothing is unusual, otherwise
            {kind ("number" | "date"), count, low, high, min, max, examples, note}
        where low/high are the limits (rounded; whole numbers for integer columns),
        min/max are formatted text, and examples are up to 3 flagged values, most
        extreme first.

    Example:
        >>> outlier_check(pd.Series([10, 12, 11, 13, 12, 11, 10, 14, 12, 13, 450]))["examples"]
        ['450']
    """
    s = series.dropna()
    if is_id or len(s) < MIN_VALUES_FOR_OUTLIERS:
        return None
    if pd.api.types.is_datetime64_any_dtype(s):
        today = pd.Timestamp.now(tz=s.dt.tz).normalize() + pd.Timedelta(days=1)
        low = EARLIEST_DATE.tz_localize(s.dt.tz) if s.dt.tz is not None else EARLIEST_DATE
        bad = s[(s < low) | (s > today)]
        if bad.empty:
            return None
        return {"kind": "date", "count": int(len(bad)), "low": _fmt(low), "high": _fmt(today - pd.Timedelta(days=1)),
                "min": _fmt(s.min()), "max": _fmt(s.max()), "examples": [_fmt(v) for v in bad.unique()[:3]],
                "note": "Dates in the future or before 1900 are usually typing mistakes."}
    if not _is_number(s):
        return None
    values = s.astype(float)
    q1, q3 = values.quantile(0.25), values.quantile(0.75)
    iqr = q3 - q1
    if iqr == 0:
        return None          # most values identical: the rule can't tell what's unusual
    low, high = q1 - IQR_FACTOR * iqr, q3 + IQR_FACTOR * iqr
    if values.min() >= 0:
        low = max(low, 0.0)  # no negatives in the data: a negative limit means nothing
    bad = values[(values < low) | (values > high)]
    if bad.empty:
        return None
    # The most extreme values first (furthest from the middle).
    mid = values.median()
    extreme = bad.reindex((bad - mid).abs().sort_values(ascending=False).index)
    whole = pd.api.types.is_integer_dtype(s)
    rnd = (lambda v: int(round(v))) if whole else (lambda v: round(float(v), 2))
    return {"kind": "number", "count": int(len(bad)), "low": rnd(low), "high": rnd(high),
            "min": _fmt(s.min()), "max": _fmt(s.max()), "examples": [_fmt(v) for v in extreme.unique()[:3]],
            "note": "Far outside the usual range (more than 3 × the interquartile range from the middle "
                    "half of the values). Check them: a typo (450 instead of 45) or a real but rare "
                    "value (a very large order)? Keep real ones."}


def _to_number(s: pd.Series) -> pd.Series:
    """
    Convert text to numbers, ignoring "$", ",", "%" and spaces: '$1,200' -> 1200.0.

    Values that still are not numbers after removing those characters become
    NaN (missing). Note '15%' becomes 15.0, not 0.15.

    Args:
        s: pd.Series: the column to convert (any dtype; it is turned into text first).

    Returns:
        pd.Series: float values, NaN where the value would not convert.

    Example:
        >>> _to_number(pd.Series(["$1,200", "15%", "abc"])).tolist()
        [1200.0, 15.0, nan]
    """
    return pd.to_numeric(s.astype(str).str.replace(_MONEY, "", regex=True), errors="coerce")


def _to_date(s: pd.Series) -> pd.Series:
    """
    Convert text to dates, accepting mixed formats; unparseable values become NaT.

    Uses `pd.to_datetime(..., format="mixed", errors="coerce")`, so each value is
    parsed on its own ("2020/12/26", "12/26/2020", "Dec 26 2020" all work).

    Args:
        s: pd.Series: the column to convert (any dtype; it is turned into text first).

    Returns:
        pd.Series: datetime64 values, NaT where the value would not convert.

    Example:
        >>> _to_date(pd.Series(["2020/12/26", "nope"])).tolist()
        [Timestamp('2020-12-26 00:00:00'), NaT]
    """
    return pd.to_datetime(s.astype(str), errors="coerce", format="mixed")


def format_check(series: pd.Series) -> dict | None:
    """
    "Wrong format": spot a text column that is mostly numbers or dates.

    Tries "number" first, then "date". A conversion is suggested when at least
    `FORMAT_SHARE` (80%) of the non-missing values convert. Details:
    - Only text (object/string) columns are checked; others return None.
    - Works on the DISTINCT values (weighted by how often each appears), so big
      columns stay fast.
    - "date" is skipped when over half the distinct values are plain numbers
      (20201226 is a number, not a date, here).
    - With more than 200 distinct values, a sample of 200 is tried first, and the
      type is skipped if under 70% of the sample converts.

    Args:
        series: pd.Series: the column to check.

    Returns:
        dict | None: {to ("number" | "date"), convertible (rows that convert),
        failing (rows that would become blank), examples (up to 3 values that
        won't convert)}, or None when the column is fine.

    Example:
        >>> format_check(pd.Series(["$1,200", "$15", "$9", "$4", "n/a"]))
        {'to': 'number', 'convertible': 4, 'failing': 1, 'examples': ['n/a']}
    """
    s = series.dropna()
    if s.empty or not (s.dtype == object or pd.api.types.is_string_dtype(s)):
        return None
    counts = s.astype(str).value_counts()
    distinct = pd.Series(counts.index, index=counts.index)
    for to, convert in (("number", _to_number), ("date", _to_date)):
        if to == "date" and _to_number(distinct).notna().mean() > 0.5:
            continue         # mostly plain numbers: not dates (20201226 is handled by "number")
        # Quick look at up to 200 distinct values first: a column of names
        # isn't dates, and parsing 100,000 names as dates is slow.
        if len(distinct) > 200 and convert(distinct.sample(200, random_state=0)).notna().mean() < FORMAT_SHARE - 0.1:
            continue
        ok = convert(distinct).notna()
        convertible = int(counts[ok].sum())
        if convertible / len(s) >= FORMAT_SHARE:
            failing = counts[~ok]
            return {"to": to, "convertible": convertible, "failing": int(failing.sum()),
                    "examples": [str(v) for v in failing.index[:3]]}
    return None


# ── Steps ─────────────────────────────────────────────────────────────────────

def validate_steps(steps) -> list[dict]:
    """
    Check the `transforms` list; raise ValueError with a plain-English message.

    Each step must be a dict whose "op" is one of `RAW_OPS` + `OPS`, and:
    - remove_top_rows needs an int "count" of 1 or more
    - change_type needs "to" in `TYPES` (number, date, text)
    - outliers needs "method" in `OUTLIER_METHODS` (cap, remove, blank)
    - pivot's "agg" (default "sum") must be in `AGGS`
    - unpivot needs a non-empty "columns" list
    Column names are NOT checked here (they are checked when the step runs).

    Args:
        steps: the `transforms` value from the request: a list of step dicts, or None.

    Returns:
        list[dict]: the same list (unchanged), or [] when `steps` is None.

    Raises:
        ValueError: if `steps` is not a list of dicts, or a step is unknown or
            missing a required setting. The message names the step number (1-based).

    Example:
        >>> validate_steps([{"op": "remove_top_rows", "count": 2}])
        [{'op': 'remove_top_rows', 'count': 2}]
        >>> validate_steps([{"op": "sort"}])
        Traceback (most recent call last):
        ValueError: Step 1: unknown step 'sort'. Use one of: promote_headers, ...
    """
    if steps is None:
        return []
    if not isinstance(steps, list) or not all(isinstance(s, dict) for s in steps):
        raise ValueError('transforms must be a list of steps, e.g. [{"op": "promote_headers"}].')
    for i, step in enumerate(steps, start=1):
        op = step.get("op")
        if op not in RAW_OPS + OPS:
            raise ValueError(f"Step {i}: unknown step '{op}'. Use one of: {', '.join(RAW_OPS + OPS)}.")
        if op == "remove_top_rows" and not (isinstance(step.get("count"), int) and step["count"] > 0):
            raise ValueError(f"Step {i}: 'Remove top rows' needs a count of 1 or more.")
        if op == "change_type" and step.get("to") not in TYPES:
            raise ValueError(f"Step {i}: change the type to one of: {', '.join(TYPES)}.")
        if op == "outliers" and step.get("method") not in OUTLIER_METHODS:
            raise ValueError(f"Step {i}: choose cap, remove or blank for unusual values.")
        if op == "pivot" and step.get("agg", "sum") not in AGGS:
            raise ValueError(f"Step {i}: combine pivoted values with one of: {', '.join(AGGS)}.")
        if op == "unpivot" and not (isinstance(step.get("columns"), list) and step["columns"]):
            raise ValueError(f"Step {i}: pick at least one column to unpivot.")
    return steps


def _retype(df: pd.DataFrame, skip: tuple[str, ...]) -> pd.DataFrame:
    """
    After headers move, turn text columns that are all numbers back into numbers.

    When the first row was the real header, pandas read every column as text
    (because of that header row). Once it is removed, a text column whose every
    non-missing value converts with `pd.to_numeric` becomes numeric again.
    Modifies `df` in place and also returns it.

    Args:
        df: pd.DataFrame: the table after "Use first row as headers".
        skip: tuple[str, ...]: column names to leave alone (columns the platform
            added, e.g. source_file).

    Returns:
        pd.DataFrame: the same DataFrame with numeric columns restored.

    Example:
        >>> _retype(pd.DataFrame({"a": ["1", "2"], "b": ["x", "2"]}), ()).dtypes["a"]
        dtype('int64')
    """
    for col in df.columns:
        if col in skip or not (df[col].dtype == object or pd.api.types.is_string_dtype(df[col])):
            continue
        nums = pd.to_numeric(df[col], errors="coerce")
        if nums.notna().sum() == df[col].notna().sum():
            df[col] = nums
    return df


def apply_raw_steps(df: pd.DataFrame, steps: list[dict], dedupe, keep: tuple[str, ...] = ()) -> tuple[pd.DataFrame, list[dict]]:
    """
    Run the steps that change the header row, BEFORE column names are cleaned.

    Only "remove_top_rows" and "promote_headers" are run here (other steps are
    skipped and run later by `apply_steps`), in the order given:
    - remove_top_rows: drop the first `count` rows (at most all of them).
    - promote_headers: use the first row as column names (blank -> unnamed, repeats
      made unique by `dedupe`), drop that row, and turn all-number text columns
      back into numbers. Columns in `keep` keep their name.

    Args:
        df: pd.DataFrame: the table as read from the file.
        steps: list[dict]: the validated steps (see `validate_steps`).
        dedupe: callable(list[str]) -> list[str]: file_reader's naming rule
            (blank -> unnamed_column_<n>, repeats -> name_2).
        keep: tuple[str, ...]: columns the platform added (e.g. source_file).

    Returns:
        tuple[pd.DataFrame, list[dict]]: the new table, and one entry per step run:
        {step (1-based position in `steps`), op, text (for the report), count}.

    Raises:
        ValueError: if "Use first row as headers" runs on a table with no rows left.

    Example:
        >>> raw = pd.DataFrame({0: ["Date", "2024-01-01"], 1: ["Amount", "5"]})
        >>> df, done = apply_raw_steps(raw, [{"op": "promote_headers"}], dedupe_column_names)
        >>> list(df.columns), done[0]["text"]
        (['Date', 'Amount'], 'Used the first row as column names.')
    """
    done = []
    for i, step in enumerate(steps, start=1):
        if step["op"] not in RAW_OPS:
            continue
        if step["op"] == "remove_top_rows":
            n = min(step["count"], len(df))
            df = df.iloc[n:].reset_index(drop=True)
            done.append({"step": i, "op": "remove_top_rows", "text": f"Removed the top {n:,} row{'s' if n != 1 else ''}.",
                         "count": n})
        elif step["op"] == "promote_headers":
            if df.empty:
                raise ValueError(f"Step {i}: there's no row left to use as headers.")
            first = df.iloc[0]
            headers = [c if c in keep else ("" if pd.isna(first[c]) else str(first[c]).strip()) for c in df.columns]
            df = df.iloc[1:].reset_index(drop=True)
            df.columns = dedupe(headers)
            df = _retype(df, keep)
            done.append({"step": i, "op": "promote_headers", "text": "Used the first row as column names.", "count": 1})
    return df, done


def _need(df: pd.DataFrame, col: str, i: int) -> str:
    """
    Make sure a step's column exists, and return its name.

    Args:
        df: pd.DataFrame: the table at this point in the steps.
        col: str: the column's current name (already resolved from its key).
        i: int: the step number (1-based), used in the error message.

    Returns:
        str: `col`, unchanged.

    Raises:
        ValueError: if `df` has no column called `col` (for example it was renamed
            or removed by an earlier step).

    Example:
        >>> _need(pd.DataFrame({"price": [1]}), "price", 1)
        'price'
    """
    if col not in df.columns:
        raise ValueError(f"Step {i}: there's no column called '{col}' (was it renamed or removed?).")
    return col


def _typed(value: str, series: pd.Series):
    """
    Turn the text typed in a box into the column's kind of value ("" = blank).

    - empty or None -> NaN (a blank)
    - number column -> a number ("$45" -> 45); an int for whole-number int columns
    - date column -> a Timestamp
    - any other column -> the text itself

    Args:
        value: str: what the client typed (e.g. the "find" or "replace" box).
        series: pd.Series: the column the value is for (decides the type).

    Returns:
        The converted value: NaN, int, float, pd.Timestamp or str.

    Raises:
        ValueError: if the column holds numbers (or dates) and `value` isn't one.

    Example:
        >>> _typed("45", pd.Series([1, 2])), _typed("x", pd.Series(["a"]))
        (45, 'x')
    """
    if value is None or str(value).strip() == "":
        return np.nan
    if _is_number(series):
        num = _to_number(pd.Series([value])).iloc[0]
        if pd.isna(num):
            raise ValueError(f"'{value}' isn't a number, but this column holds numbers.")
        return int(num) if pd.api.types.is_integer_dtype(series) and float(num).is_integer() else float(num)
    if pd.api.types.is_datetime64_any_dtype(series):
        d = _to_date(pd.Series([value])).iloc[0]
        if pd.isna(d):
            raise ValueError(f"'{value}' isn't a date, but this column holds dates.")
        return d
    return str(value)


def apply_steps(df: pd.DataFrame, steps: list[dict], resolve, clean_names) -> tuple[pd.DataFrame, list[dict]]:
    """
    Run the steps that come AFTER column names are cleaned, in order.

    Header steps (`RAW_OPS`) are skipped (they already ran in `apply_raw_steps`).
    What each op does:
    - change_type: to number / date / text; values that don't convert become blank
      (the count is reported).
    - replace_values: replace cells equal to "find" ("" finds blanks) with
      "replace" ("" makes them blank); text is compared after trimming spaces.
    - outliers: values below "low" or above "high" are capped (numbers only),
      removed (the whole row) or made blank.
    - unpivot: `df.melt` the chosen columns into attribute + value columns
      (names from "attribute" / "value", cleaned by `clean_names`).
    - pivot: group by all other columns and spread "column"'s values into new
      columns holding `agg` of "values" (at most 100 new columns).
    An integer column that receives blanks is switched to pandas "Int64".

    Args:
        df: pd.DataFrame: the table after Step 0 (clean names) and Step 1 (tidy text).
        steps: list[dict]: the validated steps (see `validate_steps`).
        resolve: callable(str) -> str: turns a column key (its automatic name)
            into its current name, so choices survive a rename.
        clean_names: callable(list[str]) -> list[str]: Step 0's naming rule, used
            for the new columns unpivot / pivot create.

    Returns:
        tuple[pd.DataFrame, list[dict]]: the new table, and one entry per step run:
        {step (1-based), op, text (for the report), count, and "column" for
        single-column steps}.

    Raises:
        ValueError: for a missing column, a value of the wrong type, capping dates,
            unpivot names that already exist, pivoting a column onto itself, or a
            pivot that would create more than 100 columns.

    Example:
        >>> df = pd.DataFrame({"store": ["A", "A"], "jan": [1, 2], "feb": [3, 4]})
        >>> out, done = apply_steps(df, [{"op": "unpivot", "columns": ["jan", "feb"]}],
        ...                         resolve=lambda c: c, clean_names=clean_column_names)
        >>> list(out.columns), len(out)
        (['store', 'attribute', 'value'], 4)
    """
    done = []
    for i, step in enumerate(steps, start=1):
        op = step["op"]
        if op in RAW_OPS:
            continue
        if op == "change_type":
            col = _need(df, resolve(step.get("column", "")), i)
            before = df[col].notna()
            if step["to"] == "number":
                df[col] = _to_number(df[col]) if not _is_number(df[col]) else df[col]
            elif step["to"] == "date":
                df[col] = _to_date(df[col]) if not pd.api.types.is_datetime64_any_dtype(df[col]) else df[col]
            else:
                df[col] = df[col].map(lambda v: v if pd.isna(v) else str(v)).astype(object)
            lost = int((before & df[col].isna()).sum())
            text = f"Changed '{col}' to {step['to']}."
            if lost:
                text += (f" {lost:,} value{'s' if lost != 1 else ''} couldn't be converted and "
                         f"{'are' if lost != 1 else 'is'} now blank.")
            done.append({"step": i, "op": op, "column": col, "text": text, "count": lost})
        elif op == "replace_values":
            col = _need(df, resolve(step.get("column", "")), i)
            find = step.get("find", "")
            if str(find).strip() == "":
                mask = df[col].isna()
            elif _is_number(df[col]) or pd.api.types.is_datetime64_any_dtype(df[col]):
                mask = df[col] == _typed(find, df[col])
            else:
                mask = df[col].astype(str).str.strip() == str(find).strip()
            new = _typed(step.get("replace", ""), df[col])
            n = int(mask.sum())
            if n:
                if pd.isna(new) and pd.api.types.is_integer_dtype(df[col]) and df[col].dtype != "Int64":
                    df[col] = df[col].astype("Int64")
                df.loc[mask, col] = new
            shown_find = "blank" if str(find).strip() == "" else f"'{find}'"
            shown_new = "blank" if pd.isna(new) else f"'{_fmt(new)}'"
            done.append({"step": i, "op": op, "column": col, "count": n,
                         "text": f"Replaced {shown_find} with {shown_new} in '{col}' ({n:,} cell{'s' if n != 1 else ''})."})
        elif op == "outliers":
            col = _need(df, resolve(step.get("column", "")), i)
            s = df[col]
            is_date = pd.api.types.is_datetime64_any_dtype(s)
            conv = (lambda v: None if v in (None, "") else (_to_date(pd.Series([v])).iloc[0] if is_date else float(v)))
            low, high = conv(step.get("low")), conv(step.get("high"))
            outside = pd.Series(False, index=df.index)
            if low is not None:
                outside |= s < low
            if high is not None:
                outside |= s > high
            n = int(outside.sum())
            method = step["method"]
            if method == "cap":
                if is_date:
                    raise ValueError(f"Step {i}: dates can't be capped — remove them or make them blank.")
                capped = s.clip(lower=low, upper=high)
                df[col] = capped.round().astype(s.dtype) if pd.api.types.is_integer_dtype(s) else capped
                text = f"Capped {n:,} unusual value{'s' if n != 1 else ''} in '{col}' to {_fmt(low) if low is not None else '…'}–{_fmt(high) if high is not None else '…'}."
            elif method == "remove":
                df = df.loc[~outside].reset_index(drop=True)
                text = f"Removed {n:,} row{'s' if n != 1 else ''} with an unusual '{col}'."
            else:
                if n and pd.api.types.is_integer_dtype(s) and s.dtype != "Int64":
                    df[col] = s.astype("Int64")
                df.loc[outside, col] = pd.NaT if is_date else np.nan
                text = f"Made {n:,} unusual value{'s' if n != 1 else ''} in '{col}' blank."
            done.append({"step": i, "op": op, "column": col, "count": n, "text": text})
        elif op == "unpivot":
            cols = [_need(df, resolve(c), i) for c in step["columns"]]
            ids = [c for c in df.columns if c not in cols]
            attr, val = clean_names([step.get("attribute") or "attribute", step.get("value") or "value"])
            if attr in ids or val in ids:
                raise ValueError(f"Step {i}: '{attr}' or '{val}' is already a column — pick other names.")
            rows = len(df)
            df = df.melt(id_vars=ids, value_vars=cols, var_name=attr, value_name=val)
            done.append({"step": i, "op": op, "count": len(df),
                         "text": f"Unpivoted {len(cols)} columns into '{attr}' and '{val}' ({rows:,} → {len(df):,} rows)."})
        elif op == "pivot":
            col = _need(df, resolve(step.get("column", "")), i)
            values = _need(df, resolve(step.get("values", "")), i)
            if col == values:
                raise ValueError(f"Step {i}: pick a different column for the values.")
            agg = step.get("agg", "sum")
            others = [c for c in df.columns if c not in (col, values)]
            if df[col].nunique() > 100:
                raise ValueError(f"Step {i}: '{col}' has more than 100 different values — too many new columns.")
            if not others:
                grouped = df.groupby(col, dropna=False)[values].agg(agg).to_frame().T.reset_index(drop=True)
            else:
                grouped = df.groupby(others + [col], dropna=False)[values].agg(agg).unstack(col).reset_index()
            grouped.columns = clean_names([str(c) for c in grouped.columns])
            rows = len(df)
            df = grouped
            done.append({"step": i, "op": op, "count": len(df),
                         "text": f"Pivoted '{col}' into columns of '{values}' ({agg}; {rows:,} → {len(df):,} rows)."})
    return df, done
