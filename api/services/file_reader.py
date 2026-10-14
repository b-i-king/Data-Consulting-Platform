"""
Flat-file reader: turn an uploaded file into pandas DataFrames.

WHAT THIS FILE DOES
-------------------
The "flat file" path is the simplest way into the platform. A user uploads
one or more files; this module looks at the extension and calls the matching
pandas reader (see https://pandas.pydata.org/docs/user_guide/io.html).

Every format lives in ONE registry, `FORMATS`, below. Each entry says:
  * which extensions it covers,
  * which group it is shown in on the upload screen,
  * whether it is RECOMMENDED (CSV and Excel — the friendliest for most people),
  * and which function reads it.
The upload screen and the intake API are built from this registry, so adding
a format here is all it takes to support it everywhere.

KEY RULES (these are assumptions — see docs/DATA_CLEANING.md)
-------------------------------------------------------------
* The FIRST ROW holds the column names (the "header row") for table formats.
* Files are read IN MEMORY from the uploaded bytes. The one exception is SPSS:
  its reader only accepts a file path, so the bytes are written to a private
  temporary folder that is deleted as soon as the read finishes.
* Files that hold several tables become several datasets, named
  "<file> › <table>": Excel/ODS sheets, HTML tables, and files inside a .zip.
* Compressed text files work too: sales.csv.gz, export.json.bz2, data.zip ...
  The decompressed size is capped (MAX_DECOMPRESSED_MB) to block "zip bombs".

FORMATS WE DELIBERATELY DO NOT READ (security)
----------------------------------------------
* Pickle (.pkl/.pickle) and HDF5 (.h5/.hdf5): loading these with pandas can
  run code hidden inside the file. On a public upload page that would let a
  stranger run code on our server. Ask clients to export CSV or Parquet.
* SQL databases, BigQuery, the clipboard: not files — databases arrive
  through connectors instead (see docs/CONNECTORS.md).
"""

from __future__ import annotations

import bz2
import gzip
import io
import json
import lzma
import re
import tempfile
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePath

import pandas as pd

from api.core.config import get_settings


class UnsupportedFileError(ValueError):
    """Raised when a file's extension is not one we know how to read."""


class FileReadError(ValueError):
    """Raised when a supported file cannot be parsed (corrupt, wrong format...)."""


@dataclass
class LoadedDataset:
    """One table read from an upload (a file, a sheet, or a file inside a zip)."""

    name: str              # Display name, e.g. "sales_2023.csv" or "book.xlsx › Customers"
    source_file: str       # Original file name as uploaded
    file_type: str         # Human label, e.g. "CSV"
    df: pd.DataFrame       # The data itself
    size_bytes: int        # Size of the original upload


# A reader takes (file name, raw bytes) and returns {table name: DataFrame}.
Reader = Callable[[str, bytes], dict[str, pd.DataFrame]]

# Encodings tried in order for text files. Most US business exports are
# UTF-8; older Excel "Save as CSV" exports are often Windows-1252 ("cp1252").
_TEXT_ENCODINGS = ("utf-8-sig", "utf-8", "cp1252", "latin-1")


def _decode_text(raw: bytes) -> str:
    """Try common encodings until one works (see _TEXT_ENCODINGS)."""
    for enc in _TEXT_ENCODINGS:
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    # latin-1 can decode any byte sequence, so we never actually get here.
    raise FileReadError("Could not detect the text encoding of this file.")


# ── Readers, one per kind of file ─────────────────────────────────────────────

_CANDIDATE_DELIMITERS = (",", "\t", "|", ";")


