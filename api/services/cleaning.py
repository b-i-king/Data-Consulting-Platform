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

Step 4. Missing-value report
        For every column: how many values are missing and what percentage
        of rows that is. Because we do not know the client's business
        rules, we do NOT guess or fill in missing values.

Step 5. Missing-value strategy (chosen by the user)
        * "keep"     (default) — keep every row; the raw data stays intact and
                      the report shows where the gaps are.
        * "drop_any" — drop every row that has at least one missing value.
                      Useful for analysis that cannot handle gaps, but can
                      remove a lot of data — the report shows how much.

Step 6. Column profile
        The inferred type of each column (number, date, text, true/false)
        and a few example values, to help the user sanity-check the result.

ASSUMPTION: the first row of the file contains the column names.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

import numpy as np
import pandas as pd

MissingStrategy = Literal["keep", "drop_any"]


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


@dataclass
class CleaningReport:
    """Everything the user needs to understand what the cleaning did."""

    dataset_name: str
    rows_before: int
    rows_after: int
    columns: int
    blank_strings_converted: int     # Step 1
    empty_rows_removed: int          # Step 2
    duplicate_rows_removed: int      # Step 3
    rows_dropped_for_missing: int    # Step 5 (only when strategy = "drop_any")
    missing_strategy: str
    total_missing_cells: int         # after cleaning
    total_missing_pct: float         # share of all cells that are missing
    columns_fully_empty: list[str]
    column_reports: list[ColumnReport] = field(default_factory=list)
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


def clean_dataframe(
    df: pd.DataFrame,
    dataset_name: str = "dataset",
    missing_strategy: MissingStrategy = "keep",
) -> tuple[pd.DataFrame, CleaningReport]:
    """
    Run the full cleaning pipeline (Steps 1–6 above).

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

    # Step 5 — apply the chosen missing-value strategy BEFORE the final report
    # so the numbers describe the data the user will actually download.
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

    # Step 4 / 6 — per-column report on the cleaned data.
    n = len(out)
    column_reports = []
    fully_empty = []
    for col in out.columns:
        series = out[col]
        missing = int(series.isna().sum())
        if n and missing == n:
            fully_empty.append(str(col))
        examples = [str(v) for v in series.dropna().unique()[:3]]
        column_reports.append(
            ColumnReport(
                column=str(col),
                inferred_type=_infer_type(series),
                missing_count=missing,
                missing_pct=round(missing / n * 100, 2) if n else 0.0,
                unique_count=int(series.nunique(dropna=True)),
                example_values=examples,
            )
        )
    if fully_empty:
        notes.append(f"These columns have no values at all: {', '.join(fully_empty)}.")

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
