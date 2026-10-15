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
    """
    Turn raw file bytes into text, trying common encodings until one works.

    Encodings are tried in the order listed in `_TEXT_ENCODINGS`:
    - "utf-8-sig" first (UTF-8 that may start with an Excel byte-order mark)
    - then plain "utf-8", then Windows-1252 ("cp1252", older Excel CSV exports)
    - finally "latin-1", which can decode ANY byte sequence, so in practice
      the function always returns something

    Args:
        raw: bytes of the uploaded file (already decompressed).

    Returns:
        str: the decoded text.

    Raises:
        FileReadError: if no encoding works (unreachable in practice because
            latin-1 accepts every byte).

    Example:
        >>> _decode_text("Café".encode("cp1252"))
        'Café'
    """
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

    Text inside double quotes is ignored while counting, so "Smith, John" does
    not add an extra comma to its line.

    Args:
        text: the decoded contents of the file (only the first 20 non-blank
            lines are looked at).

    Returns:
        str: the chosen separator: ",", the tab character, "|" or ";".

    Example:
        >>> detect_delimiter("store;amount" + chr(10) + "Main;12")
        ';'
        >>> detect_delimiter("just one column")
        ','
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
    Detect European-style numbers that use a comma as the decimal mark.

    European exports write 1.234,56 instead of 1,234.56 (usually with ";" as
    the separator). True when numbers like 12,5 clearly outnumber 12.5.

    Rules (applied to the first 20,000 characters only):
    - A "decimal comma" is a digit, a comma, then 1–2 digits (",5" or ",56").
    - A "decimal point" is a digit, a period, then 1–2 digits.
    - Three digits after the mark look like a thousands group, so they are not
      counted ("1,234" is not a decimal comma).
    - The file uses decimal commas when they are more than twice as common as
      decimal points.

    Args:
        text: the decoded contents of the file.

    Returns:
        bool: True if the file should be read with decimal="," and thousands=".".

    Example:
        >>> _uses_decimal_comma("price;qty" + chr(10) + "12,5;3" + chr(10) + "7,25;1")
        True
        >>> _uses_decimal_comma("price,qty" + chr(10) + "12.50,3")
        False
    """
    sample = text[:20000]
    # Decimals have 1-2 digits after the mark (,5 or ,56); thousands groups have 3,
    # so "1.000,5" counts as a decimal comma and "1,234.56" as a decimal point.
    commas = len(re.findall(r"\d,\d{1,2}(?!\d)", sample))
    points = len(re.findall(r"\d\.\d{1,2}(?!\d)", sample))
    return commas > 2 * points


def _delimited(sep: str | None) -> Reader:
    """
    Build a reader for CSV-style text files.

    CSV-style text. With `sep=None` the separator is detected by
    detect_delimiter() — also used for .csv files, because many "CSV" exports
    (Excel in Europe and Latin America) actually use semicolons. The usual
    missing-value markers ("", "NA", "N/A", "null", ...) become real nulls.
    Semicolon files with decimal commas (12,5) are read as numbers.

    The first row is always treated as the header row.

    Args:
        sep: the separator to use, or None to detect it from the file's text.

    Returns:
        Reader: a function (file name, raw bytes) -> {file name: DataFrame}.

    Example:
        >>> read_csv = _delimited(None)
        >>> read_csv("sales.csv", b"store,amount" + bytes([10]) + b"Main,12.5")["sales.csv"].shape
        (1, 2)
    """
    def read(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
        """
        Read one delimited text file into a single DataFrame.

        Decodes the bytes, picks the separator (the fixed `sep`, or a detected one),
        and switches pandas to decimal="," / thousands="." when the separator is a
        semicolon and the numbers use decimal commas.

        Args:
            name: the file name; used as the dataset name in the result.
            raw: the file's bytes.

        Returns:
            dict[str, pd.DataFrame]: {name: the parsed table}.
        """
        text = _decode_text(raw)
        delim = sep or detect_delimiter(text)
        extra = {"decimal": ",", "thousands": "."} if delim == ";" and _uses_decimal_comma(text) else {}
        return {name: pd.read_csv(io.StringIO(text), header=0, sep=delim, **extra)}
    return read


def _fixed_width(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """
    Read a fixed-width text file (columns lined up with spaces).

    pandas' read_fwf infers the column widths from the spacing of the first
    rows; the first row is the header.

    Args:
        name: the file name; used as the dataset name in the result.
        raw: the file's bytes.

    Returns:
        dict[str, pd.DataFrame]: {name: the parsed table}.

    Example:
        >>> _fixed_width("old.prn", b"id  amount" + bytes([10]) + b"1   12.5")["old.prn"].columns.tolist()
        ['id', 'amount']
    """
    return {name: pd.read_fwf(io.StringIO(_decode_text(raw)), header=0)}


def _spreadsheet(engine: str) -> Reader:
    """
    Build a reader for Excel / OpenDocument workbooks.

    Every non-empty sheet becomes its own dataset. Sheets that are completely
    blank are dropped. Naming rules:
    - only one non-empty sheet -> the dataset is named after the file
    - several sheets -> each is named "<file> › <sheet name>"

    Args:
        engine: the pandas Excel engine to use, e.g. "openpyxl" (.xlsx/.xlsm),
            "xlrd" (old .xls), or "calamine" (.xlsb and .ods).

    Returns:
        Reader: a function (file name, raw bytes) -> {dataset name: DataFrame}.

    Example:
        >>> read_xlsx = _spreadsheet("openpyxl")
        >>> list(read_xlsx("book.xlsx", raw_bytes))   # a workbook with two filled sheets
        ['book.xlsx › Orders', 'book.xlsx › Customers']
    """
    def read(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
        # sheet_name=None -> {sheet name: DataFrame} for ALL sheets.
        """
        Read every sheet of one workbook, dropping sheets that are entirely empty.

        Args:
            name: the file name; used (alone or with the sheet name) as the dataset name.
            raw: the workbook's bytes.

        Returns:
            dict[str, pd.DataFrame]: {name: DataFrame} for a single sheet, or
            {"<name> › <sheet>": DataFrame, ...} for several.
        """
        sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=0, engine=engine)
        sheets = {k: v for k, v in sheets.items() if not v.dropna(how="all").empty}
        if len(sheets) == 1:
            return {name: next(iter(sheets.values()))}
        return {f"{name} › {sheet}": df for sheet, df in sheets.items()}
    return read


def _json(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """
    Read a JSON file into a table.

    A JSON array of records (or {"key": [records]}); falls back to JSON Lines.
    Nested objects become columns: {"meta": {"k": 1}} -> column "meta.k".

    Rules:
    - a list of records -> one row per record
    - an object holding one or more lists of records -> the FIRST such list
    - any other object -> a single row
    - text that is not valid JSON as a whole -> read as JSON Lines instead

    Args:
        name: the file name; used as the dataset name in the result.
        raw: the file's bytes.

    Returns:
        dict[str, pd.DataFrame]: {name: the flattened table}.

    Raises:
        ValueError: if the JSON Lines fallback also fails (read_file turns this
            into a friendly FileReadError).

    Example:
        >>> _json("orders.json", b'{"orders": [{"id": 1, "meta": {"k": 2}}]}')["orders.json"].columns.tolist()
        ['id', 'meta.k']
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
    """
    Read a JSON Lines / NDJSON file: one JSON object per line.

    Blank lines are skipped and nested objects become dotted columns
    (pandas.json_normalize), just like `_json`.

    Args:
        name: the file name; used as the dataset name in the result.
        raw: the file's bytes.

    Returns:
        dict[str, pd.DataFrame]: {name: one row per line}.

    Raises:
        ValueError: if a line is not valid JSON (json.JSONDecodeError).

    Example:
        >>> _json_lines("log.jsonl", b'{"a": 1}' + bytes([10]) + b'{"a": 2}')["log.jsonl"]["a"].tolist()
        [1, 2]
    """
    rows = [json.loads(ln) for ln in _decode_text(raw).splitlines() if ln.strip()]
    return {name: pd.json_normalize(rows)}


