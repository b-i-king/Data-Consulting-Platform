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
    return pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s)


def _fmt(v) -> str:
    if isinstance(v, (pd.Timestamp, dt.date)):
        return str(v)[:10]
    if isinstance(v, (float, np.floating)):
        return f"{v:,.2f}".rstrip("0").rstrip(".")
    return f"{v:,}" if isinstance(v, (int, np.integer)) else str(v)


def outlier_check(series: pd.Series, is_id: bool = False) -> dict | None:
    """
    "Wrong data": values far outside the usual range, or None when there are none.
    Returns {kind, count, low, high, min, max, examples} — low/high are the limits.
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
    """'$1,200' -> 1200.0, '15%' -> 15.0; anything else -> NaN."""
    return pd.to_numeric(s.astype(str).str.replace(_MONEY, "", regex=True), errors="coerce")


def _to_date(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s.astype(str), errors="coerce", format="mixed")


def format_check(series: pd.Series) -> dict | None:
    """
    "Wrong format": a text column that is mostly numbers or dates. Returns
    {to, convertible, failing, examples} (examples = values that won't convert)
    or None. Works on the distinct values, so big columns stay fast.
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
    """Check the `transforms` list; raise ValueError with a plain-English message."""
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
    """After headers move, text columns that are all numbers become numbers again."""
    for col in df.columns:
        if col in skip or not (df[col].dtype == object or pd.api.types.is_string_dtype(df[col])):
            continue
        nums = pd.to_numeric(df[col], errors="coerce")
        if nums.notna().sum() == df[col].notna().sum():
            df[col] = nums
    return df


def apply_raw_steps(df: pd.DataFrame, steps: list[dict], dedupe, keep: tuple[str, ...] = ()) -> tuple[pd.DataFrame, list[dict]]:
    """
    Steps that change the header row, run BEFORE column names are cleaned.
    `dedupe` is file_reader's naming rule (blank -> unnamed_column_<n>, repeats
    -> name_2). `keep` = columns the platform added (e.g. source_file).
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
    if col not in df.columns:
        raise ValueError(f"Step {i}: there's no column called '{col}' (was it renamed or removed?).")
    return col


def _typed(value: str, series: pd.Series):
    """Turn the text typed in a box into the column's kind of value ("" = blank)."""
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
    The steps that run AFTER column names are cleaned, in order. `resolve`
    turns a column key into its current name; `clean_names` is Step 0's
    naming rule (for the new columns unpivot / pivot create).
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
