"""
Flat-file reader: turn an uploaded file into a pandas DataFrame.

WHAT THIS FILE DOES
-------------------
The "flat file" path is the simplest way into the platform. A user uploads
one or more files; this module figures out the file type from its extension
and calls the matching pandas reader (`pd.read_csv`, `pd.read_excel`, ...).

KEY RULES (these are assumptions — see docs/DATA_CLEANING.md)
-------------------------------------------------------------
* The FIRST ROW holds the column names (the "header row").
* Files are read entirely IN MEMORY from the uploaded bytes. Nothing is
  written to disk and nothing is kept after the request finishes. This is
  what lets us process data "without storing it".
* Excel workbooks with several sheets: every sheet becomes its own dataset
  (named "<file name> › <sheet name>") so the multi-file categorizer can
  treat sheets just like separate files.

SUPPORTED FORMATS
-----------------
    .csv  .tsv  .txt       -> pd.read_csv   (delimiter auto-detected for .txt)
    .xlsx .xlsm .xls       -> pd.read_excel (openpyxl / xlrd engines)
    .json                  -> pd.read_json  (records or JSON-lines)
    .parquet               -> pd.read_parquet (pyarrow engine)

To add a new format, add its extension to `SUPPORTED_EXTENSIONS` and a branch
in `read_file()`.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import PurePath

import pandas as pd

# Extension -> human-friendly label (also used by the frontend's file picker).
SUPPORTED_EXTENSIONS: dict[str, str] = {
    ".csv": "CSV",
    ".tsv": "Tab-separated",
    ".txt": "Delimited text",
    ".xlsx": "Excel",
    ".xlsm": "Excel (macro-enabled)",
    ".xls": "Excel 97-2003",
    ".json": "JSON",
    ".parquet": "Parquet",
}

# Encodings tried in order for text files. Most US business exports are
# UTF-8; older Excel "Save as CSV" exports are often Windows-1252 ("cp1252").
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


class UnsupportedFileError(ValueError):
    """Raised when a file's extension is not one we know how to read."""


class FileReadError(ValueError):
    """Raised when a supported file cannot be parsed (corrupt, wrong format...)."""


@dataclass
class LoadedDataset:
    """One table read from an upload (a file, or one sheet of a workbook)."""

    name: str              # Display name, e.g. "sales_2023.csv" or "book.xlsx › Customers"
    source_file: str       # Original file name as uploaded
    file_type: str         # Human label, e.g. "CSV"
    df: pd.DataFrame       # The data itself
    size_bytes: int        # Size of the original upload


def extension_of(filename: str) -> str:
    """Return the lowercase extension, e.g. 'Sales.CSV' -> '.csv'."""
    return PurePath(filename).suffix.lower()


def _decode_text(raw: bytes) -> str:
    """Try common encodings until one works (see _TEXT_ENCODINGS)."""
    for enc in _TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # latin-1 can decode any byte sequence, so we never actually get here.
    raise FileReadError("Could not detect the text encoding of this file.")


def _read_delimited(raw: bytes, sep: str | None) -> pd.DataFrame:
    """
    Read CSV/TSV/TXT text.

    `sep=None` + `engine="python"` asks pandas to sniff the delimiter
    (comma, tab, pipe, semicolon...). `dtype=str` would lose numbers, so we
    let pandas infer types, but `keep_default_na=True` keeps the usual
    missing-value markers ("", "NA", "N/A", "null", ...) as real nulls.
    """
    text = _decode_text(raw)
    kwargs = {"sep": sep} if sep else {"sep": None, "engine": "python"}
    return pd.read_csv(io.StringIO(text), header=0, **kwargs)


def read_file(filename: str, raw: bytes) -> list[LoadedDataset]:
    """
    Parse one uploaded file into one or more datasets.

    Returns a list because an Excel workbook can contain several sheets.
    Raises UnsupportedFileError / FileReadError with a plain-English message
    that is safe to show to the user.
    """
    ext = extension_of(filename)
    if ext not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(SUPPORTED_EXTENSIONS)
        raise UnsupportedFileError(f"'{filename}' is not a supported file type. Supported: {allowed}")

    label = SUPPORTED_EXTENSIONS[ext]
    size = len(raw)

    try:
        if ext == ".csv":
            frames = {filename: _read_delimited(raw, ",")}
        elif ext == ".tsv":
            frames = {filename: _read_delimited(raw, "\t")}
        elif ext == ".txt":
            frames = {filename: _read_delimited(raw, None)}
        elif ext in (".xlsx", ".xlsm", ".xls"):
            # sheet_name=None -> dict of {sheet name: DataFrame} for ALL sheets.
            engine = "xlrd" if ext == ".xls" else "openpyxl"
            sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=0, engine=engine)
            # Skip completely empty sheets (common in templates).
            sheets = {k: v for k, v in sheets.items() if not v.dropna(how="all").empty}
            if len(sheets) == 1:
                frames = {filename: next(iter(sheets.values()))}
            else:
                frames = {f"{filename} › {sheet}": df for sheet, df in sheets.items()}
        elif ext == ".json":
            text = _decode_text(raw)
            try:
                frames = {filename: pd.read_json(io.StringIO(text))}
            except ValueError:
                # Fall back to JSON Lines (one JSON object per line).
                frames = {filename: pd.read_json(io.StringIO(text), lines=True)}
        else:  # ".parquet"
            frames = {filename: pd.read_parquet(io.BytesIO(raw))}
    except (UnsupportedFileError, FileReadError):
        raise
    except Exception as exc:  # noqa: BLE001 — turn any parser error into a friendly one
        raise FileReadError(f"Could not read '{filename}': {exc}") from exc

    if not frames:
        raise FileReadError(f"'{filename}' does not contain any data.")

    datasets = []
    for name, df in frames.items():
        # Column names: make sure they are strings and trim stray spaces,
        # e.g. " Email " -> "Email". Duplicated names get a numeric suffix.
        df.columns = _dedupe_column_names([str(c).strip() for c in df.columns])
        datasets.append(LoadedDataset(name=name, source_file=filename, file_type=label, df=df, size_bytes=size))
    return datasets


def _dedupe_column_names(columns: list[str]) -> list[str]:
    """
    Make column names unique: ['id', 'id', 'name'] -> ['id', 'id_2', 'name'].

    Duplicate headers break most downstream tools (databases, dashboards).
    pandas already renames repeats when reading CSVs ('id', 'id.1'), but
    names can still collide after we trim spaces (' id' and 'id'), so this
    is the final safety net for every format.
    """
    seen: dict[str, int] = {}
    result = []
    for col in columns:
        col = col or "unnamed"
        if col in seen:
            seen[col] += 1
            result.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 1
            result.append(col)
    return result