def _flatten_cells(df: pd.DataFrame) -> pd.DataFrame:
    """
    Store lists or dicts found inside cells as JSON text.

    Lists or dicts left inside cells (JSON, XML, Parquet can hold them) can't
    be compared, so duplicate checks would fail. Store them as JSON text.

    Only object (text-like) columns that contain at least one list or dict are
    touched; other values in those columns are left as they are. Non-ASCII
    characters are kept readable (ensure_ascii=False).

    Args:
        df: the table just read from a file. It is changed in place.

    Returns:
        pd.DataFrame: the same DataFrame, with list/dict cells turned into strings.

    Example:
        >>> _flatten_cells(pd.DataFrame({"tags": [["a", "b"], "plain"]}))["tags"].tolist()
        ['["a", "b"]', 'plain']
    """
    for col in df.columns:
        if df[col].dtype == object and df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v)
    return df


def _friendly_error(fmt_label: str, filename: str, exc: Exception) -> str:
    """
    Turn a parser's technical message into one a business owner can act on.

    Recognized cases, checked in order:
    - empty file -> "'x.csv' is empty..."
    - not really this format (bad zip, corrupt, unsupported) -> suggests the
      file was damaged or renamed, and to re-save or export as CSV
    - pandas' "Expected N fields in line L, saw M" -> names the row and the
      column counts, and hints at unquoted commas
    - encoding problems -> suggests saving as CSV with UTF-8
    - anything else -> "Could not read '<file>' as <format>: <original message>"

    Args:
        fmt_label: the human label of the format, e.g. "CSV" or "Excel".
        filename: the uploaded file's name, quoted in the message.
        exc: the exception the pandas reader raised.

    Returns:
        str: a plain-English message that is safe to show the user.

    Example:
        >>> _friendly_error("CSV", "sales.csv", ValueError("Expected 3 fields in line 7, saw 4"))
        "Row 7 of 'sales.csv' has 4 columns but the header has 3. The file may not be a table, or ..."
    """
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
    """
    Read every <table> in an HTML page as a dataset.

    No links are followed and nothing is downloaded; only the tables already in
    the page are parsed (pandas.read_html). Naming rules:
    - one table -> the dataset is named after the file
    - several -> "<file> › table 1", "<file> › table 2", ...

    Args:
        name: the file name; used as (the start of) the dataset name.
        raw: the page's bytes.

    Returns:
        dict[str, pd.DataFrame]: {dataset name: table}.

    Raises:
        ValueError: if the page has no <table> (pandas' "No tables found").
    """
    tables = pd.read_html(io.StringIO(_decode_text(raw)))
    if len(tables) == 1:
        return {name: tables[0]}
    return {f"{name} › table {i + 1}": t for i, t in enumerate(tables)}