def detect_delimiter(text: str) -> str:
    """
    Pick the separator for a delimited text file.

    Only the usual separators are considered (comma, tab, pipe, semicolon).
    For each one we count how many times it appears on each of the first 20
    non-blank lines; the winner appears on EVERY line the SAME number of
    times (a consistent column count), with ties going to the most columns.
    Falls back to comma.

    (pandas' built-in "sniffer" can pick a letter as the delimiter on small
    files — e.g. splitting "customer" on "u" — so we use this simpler rule.)
    """
    # Separators inside quotes ("Smith, John") don't count, so drop quoted text first.
    lines = [re.sub(r'"[^"]*"', "", ln) for ln in text.splitlines() if ln.strip()][:20]
    best, best_cols = ",", 0
    for delim in _CANDIDATE_DELIMITERS:
        counts = {ln.count(delim) for ln in lines}
        if len(counts) == 1 and (cols := counts.pop()) > best_cols:
            best, best_cols = delim, cols
    return best


def _uses_decimal_comma(text: str) -> bool:
    """
    European exports write 1.234,56 instead of 1,234.56 (usually with ";" as
    the separator). True when numbers like 12,5 clearly outnumber 12.5.
    """
    sample = text[:20000]
    # Decimals have 1-2 digits after the mark (,5 or ,56); thousands groups have 3,
    # so "1.000,5" counts as a decimal comma and "1,234.56" as a decimal point.
    commas = len(re.findall(r"\d,\d{1,2}(?!\d)", sample))
    points = len(re.findall(r"\d\.\d{1,2}(?!\d)", sample))
    return commas > 2 * points


