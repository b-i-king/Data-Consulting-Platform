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
Step 0. Clean column names
        Every column name becomes lowercase "snake_case": spaces and
        punctuation turn into underscores, and words joined in camelCase are
        split ("Order Date" -> order_date, "Unit Price ($)" -> unit_price,
        "CustomerID" -> customer_id). Names stay unique (a second "amount"
        becomes amount_2) and a blank name becomes column_1, column_2, ...
        Consistent names are easier to use in Excel, SQL and Python, and every
        later step (and the download) uses the new names. The report lists
        each rename (`columns_renamed`).

        Header problems are flagged so the client can fix them before
        analyzing (`name_problem` on each column, plus a note):
          * "unnamed"   — the header cell was blank (file_reader.py names it
                          unnamed_column_<position>). Name it, remove it, or
                          fix the header row and upload again.
          * "duplicate" — two columns had the same name (amount, amount_2).
                          Rename them so analysis can tell them apart
                          (e.g. amount_paid, amount_refunded).
        The client can rename any column ("column_names": {key: new name});
        their names are cleaned the same way and must stay unique. Each column
        keeps a stable `key` (its automatic name), so choices made before a
        rename still apply after it. A renamed column's problem counts as fixed.

Step 0b. Header steps the client chose ("Use first row as headers", "Remove
        top rows") run BEFORE Step 0, because they change the header row.
        See api/services/transforms.py.

Step 1. Tidy text cells
        Trim leading/trailing spaces in text columns (" Acme " -> "Acme")
        and turn blank text ("", "   ") into a real missing value (NaN).
        Blank strings LOOK filled in but are really missing; counting them
        as missing makes the null report honest.

Step 1b. The client's transform steps (transforms.py), in order:
        change data type, replace values, a rule for unusual values (cap /
        remove / blank), unpivot, pivot. Like Power Query's "Applied steps";
        each one is listed in the report (`transforms_applied`).

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
                an average customer ID means nothing
        IDs SHOULD NOT BE MISSING (like a NOT NULL rule in a database): for an
        ID column with gaps we recommend removing the rows that have no ID
        ("drop_rows"), so only rows you can trace are analyzed. The removed
        rows aren't lost: the Excel download puts them on a "rows_missing_id"
        sheet so the client can look up the right IDs and upload again.
        "Use our suggestions" in the app fills only columns under 20% missing,
        removes rows with a blank ID, and removes 90%+ columns; other 20–90%
        columns are left for the user to decide
        (filling a third of a column with one value changes the data a lot).
        By default NOTHING is filled — we never guess without being asked.

Step 5. What to do about missing values (chosen by the user)
        5a. Per column ("column_actions"): keep (default), drop the column,
            fill gaps with the mean / median / mode ("auto" = our suggestion),
            or "drop_rows" — remove the rows where this column is missing
            (offered for ID columns; set aside for the "rows_missing_id" sheet).
            Whole-number columns get a rounded fill value so 1001 stays 1001.
        5b. Rows ("missing_strategy"):
            * "keep"     (default) — keep every row; the report shows the gaps.
            * "drop_any" — drop every row that STILL has a missing value after
                           5a. Can remove a lot of data — the report shows how much.

Step 7. Enough rows to analyze?
        Analysis needs at least 30 rows (MIN_ROWS_FOR_ANALYSIS) — fewer, and
        averages, trends and tests are too unreliable to show. Checked BEFORE
        cleaning (the file as uploaded) and AFTER cleaning (duplicates and
        empty rows removed, the user's row choices applied). Below 30 at either
        point, the data can still be cleaned and downloaded, but the dashboard
        and every non-cleaning skill are switched off for that table
        (`analysis_ready` = False, with `analysis_note` saying why).

Step 6. Column profile and data-quality checks
        The inferred type of each column (number, date, text, true/false)
        and a few example values, to help the user sanity-check the result.
        Plus two checks that only REPORT (nothing changes until the client
        picks a step):
          * wrong data ("outliers"): numbers outside 1.5 × the interquartile
            range (the box-plot rule) and dates in the future or before 1900
          * wrong format ("format_issue"): text columns that are really
            numbers or dates, and which values wouldn't convert

ASSUMPTION: the first row of the file contains the column names.
"""

from __future__ import annotations

import json
import re
import time
import unicodedata
from dataclasses import asdict, dataclass, field, fields
from typing import Literal

import numpy as np
import pandas as pd

from api.services import transforms as tf
from api.services.file_reader import dedupe_column_names

MissingStrategy = Literal["keep", "drop_any"]
# What to do with one column's missing values (Step 5a).
COLUMN_ACTIONS = ("keep", "drop", "mean", "median", "mode", "auto", "drop_rows")

# Rules of thumb for missing values (Step 4). Percentages, 0-100.
REVIEW_PCT = 20.0    # at or above: think about removing the column
REMOVE_PCT = 90.0    # at or above: remove the column
CONSULT_PCT = 90.0   # at or above, for ALL cells: recommend a consultation
SKEW_LIMIT = 0.5     # |skewness| at or above: fill numbers with the median, not the mean
MIN_ROWS_FOR_ANALYSIS = 30   # Step 7: fewer rows (before OR after cleaning) = no analysis

# A header cell that was blank in the file (named by file_reader.py).
UNNAMED = re.compile(r"^unnamed_column_\d+$")
# A repeated header made unique: "amount_2" when "amount" is also a column.
REPEAT = re.compile(r"^(.+)_(\d+)$")

# Column names that look like identifiers or codes (never averaged or filled).
ID_NAME = re.compile(r"(^|_)(id|key|code|number|num|no|zip|zipcode|postal|sku|uuid|guid)$|^id_", re.IGNORECASE)


def _rows(n: int) -> str:
    """
    Say "1 row" / "12 rows" — keeps report sentences grammatical.

    Numbers get US thousands separators.

    Args:
        n: int: the number of rows.

    Returns:
        str: the count followed by "row" or "rows".

    Example:
        >>> _rows(1), _rows(1200)
        ('1 row', '1,200 rows')
    """
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
    key: str = ""               # stable name for choices (the automatic Step 0 name)
    original_name: str = ""     # the header in the file ("" when it was blank)
    name_problem: str | None = None   # "unnamed", "duplicate", or None (fine / fixed)
    # Missing-value advice (Step 4). missing_count / missing_pct describe the
    # column BEFORE the user's missing-value choices, so the advice stays visible.
    missing_level: str = "none"          # "none", "low" (<20%), "review" (20-90%), "remove" (90%+)
    recommendation: str = ""
    suggested_fill: str | None = None    # "mean", "median", "mode", "drop_rows" (IDs), or None
    skewness: float | None = None        # numbers only; 0 = symmetric
    fill_options: dict[str, str] = field(default_factory=dict)  # method -> value it would use
    # What actually happened (Step 5a).
    action: str = "keep"                 # "keep", "dropped", "mean", "median", "mode", "rows_dropped"
    filled_count: int = 0
    rows_dropped: int = 0                # rows removed because this column was missing ("drop_rows")
    is_id: bool = False                  # looks like an ID or code (never filled; should not be missing)
    missing_after: int = 0               # missing values left in the downloaded data
    outliers: dict | None = None         # Step 6 "wrong data": {kind, count, low, high, min, max, examples, note}
    format_issue: dict | None = None     # Step 6 "wrong format": {to, convertible, failing, examples}


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
    analysis_ready: bool = True          # Step 7: at least 30 rows before AND after cleaning
    analysis_note: str = ""              # why not, in plain English (empty when ready)
    columns_renamed: dict[str, str] = field(default_factory=dict)   # Step 0: old name -> new name
    unnamed_columns: list[str] = field(default_factory=list)       # Step 0: blank headers, still unfixed
    duplicate_columns: list[list[str]] = field(default_factory=list)  # Step 0: same-name groups, still unfixed
    rows_missing_id_removed: int = 0     # Step 5a "drop_rows": rows set aside because an ID was missing
    transforms_applied: list[dict] = field(default_factory=list)   # Steps 0b / 1b: what each step did
    outlier_columns: list[str] = field(default_factory=list)       # Step 6: columns with unusual values
    format_columns: list[str] = field(default_factory=list)        # Step 6: columns in the wrong format
    notes: list[str] = field(default_factory=list)
    runtime_seconds: float = 0.0
    # The rows removed for a missing ID, kept so the Excel download can list
    # them for the client to fix. Not part of the JSON report.
    rows_missing_id: pd.DataFrame | None = field(default=None, repr=False, compare=False)

    def to_dict(self) -> dict:
        # Every field except the DataFrame (asdict would also deep-copy it).
        """
        Turn the report into a plain dict that can be sent as JSON.

        Every field is included EXCEPT `rows_missing_id` (a DataFrame, used only for
        the Excel download). Nested ColumnReports become dicts, and lists/dicts are
        copied so changing the result does not change the report.

        Returns:
            dict: field name -> value, ready for the API response.

        Example:
            report.to_dict()["rows_after"]        # e.g. 1200
            report.to_dict()["column_reports"][0]["column"]   # e.g. "order_date"
        """
        out = {f.name: getattr(self, f.name) for f in fields(self) if f.name != "rows_missing_id"}
        out["column_reports"] = [asdict(c) for c in self.column_reports]
        out["columns_renamed"] = dict(self.columns_renamed)
        out["columns_fully_empty"] = list(self.columns_fully_empty)
        out["unnamed_columns"] = list(self.unnamed_columns)
        out["duplicate_columns"] = [list(g) for g in self.duplicate_columns]
        out["transforms_applied"] = [dict(s) for s in self.transforms_applied]
        out["outlier_columns"] = list(self.outlier_columns)
        out["format_columns"] = list(self.format_columns)
        return out


def snake_case(name: object) -> str:
    """
    Step 0 for one name: make it lowercase "snake_case".

    Rules, in order:
    - trim spaces; accented letters become plain letters (é -> e)
    - camelCase is split ("CustomerID" -> "Customer ID", "IDNumber" -> "ID Number")
    - lowercase, and every run of non-letters/digits becomes one "_" (stripped at the ends)
    'Order Date' -> 'order_date', 'CustomerID' -> 'customer_id',
    'Unit Price ($)' -> 'unit_price', 'Café Name' -> 'cafe_name'.
    Returns '' for a name with no letters or digits (the caller numbers it).

    Args:
        name: object: the column name (any value; converted with `str()`).

    Returns:
        str: the snake_case name, or '' when nothing usable is left.

    Example:
        >>> snake_case("Unit Price ($)"), snake_case("CustomerID"), snake_case("###")
        ('unit_price', 'customer_id', '')
    """
    text = str(name).strip()
    # Accented letters -> plain letters (é -> e), so names are easy to type in code.
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    # Split camelCase: "displayName" -> "display Name", "IDNumber" -> "ID Number".
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", text)
    # Anything that isn't a letter or digit becomes one underscore.
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def clean_column_names(columns) -> list[str]:
    """
    Step 0 for a whole table: snake_case every name, keeping names unique.

    - each name goes through `snake_case`; an empty result becomes column_<position>
      (1-based)
    - a name starting with a digit gets "col_" in front ("2024 sales" -> col_2024_sales)
    - repeats get a number: the second "amount" -> amount_2, the third -> amount_3

    Args:
        columns: iterable of column names (any values), in table order.

    Returns:
        list[str]: the new names, same length and order as `columns`.

    Example:
        >>> clean_column_names(["Amount", "amount", "", "2024 Sales"])
        ['amount', 'amount_2', 'column_3', 'col_2024_sales']
    """
    seen: dict[str, int] = {}
    out = []
    for i, col in enumerate(columns, start=1):
        base = snake_case(col) or f"column_{i}"
        if base[0].isdigit():
            base = f"col_{base}"        # "2024 sales" -> col_2024_sales (code can't start with a digit)
        name = base
        while name in seen:             # second "amount" -> amount_2, third -> amount_3
            seen[base] += 1
            name = f"{base}_{seen[base]}"
        seen.setdefault(base, 1)
        seen.setdefault(name, 1)
        out.append(name)
    return out


def _header_problems(auto: list[str], fixed: set[str]) -> dict[str, str]:
    """
    Step 0 checks: find blank ("unnamed") and repeated ("duplicate") headers.

    Works on the automatic names: unnamed_column_<n> means the header cell was
    blank; "amount_2" alongside "amount" means the name was repeated. A column
    the client renamed or removed (`fixed`) is fine. A duplicate group is fixed
    once at most one of its columns still has its automatic name.

    Args:
        auto: list[str]: the automatic Step 0 names (the stable column keys).
        fixed: set[str]: keys of columns the client renamed or is removing.

    Returns:
        dict[str, str]: key -> "unnamed" or "duplicate", only for columns that
        still have a problem.

    Example:
        >>> _header_problems(["amount", "amount_2", "unnamed_column_3"], set())
        {'unnamed_column_3': 'unnamed', 'amount': 'duplicate', 'amount_2': 'duplicate'}
        >>> _header_problems(["amount", "amount_2"], {"amount_2"})
        {}
    """
    problems: dict[str, str] = {}
    names = set(auto)
    groups: dict[str, list[str]] = {}
    for name in auto:
        if UNNAMED.match(name):
            if name not in fixed:
                problems[name] = "unnamed"
            continue
        m = REPEAT.match(name)
        if m and int(m.group(2)) >= 2 and m.group(1) in names:
            groups.setdefault(m.group(1), [m.group(1)]).append(name)
    for members in groups.values():
        unfixed = [n for n in members if n not in fixed]
        if len(unfixed) > 1:
            problems.update({n: "duplicate" for n in unfixed})
    return problems


def _apply_client_names(auto: list[str], column_names: dict[str, str] | None) -> list[str]:
    """
    Step 0: apply the client's own column names.

    Each new name is cleaned like every other name (`snake_case`, "col_" in front
    of a leading digit). Keys that are not in `auto` (columns no longer in the
    file) are ignored.

    Args:
        auto: list[str]: the automatic Step 0 names, in table order.
        column_names: dict[str, str] | None: {automatic name: new name} from the
            client; None or {} changes nothing.

    Returns:
        list[str]: the final names, same order as `auto`.

    Raises:
        ValueError: (shown to the client) for a name with no letters or digits, or
            when two columns would end up with the same name.

    Example:
        >>> _apply_client_names(["amount", "amount_2"], {"amount_2": "Amount Refunded"})
        ['amount', 'amount_refunded']
    """
    final = list(auto)
    for key, new in (column_names or {}).items():
        if key not in auto:
            continue                       # a column that's no longer in the file
        clean = snake_case(new)
        if not clean:
            raise ValueError(f"'{new}' can't be used as a column name — use letters or numbers.")
        if clean[0].isdigit():
            clean = f"col_{clean}"
        final[auto.index(key)] = clean
    repeats = sorted({n for n in final if final.count(n) > 1})
    if repeats:
        raise ValueError(f"Two columns can't share a name: {', '.join(repeats)}. Pick a different name.")
    return final


def _infer_type(series: pd.Series) -> str:
    """
    Return a friendly type name for a column (used only for reporting).

    Missing values are ignored. bool -> "true/false", numbers -> "number",
    datetimes -> "date". A text column is checked on its first 500 values: if
    95%+ are numbers once "$", ",", "%" and spaces are removed it is "number
    (stored as text)"; if 95%+ parse as dates it is "date (stored as text)";
    otherwise "text". No values at all -> "empty".

    Args:
        series: pd.Series: the column to describe.

    Returns:
        str: "empty", "true/false", "number", "date", "number (stored as text)",
        "date (stored as text)" or "text".

    Example:
        >>> _infer_type(pd.Series(["$1,200", "$15"])), _infer_type(pd.Series([True, False]))
        ('number (stored as text)', 'true/false')
    """
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
    """
    Step 1: trim spaces in text columns and convert blanks to NaN.

    Only object/string columns are touched, and only cells that really are
    strings (numbers mixed into a text column are left alone). A cell that is
    empty after trimming becomes NaN, so it counts as missing. Modifies `df` in place.

    Args:
        df: pd.DataFrame: the working copy of the table.

    Returns:
        tuple[pd.DataFrame, int]: the tidied table and how many blank text cells
        were turned into missing values.

    Example:
        >>> df, n = _tidy_text(pd.DataFrame({"name": [" Acme ", "  ", "Bo"]}))
        >>> df["name"].tolist(), n
        (['Acme', nan, 'Bo'], 1)
    """
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
    Step 3b: turn float columns that only hold whole numbers back into integers.

    Uses pandas' nullable "Int64" type, which allows blanks, so 1001.0 -> 1001
    while missing values stay missing. Skipped for columns with no values, any
    decimal value, or values of 2**53 or more (beyond what a float stores exactly).
    Modifies `df` in place.

    Args:
        df: pd.DataFrame: the working copy of the table.

    Returns:
        list[str]: the changed column names (for the report).

    Example:
        >>> df = pd.DataFrame({"id": [1001.0, None, 1003.0], "price": [1.5, 2.0, None]})
        >>> _restore_whole_numbers(df), str(df["id"].dtype)
        (['id'], 'Int64')
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
    """
    Say whether a column holds real numbers.

    True/false columns are numbers to pandas, but not to people, so they are excluded.

    Args:
        series: pd.Series: the column to test.

    Returns:
        bool: True for int/float columns, False for bool, text, dates, etc.

    Example:
        >>> _is_number(pd.Series([1, 2])), _is_number(pd.Series([True]))
        (True, False)
    """
    return pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series)


def _fill_value(series: pd.Series, method: str):
    """
    Work out the value used to fill gaps with `method` ("mean", "median" or "mode").

    Returns None when the method doesn't fit the column:
    - ID / code columns (name matches `ID_NAME`) and empty columns: never filled
    - mean / median: numbers only
    - mode: anything except dates (ties: the first value in sort order)
    Whole-number columns (pandas "Int64") get a rounded mean or median.

    Args:
        series: pd.Series: the column to fill (its `.name` is checked for IDs).
        method: str: "mean", "median" or "mode"; anything else gives None.

    Returns:
        The fill value (float, int, str, ...), or None when the method doesn't fit.

    Example:
        >>> _fill_value(pd.Series([1, 2, None, 10], name="qty"), "median")
        2.0
        >>> _fill_value(pd.Series([1, 2, None], name="customer_id"), "mean") is None
        True
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
    """
    Step 4: the level, plain-English advice, and fill suggestion for one column.

    - No gaps: level "none", no advice.
    - ID / code columns: level "low" (<20%), "review" (20–90%) or "remove" (90%+);
      below 90% the suggestion is "drop_rows" (an ID should never be blank), at
      90%+ the advice is to remove the column. No fill values are offered.
    - Other columns: `fill_options` lists each fill that fits and its value
      (numbers: mean, median, mode; others: mode). Numbers get a skewness, and
      the suggestion is the median when |skewness| >= `SKEW_LIMIT` (0.5), else the
      mean; other columns get the mode. The level is "low", "review" or "remove"
      by the same 20% / 90% limits.

    Args:
        series: pd.Series: the column, before the user's missing-value choices.
        missing_pct: float: percentage (0–100) of rows missing in this column.

    Returns:
        dict: {missing_level, recommendation, suggested_fill, skewness,
        fill_options}, merged into the column's ColumnReport.

    Example:
        >>> a = _missing_advice(pd.Series([1.0, 2.0, 3.0, None], name="price"), 25.0)
        >>> a["missing_level"], a["suggested_fill"], a["fill_options"]
        ('review', 'mean', {'mean': '2', 'median': '2', 'mode': '1'})
    """
    advice = {"missing_level": "none", "recommendation": "", "suggested_fill": None,
              "skewness": None, "fill_options": {}}
    if missing_pct == 0:
        return advice
    if ID_NAME.search(str(series.name)):
        # IDs and codes: never filled, and they shouldn't be missing (NOT NULL).
        # Recommend removing the rows without an ID; the client can look the IDs
        # up from the "rows_missing_id" sheet of the Excel download.
        level = "remove" if missing_pct >= REMOVE_PCT else "review" if missing_pct >= REVIEW_PCT else "low"
        advice["missing_level"] = level
        if level == "remove":
            advice["recommendation"] = (f"{missing_pct:.0f}% missing — this ID column is mostly empty; "
                                        "remove the column if it isn't needed.")
        else:
            advice["suggested_fill"] = "drop_rows"
            advice["recommendation"] = (f"{missing_pct:.1f}% missing — an ID should never be blank, so we recommend "
                                        "removing these rows. They're listed on the \"rows_missing_id\" sheet of the "
                                        "Excel download so you can find the right IDs and upload again.")
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
                          notes: list[str]) -> tuple[pd.DataFrame, dict[str, tuple[str, int]], pd.DataFrame | None]:
    """
    Step 5a: drop or fill columns, or drop rows, as the user chose.

    For each column choice: "keep" does nothing; "auto" means our suggestion
    (`suggested_fill` in `advice`, or keep); "drop" removes the column;
    "drop_rows" removes rows where this column is missing and sets them aside
    for the "rows_missing_id" sheet; "mean" / "median" / "mode" fill the gaps
    (if the method doesn't fit, a note is added and the column is left as is).
    Columns not in `df` are ignored.

    Args:
        df: pd.DataFrame: the working table (a copy; it is changed).
        actions: dict[str, str]: column name -> one of `COLUMN_ACTIONS`.
        advice: dict[str, dict]: column name -> its profile, including
            "suggested_fill" from `_missing_advice`.
        notes: list[str]: the report notes; messages are appended to it.

    Returns:
        tuple[pd.DataFrame, dict[str, tuple[str, int]], pd.DataFrame | None]:
        the new data; {column: (what was done, cells filled or rows removed)},
        where "what was done" is "dropped", "rows_dropped", "mean", "median" or
        "mode"; and the rows removed by "drop_rows" (None if none).

    Raises:
        ValueError: for an action that is not in `COLUMN_ACTIONS`.

    Example:
        out, done, removed = _apply_column_actions(df.copy(), {"price": "median", "notes": "drop"},
                                                   profiles, notes)
        done   # {"price": ("median", 3), "notes": ("dropped", 0)}
    """
    done: dict[str, tuple[str, int]] = {}
    set_aside: list[pd.DataFrame] = []
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
        if action == "drop_rows":
            # Like a NOT NULL rule: rows with no value here are set aside.
            missing = df[col].isna()
            if missing.any():
                set_aside.append(df.loc[missing])
                df = df.loc[~missing]
            done[col] = ("rows_dropped", int(missing.sum()))
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
    removed = pd.concat(set_aside) if set_aside else None
    return df, done, removed


def clean_dataframe(
    df: pd.DataFrame,
    dataset_name: str = "dataset",
    missing_strategy: MissingStrategy = "keep",
    column_actions: dict[str, str] | None = None,
    column_names: dict[str, str] | None = None,
    transforms: list[dict] | None = None,
) -> tuple[pd.DataFrame, CleaningReport]:
    """
    Run the full cleaning pipeline (Steps 0–7 above).

    `column_actions` maps column (its key or current name) -> "keep" | "drop" |
    "mean" | "median" | "mode" | "auto" | "drop_rows" (Step 5a).
    `column_names` maps column key -> the client's new name (Step 0).
    `transforms` is the ordered list of steps (Steps 0b and 1b, transforms.py).
    Leave them out to change nothing.

    The caller's DataFrame is never modified. Columns are profiled (missing
    counts, advice, outliers, format issues) BEFORE the missing-value choices,
    so the report always shows where the gaps were.

    Args:
        df: pd.DataFrame: the table as read from the file.
        dataset_name: str: name shown in the report (default "dataset").
        missing_strategy: "keep" | "drop_any": Step 5b; "drop_any" removes every
            row that still has a missing value after Step 5a.
        column_actions: dict[str, str] | None: per-column missing-value choices.
        column_names: dict[str, str] | None: the client's new column names.
        transforms: list[dict] | None: the client's transform steps.

    Returns:
        tuple[pd.DataFrame, CleaningReport]: (cleaned_dataframe, report). The
        cleaned data has a fresh 0..n-1 index. `report.runtime_seconds` is the
        wall-clock processing time, which is what runtime billing is based on.

    Raises:
        ValueError: (plain English, shown to the client) for an invalid transform
            step, a bad or repeated client column name, or an unknown column action.

    Example:
        >>> raw = pd.DataFrame({"Order ID": [1, 1, 2], "Amount": [" 5 ", " 5 ", ""]})
        >>> clean, report = clean_dataframe(raw)
        >>> list(clean.columns), report.duplicate_rows_removed, report.blank_strings_converted
        (['order_id', 'amount'], 1, 1)
    """
    started = time.perf_counter()
    notes: list[str] = []
    rows_before = int(len(df))
    out = df.copy()  # never touch the caller's data
    steps = tf.validate_steps(transforms)

    # Step 0b — header steps ("Use first row as headers", "Remove top rows").
    out, applied = tf.apply_raw_steps(out, steps, dedupe_column_names, keep=METADATA_COLUMNS)

    # Step 0 — lowercase snake_case column names (our own metadata columns keep
    # theirs), then the client's own names, then the header checks.
    originals = [str(c) for c in out.columns]
    auto = [c if c in METADATA_COLUMNS else n for c, n in zip(originals, clean_column_names(originals))]
    final = _apply_client_names(auto, column_names)
    key_of = dict(zip(final, auto))                 # final name -> stable key
    final_of = dict(zip(auto, final))               # key -> final name
    # Renaming a column or removing it both fix its header problem.
    removing = {key_of.get(c, c) for c, a in (column_actions or {}).items() if a == "drop"}
    problems = _header_problems(auto, {a for a, f in zip(auto, final) if a != f} | removing)
    def as_in_file(o: str) -> str:
        """
        Show the header as the client typed it in the file.

        A blank header (unnamed_column_<n>) becomes "", and a repeat made unique by
        file_reader ("Amount_2" next to "Amount") goes back to "Amount".

        Args:
            o: str: the column name after the header steps, before Step 0.

        Returns:
            str: the original header text ("" when it was blank).

        Example:
            as_in_file("unnamed_column_3")   # ""
            as_in_file("Amount_2")           # "Amount" (when "Amount" is also a column)
        """
        if UNNAMED.match(o):
            return ""
        m = REPEAT.match(o)
        return m.group(1) if m and int(m.group(2)) >= 2 and m.group(1) in originals else o
    shown_original = {f: as_in_file(o) for f, o in zip(final, originals)}
    renamed = {o: f for o, f in zip(originals, final) if o != f and not UNNAMED.match(o)}
    out.columns = final
    if renamed:
        notes.append(f"Column names cleaned (lowercase, underscores instead of spaces) for "
                     f"{len(renamed):,} column{'s' if len(renamed) != 1 else ''}.")
    unnamed_cols = [final_of[a] for a, p in problems.items() if p == "unnamed"]
    dup_groups: dict[str, list[str]] = {}
    for a, p in problems.items():
        if p == "duplicate":
            m = REPEAT.match(a)
            base = m.group(1) if m and m.group(1) in final_of else a
            dup_groups.setdefault(base, []).append(final_of[a])
    duplicate_cols = [sorted(g, key=final.index) for g in dup_groups.values()]
    if unnamed_cols:
        notes.append(f"{len(unnamed_cols)} column{' has' if len(unnamed_cols) == 1 else 's have'} no name in your "
                     f"file ({', '.join(unnamed_cols)}). Name {'it' if len(unnamed_cols) == 1 else 'them'}, remove "
                     f"{'it' if len(unnamed_cols) == 1 else 'them'}, or fix the header row and upload again.")
    for g in duplicate_cols:
        notes.append(f"These columns had the same name: {', '.join(g)}. Rename them so the analysis can tell them "
                     "apart (for example amount_paid and amount_refunded).")
    # Choices may name a column by its key or its current name.
    if column_actions:
        column_actions = {final_of.get(c, c): a for c, a in column_actions.items()}

    # Step 1 — tidy text.
    out, blanks_converted = _tidy_text(out)
    if blanks_converted:
        cells = "cell was" if blanks_converted == 1 else "cells were"
        notes.append(f"{blanks_converted:,} blank text {cells} treated as missing values.")

    # Step 1b — the client's transform steps, in order.
    out, more = tf.apply_steps(out, steps, resolve=lambda c: final_of.get(c, c), clean_names=clean_column_names)
    applied = sorted(applied + more, key=lambda s: s["step"])   # in the client's order
    # Columns the client already set an unusual-values rule for aren't flagged
    # again (their own limit wins over the automatic range).
    ruled = {final_of.get(s.get("column", ""), s.get("column", "")) for s in steps if s["op"] == "outliers"}
    notes.extend(s["text"] for s in applied)

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
            "is_id": bool(ID_NAME.search(str(col))),
            "key": key_of.get(str(col), str(col)),
            "original_name": shown_original.get(str(col), str(col)),
            "name_problem": problems.get(key_of.get(str(col), str(col))),
            "outliers": None if str(col) in ruled else tf.outlier_check(series, is_id=bool(ID_NAME.search(str(col)))),
            "format_issue": tf.format_check(series),
            **_missing_advice(series, pct),
        }
    total_cells_pre = n_pre * columns_before
    missing_pct_before = round(int(profiled.isna().sum().sum()) / total_cells_pre * 100, 2) if total_cells_pre else 0.0
    if fully_empty:
        notes.append(f"These columns have no values at all: {', '.join(fully_empty)}.")
    # (IDs are left out of the "review" list: their gaps usually mean "none", which is fine.)
    outlier_cols = [c for c, p in profiles.items() if p["outliers"]]
    format_cols = [c for c, p in profiles.items() if p["format_issue"]]
    for c in outlier_cols:
        o = profiles[c]["outliers"]
        notes.append(f"'{c}' has {o['count']:,} unusual value{'s' if o['count'] != 1 else ''} "
                     f"(expected {o['low']} to {o['high']}; e.g. {', '.join(o['examples'])}). Check them before analyzing.")
    for c in format_cols:
        f = profiles[c]["format_issue"]
        notes.append(f"'{c}' looks like {f['to']}s stored as text"
                     + (f"; {f['failing']:,} value{'s' if f['failing'] != 1 else ''} wouldn't convert "
                        f"(e.g. {', '.join(f['examples'])})." if f["failing"] else "."))
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
    # (Only copy when there are choices to apply: big tables are expensive to copy.)
    removed_for_id = None
    if column_actions:
        out, done, removed_for_id = _apply_column_actions(out.copy(), column_actions, profiles, notes)
    else:
        done = {}
    dropped_cols = [c for c, (a, _) in done.items() if a == "dropped"]
    cells_filled = sum(k for a, k in done.values() if a not in ("dropped", "rows_dropped"))
    rows_missing_id = 0 if removed_for_id is None else len(removed_for_id)
    if rows_missing_id:
        cols = ", ".join(c for c, (a, k) in done.items() if a == "rows_dropped" and k)
        notes.append(f"{_rows(rows_missing_id)} removed because the ID was missing ({cols}). Download Excel to see "
                     "them on the \"rows_missing_id\" sheet, fill in the IDs, and upload again.")
    if dropped_cols:
        notes.append(f"Columns removed at your request: {', '.join(dropped_cols)}.")
    for col, (action, k) in done.items():
        if action not in ("dropped", "rows_dropped") and k:
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
        action, k = done.get(col, ("keep", 0))
        column_reports.append(ColumnReport(
            **prof, action=action,
            filled_count=0 if action in ("dropped", "rows_dropped") else k,
            rows_dropped=k if action == "rows_dropped" else 0,
            missing_after=int(out[col].isna().sum()) if col in out.columns else 0,
        ))

    total_cells = n * len(out.columns)
    total_missing = int(out.isna().sum().sum())

    # Step 7 — enough rows to analyze, before and after cleaning?
    analysis_note = analysis_readiness(rows_before, n)
    if analysis_note:
        notes.append(analysis_note)

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
        analysis_ready=not analysis_note,
        analysis_note=analysis_note,
        columns_renamed=renamed,
        unnamed_columns=unnamed_cols,
        transforms_applied=applied,
        outlier_columns=outlier_cols,
        format_columns=format_cols,
        duplicate_columns=duplicate_cols,
        rows_missing_id_removed=rows_missing_id,
        rows_missing_id=removed_for_id,
        notes=notes,
        runtime_seconds=round(time.perf_counter() - started, 4),
    )
    return out, report