def _xml(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """
    Read an XML file: repeating XML elements become rows.

    Repeating XML elements become rows. parser="etree" (Python's built-in
    parser) never downloads external entities, which keeps untrusted XML safe.

    Args:
        name: the file name; used as the dataset name in the result.
        raw: the file's bytes.

    Returns:
        dict[str, pd.DataFrame]: {name: one row per repeating element}.

    Example:
        >>> xml = b"<rows><row><id>1</id></row><row><id>2</id></row></rows>"
        >>> _xml("data.xml", xml)["data.xml"]["id"].tolist()
        [1, 2]
    """
    return {name: pd.read_xml(io.BytesIO(raw), parser="etree")}


def _binary(fn: Callable[..., pd.DataFrame], **kwargs) -> Reader:
    """
    Wrap a pandas reader that accepts a file-like object of bytes.

    Used for formats pandas can read straight from memory (Parquet, Feather,
    ORC, Stata). Each such file holds exactly one table.

    Args:
        fn: the pandas reader, e.g. pd.read_parquet.
        **kwargs: extra keyword arguments passed through to `fn` on every read.

    Returns:
        Reader: a function (file name, raw bytes) -> {file name: DataFrame}.

    Example:
        >>> read_parquet = _binary(pd.read_parquet)
        >>> read_parquet("sales.parquet", parquet_bytes)   # {"sales.parquet": DataFrame}
    """
    def read(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
        """
        Read one binary file with the wrapped pandas reader.

        Args:
            name: the file name; used as the dataset name in the result.
            raw: the file's bytes, handed to the reader as an in-memory file.

        Returns:
            dict[str, pd.DataFrame]: {name: the parsed table}.
        """
        return {name: fn(io.BytesIO(raw), **kwargs)}
    return read


def _sas(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """
    Read a SAS dataset (.sas7bdat) or SAS transport file (.xpt).

    SAS .sas7bdat or .xpt (transport v5). Without an encoding, pandas returns
    text as raw bytes (b'Ana'); "infer" reads it from the file header, and
    transport files are plain ASCII/Latin-1.

    Args:
        name: the file name; its extension (.xpt or not) picks the SAS format,
            and it is used as the dataset name.
        raw: the file's bytes.

    Returns:
        dict[str, pd.DataFrame]: {name: the parsed table}, with text as str.
    """
    if name.lower().endswith(".xpt"):
        return {name: pd.read_sas(io.BytesIO(raw), format="xport", encoding="latin-1")}
    return {name: pd.read_sas(io.BytesIO(raw), format="sas7bdat", encoding="infer")}


def _spss(name: str, raw: bytes) -> dict[str, pd.DataFrame]:
    """
    Read an SPSS data file (.sav / .zsav).

    SPSS needs a real file path: write to a private temp folder, read, delete.
    This is the ONLY reader that touches disk; the temporary folder (and the
    file in it) is removed as soon as the `with` block ends, even on error.

    Args:
        name: the file name; its extension is kept on the temporary file, and it
            is used as the dataset name.
        raw: the file's bytes.

    Returns:
        dict[str, pd.DataFrame]: {name: the parsed table}.
    """
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
    """
    Read any Excel flavor by choosing the right engine from the extension.

    .xlsx and .xlsm use openpyxl, old .xls uses xlrd, and binary .xlsb uses
    calamine (see `_EXCEL_READERS`). Every non-empty sheet becomes a dataset.

    Args:
        name: the file name; its extension picks the engine.
        raw: the workbook's bytes.

    Returns:
        dict[str, pd.DataFrame]: {dataset name: sheet}, as from `_spreadsheet`.

    Raises:
        KeyError: if the extension is not an Excel one (cannot happen through
            read_file, which only routes Excel extensions here).
    """
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
    """
    The registry as JSON for the upload screen (recommended first).

    One entry per FileFormat, in the order of `FORMATS` (CSV and Excel come
    first). The reader function itself is not included.

    Returns:
        list[dict]: dicts with keys "id", "label", "extensions" (list), "group",
        "recommended" (bool) and "note".

    Example:
        >>> formats_for_ui()[0]
        {'id': 'csv', 'label': 'CSV', 'extensions': ['.csv'], 'group': 'Recommended',
         'recommended': True, 'note': 'Comma-separated values. Every spreadsheet app can save as CSV.'}
    """
    return [
        {"id": f.id, "label": f.label, "extensions": list(f.extensions), "group": f.group,
         "recommended": f.recommended, "note": f.note}
        for f in FORMATS
    ]


# ── Compression ──────────────────────────────────────────────────────────────

def _limited(stream, limit: int, what: str) -> bytes:
    """
    Read at most `limit` bytes; refuse anything larger (zip-bomb guard).

    Reads one byte past the limit: if that byte exists, the content is too big
    and is refused before more is decompressed into memory.

    Args:
        stream: a readable file-like object (e.g. a gzip stream or a file inside
            a zip) with a .read(n) method.
        limit: the maximum number of bytes allowed.
        what: the uploaded file's name, quoted in the error message.

    Returns:
        bytes: everything in the stream (at most `limit` bytes).

    Raises:
        FileReadError: if the stream holds more than `limit` bytes.

    Example:
        >>> _limited(io.BytesIO(b"abc"), 10, "x.gz")
        b'abc'
    """
    data = stream.read(limit + 1)
    if len(data) > limit:
        raise FileReadError(
            f"'{what}' expands to more than {limit // (1024 * 1024)} MB. "
            "Please upload a smaller file or book a consultation."
        )
    return data


def _decompress(name: str, raw: bytes) -> list[tuple[str, bytes]]:
    """
    Unwrap .gz / .bz2 / .xz / .zip files.

    Unwrap .gz / .bz2 / .xz / .zip. Returns [(inner file name, bytes), ...].
    A .zip can hold several files; each supported one is returned.

    Rules:
    - .gz/.bz2/.xz hold one file; its name is the upload name without the
      compression extension ("sales.csv.gz" -> "sales.csv").
    - .zip members are named "<zip name> › <member file name>". Folders, Mac
      metadata ("__MACOSX/", hidden "." files) and unsupported types are skipped.
    - The total decompressed size is capped at settings.max_decompressed_mb.

    Args:
        name: the uploaded file's name (ends in a compression extension).
        raw: the compressed bytes.

    Returns:
        list[tuple[str, bytes]]: (inner file name, decompressed bytes) pairs.

    Raises:
        FileReadError: if the content is too big, or a .zip holds no supported files.
        OSError / zipfile.BadZipFile / lzma.LZMAError: for a corrupt archive
            (read_file turns these into FileReadError).

    Example:
        >>> _decompress("sales.csv.gz", gzip.compress(b"a,b"))
        [('sales.csv', b'a,b')]
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
    """
    Return the lowercase final extension, e.g. 'Sales.CSV' -> '.csv'.

    Only the LAST extension counts, so "sales.csv.gz" gives ".gz".

    Args:
        filename: a file name or path.

    Returns:
        str: the extension with its leading dot, lowercased ("" if there is none).

    Example:
        >>> extension_of("Sales.CSV")
        '.csv'
        >>> extension_of("sales.csv.gz")
        '.gz'
    """
    return PurePath(filename).suffix.lower()


def read_file(filename: str, raw: bytes) -> list[LoadedDataset]:
    """
    Parse one uploaded file into one or more datasets.

    Returns a list because one file can hold several tables (sheets, HTML
    tables, files in a zip). Raises UnsupportedFileError / FileReadError with
    a plain-English message that is safe to show to the user.

    Steps:
    1. Refuse pickle/HDF5 on purpose (they can run code; see EXCLUDED_FORMATS).
    2. Compressed files are unwrapped and each inner file is read recursively;
       the resulting datasets keep the ORIGINAL upload name and size.
    3. Otherwise the extension picks a reader from FORMAT_BY_EXTENSION.
    4. Column names are turned into trimmed strings and made unique
       (dedupe_column_names), and list/dict cells become JSON text.

    Args:
        filename: the uploaded file's name (its extension decides the format).
        raw: the file's bytes.

    Returns:
        list[LoadedDataset]: one entry per table found in the file.

    Raises:
        UnsupportedFileError: for excluded or unknown extensions.
        FileReadError: if the file is corrupt, empty, too big once decompressed,
            or cannot be parsed in its format.

    Example:
        >>> ds = read_file("sales.csv", b"date,amount" + bytes([10]) + b"2024-01-05,12.50")
        >>> ds[0].name, ds[0].file_type, ds[0].df.shape
        ('sales.csv', 'CSV', (1, 2))
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
        df.columns = dedupe_column_names([str(c).strip() for c in df.columns])
        df = _flatten_cells(df)
        datasets.append(LoadedDataset(name=name, source_file=filename, file_type=fmt.label, df=df, size_bytes=len(raw)))
    return datasets


# pandas' own names for problem headers: a blank header becomes "Unnamed: 3",
# and a repeated one becomes "amount.1", "amount.2" when reading CSV/Excel.
PANDAS_BLANK = re.compile(r"^Unnamed: \d+(_level_\d+)?$")
PANDAS_REPEAT = re.compile(r"^(.*)\.(\d+)$")


def dedupe_column_names(columns: list[str]) -> list[str]:
    """
    Make column names unique and mark the two header problems.

    Make column names unique and mark the two header problems so the cleaning
    report can ask the client to fix them (api/services/cleaning.py, Step 0):

      * a BLANK header (or pandas' "Unnamed: 3") -> "unnamed_column_4"
        (its position, counting from 1, so the client can find it)
      * a REPEATED header -> "amount", "amount_2", "amount_3"
        (pandas' "amount.1" is turned back into a repeat of "amount" first)

    Duplicate headers break most downstream tools (databases, dashboards), and
    names can also collide after we trim spaces (' id' and 'id'), so this is
    the final safety net for every format.

    A name like "version.2" is only treated as a pandas repeat when "version"
    is also one of the columns; otherwise it is kept as typed.

    Args:
        columns: the column names, in order (already converted to strings).

    Returns:
        list[str]: the new names, same length and order as `columns`.

    Example:
        >>> dedupe_column_names(["id", "", "amount", "amount.1", "amount"])
        ['id', 'unnamed_column_2', 'amount', 'amount_2', 'amount_3']
    """
    present = set(columns)
    seen: dict[str, int] = {}
    result = []
    for position, col in enumerate(columns, start=1):
        if not col or PANDAS_BLANK.match(col):
            result.append(f"unnamed_column_{position}")
            continue
        repeat = PANDAS_REPEAT.match(col)
        if repeat and repeat.group(1) in present:
            col = repeat.group(1)          # "amount.1" is really a second "amount"
        if col in seen:
            seen[col] += 1
            result.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 1
            result.append(col)
    return result