def _delimited(sep: str | None) -> Reader:
    """
    CSV-style text. With `sep=None` the separator is detected by
    detect_delimiter() — also used for .csv files, because many "CSV" exports
    (Excel in Europe and Latin America) actually use semicolons. The usual
    missing-value markers ("", "NA", "N/A", "null", ...) become real nulls.
    Semicolon files with decimal commas (12,5) are read as numbers.
    """
    def read(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
        text = _decode_text(raw)
        delim = sep or detect_delimiter(text)
        extra = {"decimal": ",", "thousands": "."} if delim == ";" and _uses_decimal_comma(text) else {}
        return {name: pd.read_csv(io.StringIO(text), header=0, sep=delim, **extra)}
    return read


def _fixed_width(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """Fixed-width text (columns lined up with spaces); pandas infers the widths."""
    return {name: pd.read_fwf(io.StringIO(_decode_text(raw)), header=0)}


def _spreadsheet(engine: str) -> Reader:
    """Excel / OpenDocument: every non-empty sheet becomes its own dataset."""
    def read(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
        # sheet_name=None -> {sheet name: DataFrame} for ALL sheets.
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=0, engine=engine)
        sheets = {k: v for k, v in sheets.items() if not v.dropna(how="all").empty}
        if len(sheets) == 1:
            return {name: next(iter(sheets.values()))}
        return {f"{name} › {sheet}": df for sheet, df in sheets.items()}
    return read


def _json(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """
    A JSON array of records (or {"key": [records]}); falls back to JSON Lines.
    Nested objects become columns: {"meta": {"k": 1}} -> column "meta.k".
    """
    text = _decode_text(raw)
    try:
        data = json.loads(text)
    except ValueError:
        return _json_lines(name, raw)
    if isinstance(data, dict):
        # {"records": [...]} -> the first list of records inside; else one row.
        lists = [v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)]
        data = lists[0] if lists else [data]
    return {name: pd.json_normalize(data)}


def _json_lines(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """JSON Lines / NDJSON: one JSON object per line (nested objects become columns)."""
    rows = [json.loads(ln) for ln in _decode_text(raw).splitlines() if ln.strip()]
    return {name: pd.json_normalize(rows)}


def _flatten_cells(df: pd.DataFrame) -> pd.DataFrame:
    """
    Lists or dicts left inside cells (JSON, XML, Parquet can hold them) can't
    be compared, so duplicate checks would fail. Store them as JSON text.
    """
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    return df


def _friendly_error(fmt_label: str, filename: str, exc: Exception) -> str:
    """Turn a parser's technical message into one a business owner can act on."""
    text = str(exc)
    if "No columns to parse" in text or "EmptyDataError" in type(exc).__name__:
        return f"'{filename}' is empty. Please check you picked the right file."
    if "not a zip file" in text.lower() or "unsupported format" in text.lower() or "corrupt" in text.lower():
        return (f"'{filename}' doesn't look like a real {fmt_label} file — it may be damaged, or a different "
                "file type renamed. Open it and save it again, or export it as CSV.")
    m = re.search(r"Expected (\d+) fields in line (\d+), saw (\d+)", text)
    if m:
        return (f"Row {m.group(2)} of '{filename}' has {m.group(3)} columns but the header has {m.group(1)}. "
                "The file may not be a table, or some rows have extra separators (like an unquoted comma).")
    if "codec" in text or "decode" in text.lower():
        return f"We couldn't read the text in '{filename}'. Try saving it as CSV with UTF-8 encoding."
    return f"Could not read '{filename}' as {fmt_label}: {text}"


def _html(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """Every <table> in an HTML page becomes a dataset. No links are followed."""
    tables = pd.read_html(io.StringIO(_decode_text(raw)))
    if len(tables) == 1:
        return {name: tables[0]}
    return {f"{name} › table {i + 1}": t for i, t in enumerate(tables)}


def _xml(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """
    Repeating XML elements become rows. parser="etree" (Python's built-in
    parser) never downloads external entities, which keeps untrusted XML safe.
    """
    return {name: pd.read_xml(io.BytesIO(raw), parser="etree")}


def _binary(fn: Callable[..., pd.DataFrame], **kwargs) -> Reader:
    """Wrap a pandas reader that accepts a file-like object of bytes."""
    def read(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
        return {name: fn(io.BytesIO(raw), **kwargs)}
    return read


def _sas(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """
    SAS .sas7bdat or .xpt (transport v5). Without an encoding, pandas returns
    text as raw bytes (b'Ana'); "infer" reads it from the file header, and
    transport files are plain ASCII/Latin-1.
    """
    if name.lower().endswith(".xpt"):
        return {name: pd.read_sas(io.BytesIO(raw), format="xport", encoding="latin-1")}
    return {name: pd.read_sas(io.BytesIO(raw), format="sas7bdat", encoding="infer")}


def _spss(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """SPSS needs a real file path: write to a private temp folder, read, delete."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"upload{PurePath(name).suffix.lower()}"
        path.write_bytes(raw)
        return {name: pd.read_spss(path)}
    # TemporaryDirectory removes the folder and file when the `with` block ends.


# ── The registry ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FileFormat:
    id: str
    label: str
    extensions: tuple[str, ...]
    group: str              # heading on the upload screen
    recommended: bool       # shown first, with a "Recommended" badge
    note: str               # one line of help for the user
    reader: Reader


FORMATS: tuple[FileFormat, ...] = (
    # Recommended — what most people already have and can open themselves.
    FileFormat("csv", "CSV", (".csv",), "Recommended", True,
               "Comma-separated values. Every spreadsheet app can save as CSV.", _delimited(None)),
    FileFormat("excel", "Excel", (".xlsx", ".xlsm", ".xls", ".xlsb"), "Recommended", True,
               "Every sheet in the workbook is read.", lambda n, r: _excel(n, r)),
    # Other spreadsheets and text.
    FileFormat("ods", "OpenDocument spreadsheet", (".ods",), "Spreadsheets & text", False,
               "LibreOffice / OpenOffice Calc. Every sheet is read.", _spreadsheet("calamine")),
    FileFormat("tsv", "Tab-separated", (".tsv", ".tab"), "Spreadsheets & text", False,
               "Like CSV, separated by tabs.", _delimited("\t")),
    FileFormat("txt", "Delimited text", (".txt", ".dat", ".psv"), "Spreadsheets & text", False,
               "The separator (comma, tab, pipe, semicolon) is detected automatically.", _delimited(None)),
    FileFormat("fwf", "Fixed-width text", (".fwf", ".prn"), "Spreadsheets & text", False,
               "Columns lined up with spaces, common in older systems.", _fixed_width),
    # Web and data-exchange formats.
    FileFormat("json", "JSON", (".json",), "Web & data exchange", False,
               "A list of records, or JSON Lines.", _json),
    FileFormat("jsonl", "JSON Lines", (".jsonl", ".ndjson"), "Web & data exchange", False,
               "One JSON record per line.", _json_lines),
    FileFormat("html", "HTML tables", (".html", ".htm"), "Web & data exchange", False,
               "Every table on the page is read.", _html),
    FileFormat("xml", "XML", (".xml",), "Web & data exchange", False,
               "Repeating elements become rows.", _xml),
    # Columnar / big-data formats (pyarrow).
    FileFormat("parquet", "Parquet", (".parquet", ".pq"), "Big-data & columnar", False,
               "Compact columnar format from data warehouses and Spark.", _binary(pd.read_parquet)),
    FileFormat("feather", "Feather / Arrow", (".feather", ".arrow"), "Big-data & columnar", False,
               "Apache Arrow files.", _binary(pd.read_feather)),
    FileFormat("orc", "ORC", (".orc",), "Big-data & columnar", False,
               "Columnar format from Hadoop / Hive.", _binary(pd.read_orc)),
    # Statistical software.
    FileFormat("stata", "Stata", (".dta",), "Statistical software", False,
               "Stata datasets.", _binary(pd.read_stata)),
    FileFormat("sas", "SAS", (".sas7bdat", ".xpt"), "Statistical software", False,
               "SAS datasets and transport files.", lambda n, r: _sas(n, r)),
    FileFormat("spss", "SPSS", (".sav", ".zsav"), "Statistical software", False,
               "SPSS data files.", _spss),
)

# Each Excel flavor needs its own engine: openpyxl (modern), xlrd (97-2003),
# calamine (binary .xlsb).
_EXCEL_READERS: dict[str, Reader] = {
    ".xlsx": _spreadsheet("openpyxl"),
    ".xlsm": _spreadsheet("openpyxl"),
    ".xls": _spreadsheet("xlrd"),
    ".xlsb": _spreadsheet("calamine"),
}


def _excel(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    return _EXCEL_READERS[PurePath(name).suffix.lower()](name, raw)

# Formats we refuse on purpose, with the reason shown to the user.
EXCLUDED_FORMATS: dict[str, str] = {
    ".pkl": "Pickle files can run hidden code when opened, so we never open them. Please export to CSV or Parquet.",
    ".pickle": "Pickle files can run hidden code when opened, so we never open them. Please export to CSV or Parquet.",
    ".h5": "HDF5 files can contain pickled data that runs code when opened. Please export to CSV or Parquet.",
    ".hdf5": "HDF5 files can contain pickled data that runs code when opened. Please export to CSV or Parquet.",
    ".hdf": "HDF5 files can contain pickled data that runs code when opened. Please export to CSV or Parquet.",
}

# Compression wrappers: "sales.csv.gz" is read as a gzip-compressed CSV.
COMPRESSION_EXTENSIONS = (".gz", ".bz2", ".xz", ".zip")

FORMAT_BY_EXTENSION: dict[str, FileFormat] = {ext: f for f in FORMATS for ext in f.extensions}

# ext -> label, e.g. {".csv": "CSV", ...}. Kept for code that only needs the list.
SUPPORTED_EXTENSIONS: dict[str, str] = {
    **{ext: f.label for ext, f in FORMAT_BY_EXTENSION.items()},
    **{ext: "Compressed" for ext in COMPRESSION_EXTENSIONS},
}


def formats_for_ui() -> list[dict]:
    """The registry as JSON for the upload screen (recommended first)."""
    return [
        {"id": f.id, "label": f.label, "extensions": list(f.extensions), "group": f.group,
         "recommended": f.recommended, "note": f.note}
        for f in FORMATS
    ]


# ── Compression ──────────────────────────────────────────────────────────────

def _limited(stream, limit: int, what: str) -> bytes:
    """Read at most `limit` bytes; refuse anything larger (zip-bomb guard)."""
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise FileReadError(
            f"'{what}' expands to more than {limit // (1024 * 1024)} MB. "
            "Please upload a smaller file or book a consultation."
        )
    return data


def _decompress(name: str, raw: bytes) -> list[tuple[str, bytes]]:
    """
    Unwrap .gz / .bz2 / .xz / .zip. Returns [(inner file name, bytes), ...].
    A .zip can hold several files; each supported one is returned.
    """
    limit = get_settings().max_decompressed_mb * 1024 * 1024
    ext = PurePath(name).suffix.lower()
    inner_name = name[: -len(ext)]
    if ext == ".gz":
        return [(inner_name, _limited(gzip.GzipFile(fileobj=io.BytesIO(raw)), limit, name))]
    if ext == ".bz2":
        return [(inner_name, _limited(bz2.BZ2File(io.BytesIO(raw)), limit, name))]
    if ext == ".xz":
        return [(inner_name, _limited(lzma.LZMAFile(io.BytesIO(raw)), limit, name))]

    # .zip — read every supported file inside, skipping folders and Mac metadata.
    out, total = [], 0
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        for info in zf.infolist():
            member = info.filename
            if info.is_dir() or member.startswith("__MACOSX/") or PurePath(member).name.startswith("."):
                continue
            if extension_of(member) not in FORMAT_BY_EXTENSION:
                continue
            with zf.open(info) as fh:
                data = _limited(fh, limit - total, name)
            total += len(data)
            out.append((f"{name} › {PurePath(member).name}", data))
    if not out:
        raise FileReadError(f"'{name}' does not contain any supported files.")
    return out


# ── Public entry point ───────────────────────────────────────────────────────

def extension_of(filename: str) -> str:
    """Return the lowercase final extension, e.g. 'Sales.CSV' -> '.csv'."""
    return PurePath(filename).suffix.lower()


def read_file(filename: str, raw: bytes) -> list[LoadedDataset]:
    """
    Parse one uploaded file into one or more datasets.

    Returns a list because one file can hold several tables (sheets, HTML
    tables, files in a zip). Raises UnsupportedFileError / FileReadError with
    a plain-English message that is safe to show to the user.
    """
    ext = extension_of(filename)
    if ext in EXCLUDED_FORMATS:
        raise UnsupportedFileError(f"'{filename}': {EXCLUDED_FORMATS[ext]}")

    # Unwrap compression first, then read whatever is inside.
    if ext in COMPRESSION_EXTENSIONS:
        try:
            members = _decompress(filename, raw)
        except FileReadError:
            raise
        except Exception as exc:  # noqa: BLE001 — corrupt archive etc.
            raise FileReadError(f"Could not open '{filename}': {exc}") from exc
        datasets: list[LoadedDataset] = []
        for inner_name, data in members:
            for ds in read_file(inner_name, data):
                ds.source_file, ds.size_bytes = filename, len(raw)
                datasets.append(ds)
        return datasets

    fmt = FORMAT_BY_EXTENSION.get(ext)
    if fmt is None:
        raise UnsupportedFileError(
            f"'{filename}' is not a supported file type. CSV or Excel work best; "
            f"see the full list on the upload screen."
        )

    try:
        frames = fmt.reader(filename, raw)
    except Exception as exc:  # noqa: BLE001 — turn any parser error into a friendly one
        raise FileReadError(_friendly_error(fmt.label, filename, exc)) from exc

    if not frames:
        raise FileReadError(f"'{filename}' does not contain any data.")

    datasets = []
    for name, df in frames.items():
        # Column names: make sure they are strings and trim stray spaces,
        # e.g. " Email " -> "Email". Duplicated names get a numeric suffix.
        df.columns = _dedupe_column_names([str(c).strip() for c in df.columns])
        df = _flatten_cells(df)
        datasets.append(LoadedDataset(name=name, source_file=filename, file_type=fmt.label, df=df, size_bytes=len(raw)))
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