def analysis_readiness(rows_before: int, rows_after: int) -> str:
    """
    Step 7: check there are enough rows to analyze, before and after cleaning.

    Both counts must be at least `MIN_ROWS_FOR_ANALYSIS` (30). The "before" check
    comes first, so a small upload gets the "add more rows" message.

    Args:
        rows_before: int: rows in the file as uploaded.
        rows_after: int: rows left after cleaning (and the user's row choices).

    Returns:
        str: "" when there are enough rows to analyze, else the reason (shown to
        the user; the dashboard and non-cleaning skills are switched off).

    Example:
        >>> analysis_readiness(100, 50)
        ''
        >>> analysis_readiness(10, 10)[:28]
        'This file has only 10 rows —'
    """
    m = MIN_ROWS_FOR_ANALYSIS
    if rows_before < m:
        return (f"This file has only {rows_before:,} row{'s' if rows_before != 1 else ''} — analysis needs at least "
                f"{m}, so the dashboard and analysis skills are off. You can still clean and download it; "
                "add more rows (or book a consultation) to analyze it.")
    if rows_after < m:
        return (f"After cleaning, only {rows_after:,} row{'s' if rows_after != 1 else ''} remain — analysis needs at "
                f"least {m}, so the dashboard and analysis skills are off. Try keeping rows with missing values, "
                "or add more data.")
    return ""


def dataframe_to_records(df: pd.DataFrame, limit: int | None = None) -> list[dict]:
    """
    Convert a DataFrame to JSON-safe rows for the on-screen preview.

    NaN / NaT become `None` (shown as an empty cell), and dates/numpy types
    become plain strings/numbers that JSON understands. Dates use ISO format
    ("2024-01-05T00:00:00.000"). Note an integer column with blanks comes back
    as floats (1.0).

    Args:
        df: pd.DataFrame: the table to show.
        limit: int | None: only the first `limit` rows; None = all rows.

    Returns:
        list[dict]: one dict per row, {column name: value}.

    Example:
        >>> dataframe_to_records(pd.DataFrame({"a": [1, None]}))
        [{'a': 1.0}, {'a': None}]
    """
    view = df if limit is None else df.head(limit)
    # to_json handles numpy types, NaN and dates correctly; read it back as dicts.
    return json.loads(view.to_json(orient="records", date_format="iso"))
