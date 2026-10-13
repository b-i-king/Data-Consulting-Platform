"""
Data cleaning pipeline — the most important step on the platform.

WHAT THIS FILE DOES
-------------------
Takes one pandas DataFrame and returns:
  * a CLEANED copy of the data, and
  * a CLEANING REPORT that explains, in plain English and in numbers,
    exactly what was changed and what still needs attention.

The original DataFrame is never modified (we work on a copy), so the raw
data is always recoverable.

THE PIPELINE (runs in this order)
---------------------------------
Step 1. Tidy text cells
        Trim leading/trailing spaces in text columns (" Acme " -> "Acme")
        and turn blank text ("", "   ") into a real missing value (NaN).
        Blank strings LOOK filled in but are really missing; counting them
        as missing makes the null report honest.

Step 2. Drop completely empty rows
        Rows where EVERY value is missing (very common at the bottom of
        Excel exports). They contain no information.

Step 3. Remove duplicate rows
        A row is a duplicate only when ALL of its values match an earlier
        row exactly — the clearest possible signal of a copy. The first
        occurrence is kept. (Near-duplicates, e.g. same customer with a
        different spelling, need business context and are NOT removed.)

        Note on stacked files: the `source_file` column we add when stacking
        is IGNORED in Steps 2 and 3. So a blank row is still "empty", and the
        same record appearing in two overlapping exports (e.g. sales_2023.csv
        and sales_all.csv) is still caught as a duplicate.

Step 3b. Restore whole numbers
        pandas stores a number column that has ANY blank as decimals, so IDs
        like 1001 come out as "1001.0". Columns whose values are all whole
        numbers are switched back to an integer type that allows blanks.

Step 4. Missing-value report and advice
        For every column: how many values are missing and what percentage
        of rows that is, plus advice based on simple rules of thumb:
            under 20% missing  -> fine to keep, or fill the gaps
            20% or more        -> think about removing the column (or fill it)
            90% or more        -> remove the column; too little is left to trust
            90%+ of ALL cells  -> the whole dataset is mostly empty: book a
                                  consultation instead of cleaning by hand
        For each column with gaps we also suggest how to FILL them:
            numbers, roughly symmetric (|skewness| < 0.5) -> the mean (average)
            numbers, skewed (a long tail, e.g. sales)    -> the median (middle
                value), because a few huge values drag the mean up
            text, categories, true/false                 -> the mode (most common value)
            dates, and IDs (customer_id, order number, zip) -> no fill offered:
                an average customer ID means nothing, and a missing ID usually
                means "none" (e.g. a walk-in customer)
        "Use our suggestions" in the app fills only columns under 20% missing
        and removes 90%+ columns; 20–90% columns are left for the user to decide
        (filling a third of a column with one value changes the data a lot).
        By default NOTHING is filled — we never guess without being asked.

Step 5. What to do about missing values (chosen by the user)
        5a. Per column ("column_actions"): keep (default), drop the column, or
            fill gaps with the mean / median / mode ("auto" = our suggestion).
            Whole-number columns get a rounded fill value so 1001 stays 1001.
        5b. Rows ("missing_strategy"):
            * "keep"     (default) — keep every row; the report shows the gaps.
            * "drop_any" — drop every row that STILL has a missing value after
                           5a. Can remove a lot of data — the report shows how much.

Step 6. Column profile
        The inferred type of each column (number, date, text, true/false)
        and a few example values, to help the user sanity-check the result.

ASSUMPTION: the first row of the file contains the column names.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

MissingStrategy = Literal["keep", "drop_any"]
# What to do with one column's missing values (Step 5a).
COLUMN_ACTIONS = ("keep", "drop", "mean", "median", "mode", "auto")

# Rules of thumb for missing values (Step 4). Percentages, 0-100.
REVIEW_PCT = 20.0    # at or above: think about removing the column
REMOVE_PCT = 90.0    # at or above: remove the column
CONSULT_PCT = 90.0   # at or above, for ALL cells: recommend a consultation
SKEW_LIMIT = 0.5     # |skewness| at or above: fill numbers with the median, not the mean

# Column names that look like identifiers or codes (never averaged or filled).
ID_NAME = re.compile(r"(^|_)(id|key|code|number|num|no|zip|zipcode|postal|sku|uuid|guid)$|^id_", re.IGNORECASE)


def _rows(n: int) -> str:
    """'1 row' / '12 rows' — keeps report sentences grammatical."""
    return f"{n:,} row" if n == 1 else f"{n:,} rows"

# Columns WE add (not the client's data); ignored when spotting empty/duplicate rows.
METADATA_COLUMNS = ("source_file",)


@dataclass
class ColumnReport:
    """Per-column facts shown in the cleaning report table."""

    column: str
    inferred_type: str          # "number", "date", "text", "true/false", "empty"
    missing_count: int
    missing_pct: float          # 0–100, rounded to 2 decimals
    unique_count: int
    example_values: list[str]
    # Missing-value advice (Step 4). missing_count / missing_pct describe the
    # column BEFORE the user's missing-value choices, so the advice stays visible.
    missing_level: str = "none"          # "none", "low" (<20%), "review" (20-90%), "remove" (90%+)
    recommendation: str = ""
    suggested_fill: str | None = None    # "mean", "median", "mode", or None
    skewness: float | None = None        # numbers only; 0 = symmetric
    fill_options: dict[str, str] = field(default_factory=dict)  # method -> value it would use
    # What actually happened (Step 5a).
    action: str = "keep"                 # "keep", "dropped", "mean", "median", "mode"
    filled_count: int = 0
    missing_after: int = 0               # missing values left in the downloaded data


@dataclass
class CleaningReport:
    """Everything the user needs to understand what the cleaning did."""

    dataset_name: str
    rows_before: int
    rows_after: int
    columns: int                     # fields (columns) in the cleaned data
    blank_strings_converted: int     # Step 1
    empty_rows_removed: int          # Step 2
    duplicate_rows_removed: int      # Step 3
    rows_dropped_for_missing: int    # Step 5 (only when strategy = "drop_any")
    missing_strategy: str
    total_missing_cells: int         # after cleaning
    total_missing_pct: float         # share of all cells that are missing
    columns_fully_empty: list[str]
    column_reports: list[ColumnReport] = field(default_factory=list)
    columns_before: int = 0              # fields before any column was dropped
    columns_dropped: list[str] = field(default_factory=list)   # Step 5a
    cells_filled: int = 0                # Step 5a
    missing_pct_before: float = 0.0      # share of cells missing before Step 5
    columns_to_review: list[str] = field(default_factory=list)  # 20%+ missing
    columns_to_remove: list[str] = field(default_factory=list)  # 90%+ missing
    consultation_recommended: bool = False   # 90%+ of all cells missing
    notes: list[str] = field(default_factory=list)
    runtime_seconds: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


def _infer_type(series: pd.Series) -> str:
    """Return a friendly type name for a column (used only for reporting)."""
    s = series.dropna()
    if s.empty:
        return "empty"
    if pd.api.types.is_bool_dtype(s):
        return "true/false"
    if pd.api.types.is_numeric_dtype(s):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "date"
    # Text column: check whether it is really dates or numbers stored as text.
    sample = s.astype(str).head(500)
    if pd.to_numeric(sample.str.replace(r"[$,%\s]", "", regex=True), errors="coerce").notna().mean() >= 0.95:
        return "number (stored as text)"
    if pd.to_datetime(sample, errors="coerce", format="mixed").notna().mean() >= 0.95:
        return "date (stored as text)"
    return "text"


def _tidy_text(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """Step 1: trim spaces in text columns and convert blanks to NaN."""
    converted = 0
    for col in df.columns:
        # Only object/string columns hold free text.
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            is_str = df[col].map(lambda v: isinstance(v, str))
            if not is_str.any():
                continue
            stripped = df.loc[is_str, col].str.strip()
            blanks = stripped == ""
            converted += int(blanks.sum())
            df.loc[is_str, col] = stripped.where(~blanks, np.nan)
    return df, converted


def _restore_whole_numbers(df: pd.DataFrame) -> list[str]:
    """
    Step 3b: turn float columns that only hold whole numbers back into
    integers. Uses pandas' nullable "Int64" type, which allows blanks, so
    1001.0 -> 1001 while missing values stay missing. Returns the changed
    column names (for the report).
    """
    changed = []
    for col in df.columns:
        if not pd.api.types.is_float_dtype(df[col]):
            continue
        values = df[col].dropna()
        if values.empty or not (values % 1 == 0).all():
            continue
        # Stay within the 64-bit integer range to avoid overflow on huge values.
        if values.abs().max() >= 2**53:
            continue
        df[col] = df[col].astype("Int64")
        changed.append(str(col))
    return changed


def _is_number(series: pd.Series) -> bool:
    """Real number columns (true/false columns are numbers to pandas, but not to people)."""
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def _fill_value(series: pd.Series, method: str):
    """
    The value used to fill gaps with `method` ("mean", "median" or "mode"),
    or None when the method doesn't fit the column (e.g. the mean of text).
    Whole-number columns (pandas "Int64") get a rounded value.
    """
    s = series.dropna()
    if s.empty or ID_NAME.search(str(series.name)):   # IDs and codes are never filled
        return None
    if method in ("mean", "median"):
        if not _is_number(series):
            return None
        value = float(s.mean() if method == "mean" else s.median())
        return int(round(value)) if pd.api.types.is_integer_dtype(series) else value
    if method == "mode":
        if pd.api.types.is_datetime64_any_dtype(series):
            return None
        return s.mode().iloc[0]  # most common value (ties: the first in sort order)
    return None


def _missing_advice(series: pd.Series, missing_pct: float) -> dict:
    """Step 4: the level, plain-English advice, and fill suggestion for one column."""
    advice = {"missing_level": "none", "recommendation": "", "suggested_fill": None,
              "skewness": None, "fill_options": {}}
    if missing_pct == 0:
        return advice
    if ID_NAME.search(str(series.name)):
        # IDs and codes: no fills. A blank customer_id usually means "no account" (a walk-in).
        level = "remove" if missing_pct >= REMOVE_PCT else "review" if missing_pct >= REVIEW_PCT else "low"
        advice["missing_level"] = level
        advice["recommendation"] = (f"{missing_pct:.0f}% missing — this looks like an ID or code, so it can't be "
                                    "filled; a blank usually means \"none\" (e.g. a walk-in customer). "
                                    + ("Remove it if it isn't needed." if level == "remove" else "Keep the gaps."))
        return advice
    s = series.dropna()
    if _is_number(series) and len(s) >= 3:
        skew = s.astype(float).skew()
        advice["skewness"] = None if pd.isna(skew) else round(float(skew), 2)
    # Which fills make sense, and what value each would use (shown in the UI).
    methods = ("mean", "median", "mode") if _is_number(series) else ("mode",)
    for m in methods:
        v = _fill_value(series, m)
        if v is not None:
            advice["fill_options"][m] = f"{v:,.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    if _is_number(series):
        skewed = advice["skewness"] is not None and abs(advice["skewness"]) >= SKEW_LIMIT
        advice["suggested_fill"] = "median" if skewed else "mean"
    elif advice["fill_options"]:
        advice["suggested_fill"] = "mode"
    why = {"mean": "the values are roughly symmetric, so the average is typical",
           "median": "the values are skewed, so the middle value is more typical than the average",
           "mode": "it's the most common value"}.get(advice["suggested_fill"] or "", "")
    fill = f"fill the gaps with the {advice['suggested_fill']} ({why})" if advice["suggested_fill"] else "leave the gaps"

    if missing_pct >= REMOVE_PCT:
        advice["missing_level"] = "remove"
        advice["recommendation"] = f"{missing_pct:.0f}% missing — remove this column; too little is left to trust or fill."
    elif missing_pct >= REVIEW_PCT:
        advice["missing_level"] = "review"
        advice["recommendation"] = f"{missing_pct:.0f}% missing — think about removing this column, or {fill}."
    else:
        advice["missing_level"] = "low"
        advice["recommendation"] = f"Only {missing_pct:.1f}% missing — keep it, or {fill}."
    return advice


def _apply_column_actions(df: pd.DataFrame, actions: dict[str, str], advice: dict[str, dict],
                          notes: list[str]) -> tuple[pd.DataFrame, dict[str, tuple[str, int]]]:
    """
    Step 5a: drop or fill columns as the user chose. Returns the new data and
    {column: (what was done, cells filled)}. "auto" means our suggestion.
    """
    done: dict[str, tuple[str, int]] = {}
    for col, action in actions.items():
        if action not in COLUMN_ACTIONS:
            raise ValueError(f"Unknown action '{action}' for column '{col}'. Use one of: {', '.join(COLUMN_ACTIONS)}.")
        if col not in df.columns or action == "keep":
            continue
        if action == "auto":
            action = advice.get(col, {}).get("suggested_fill") or "keep"
            if action == "keep":
                continue
        if action == "drop":
            df = df.drop(columns=[col])
            done[col] = ("dropped", 0)
            continue
        gaps = int(df[col].isna().sum())
        value = _fill_value(df[col], action)
        if value is None:
            notes.append(f"Couldn't fill '{col}' with the {action} (mean and median need numbers, and IDs "
                         "are never filled); left as is.")
            continue
        if gaps:
            df[col] = df[col].fillna(value)
        done[col] = (action, gaps)
    return df, done


def clean_dataframe(
    df: pd.DataFrame,
    dataset_name: str = "dataset",
    missing_strategy: MissingStrategy = "keep",
    column_actions: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, CleaningReport]:
    """
    Run the full cleaning pipeline (Steps 1–6 above).

    `column_actions` maps column name -> "keep" | "drop" | "mean" | "median" |
    "mode" | "auto" (Step 5a). Leave it out to change nothing.

    Returns (cleaned_dataframe, report). `report.runtime_seconds` is the
    wall-clock processing time, which is what runtime billing is based on.
    """
    started = time.perf_counter()
    notes: list[str] = []
    rows_before = int(len(df))
    out = df.copy()  # never touch the caller's data

    # Step 1 — tidy text.
    out, blanks_converted = _tidy_text(out)
    if blanks_converted:
        cells = "cell was" if blanks_converted == 1 else "cells were"
        notes.append(f"{blanks_converted:,} blank text {cells} treated as missing values.")

    # Columns that hold the client's actual data (everything except ours).
    data_cols = [c for c in out.columns if c not in METADATA_COLUMNS] or list(out.columns)

    # Step 2 — drop rows where every data value is missing.
    empty_mask = out[data_cols].isna().all(axis=1)
    empty_rows = int(empty_mask.sum())
    out = out.loc[~empty_mask]
    if empty_rows:
        notes.append(f"{_rows(empty_rows)} with no values at all removed.")

    # Step 3 — remove exact duplicates (all data values identical), keep first.
    dup_mask = out.duplicated(subset=data_cols, keep="first")
    dup_rows = int(dup_mask.sum())
    out = out.loc[~dup_mask]
    if dup_rows:
        notes.append(f"{_rows(dup_rows)} removed as exact duplicates (the first copy of each was kept).")

    # Step 3b — 1001.0 -> 1001 for whole-number columns.
    restored = _restore_whole_numbers(out)
    if restored:
        notes.append(f"Whole numbers restored (no more '.0') in: {', '.join(restored)}.")

    # Step 4 / 6 — profile every column BEFORE the missing-value choices, so
    # the report always shows where the gaps were and what we'd suggest.
    profiled = out
    n_pre = len(profiled)
    columns_before = len(profiled.columns)
    profiles: dict[str, dict] = {}
    fully_empty = []
    for col in profiled.columns:
        series = profiled[col]
        missing = int(series.isna().sum())
        pct = round(missing / n_pre * 100, 2) if n_pre else 0.0
        if n_pre and missing == n_pre:
            fully_empty.append(str(col))
        profiles[str(col)] = {
            "column": str(col),
            "inferred_type": _infer_type(series),
            "missing_count": missing,
            "missing_pct": pct,
            "unique_count": int(series.nunique(dropna=True)),
            "example_values": [str(v) for v in series.dropna().unique()[:3]],
            **_missing_advice(series, pct),
        }
    total_cells_pre = n_pre * columns_before
    missing_pct_before = round(int(profiled.isna().sum().sum()) / total_cells_pre * 100, 2) if total_cells_pre else 0.0
    if fully_empty:
        notes.append(f"These columns have no values at all: {', '.join(fully_empty)}.")
    # (IDs are left out of the "review" list: their gaps usually mean "none", which is fine.)
    to_review = [c for c, p in profiles.items() if p["missing_level"] == "review" and not ID_NAME.search(c)]
    to_remove = [c for c, p in profiles.items() if p["missing_level"] == "remove"]
    if to_review:
        notes.append(f"20% or more missing — think about removing (or filling): {', '.join(to_review)}.")
    if to_remove:
        notes.append(f"90% or more missing — we recommend removing: {', '.join(to_remove)}.")
    consult = missing_pct_before >= CONSULT_PCT
    if consult:
        notes.append(f"{missing_pct_before:.0f}% of all cells are empty. Book a consultation — "
                     "a consultant can help find the missing data before any analysis.")

    # Step 5a — per-column choices (drop / fill).
    out, done = _apply_column_actions(out.copy(), column_actions or {}, profiles, notes)
    dropped_cols = [c for c, (a, _) in done.items() if a == "dropped"]
    cells_filled = sum(k for _, k in done.values())
    if dropped_cols:
        notes.append(f"Columns removed at your request: {', '.join(dropped_cols)}.")
    for col, (action, k) in done.items():
        if action != "dropped" and k:
            notes.append(f"Filled {k:,} missing value{'s' if k != 1 else ''} in '{col}' with the "
                         f"{action} ({profiles[col]['fill_options'].get(action, '')}).")

    # Step 5b — row strategy, AFTER fills so filled rows are kept.
    dropped_for_missing = 0
    if missing_strategy == "drop_any":
        before = len(out)
        out = out.dropna(how="any")
        dropped_for_missing = before - len(out)
        if before and dropped_for_missing / before > 0.2:
            notes.append(
                f"Warning: dropping rows with missing values removed {_rows(dropped_for_missing)} "
                f"({dropped_for_missing / before:.0%} of the data). Consider keeping them."
            )

    out = out.reset_index(drop=True)

    n = len(out)
    column_reports = []
    for col, prof in profiles.items():
        action, filled = done.get(col, ("keep", 0))
        column_reports.append(ColumnReport(
            **prof, action=action, filled_count=filled,
            missing_after=int(out[col].isna().sum()) if col in out.columns else 0,
        ))

    total_cells = n * len(out.columns)
    total_missing = int(out.isna().sum().sum())

    report = CleaningReport(
        dataset_name=dataset_name,
        rows_before=rows_before,
        rows_after=n,
        columns=len(out.columns),
        blank_strings_converted=blanks_converted,
        empty_rows_removed=empty_rows,
        duplicate_rows_removed=dup_rows,
        rows_dropped_for_missing=dropped_for_missing,
        missing_strategy=missing_strategy,
        total_missing_cells=total_missing,
        total_missing_pct=round(total_missing / total_cells * 100, 2) if total_cells else 0.0,
        columns_fully_empty=fully_empty,
        column_reports=column_reports,
        columns_before=columns_before,
        columns_dropped=dropped_cols,
        cells_filled=cells_filled,
        missing_pct_before=missing_pct_before,
        columns_to_review=to_review,
        columns_to_remove=to_remove,
        consultation_recommended=consult,
        notes=notes,
        runtime_seconds=round(time.perf_counter() - started, 4),
    )
    return out, report


def dataframe_to_records(df: pd.DataFrame, limit: int | None = None) -> list[dict]:
    """
    Convert a DataFrame to JSON-safe rows for the on-screen preview.

    NaN / NaT become `None` (shown as an empty cell), and dates/numpy types
    become plain strings/numbers that JSON understands.
    """
    view = df if limit is None else df.head(limit)
    # to_json handles numpy types, NaN and dates correctly; read it back as dicts.
    return json.loads(view.to_json(orient="records", date_format="iso"))
