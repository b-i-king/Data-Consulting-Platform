# Data cleaning and multi-file intake

This page explains what the platform does to a client's files, in the order it
happens. Code: `api/services/file_reader.py`, `categorizer.py`, `cleaning.py`.
Procedures: `data_skills/flat-file-intake` and `data_skills/data-cleaning`.

## 1. Reading files

Every format comes from one registry, `FORMATS` in `api/services/file_reader.py`
(based on the [pandas IO guide](https://pandas.pydata.org/docs/user_guide/io.html)).
**CSV and Excel are recommended** and shown first on the upload screen; everything
else sits under "See all supported formats".

| Group | Formats (extensions) | pandas reader |
|---|---|---|
| ⭐ Recommended | CSV (`.csv`) · Excel (`.xlsx` `.xlsm` `.xls` `.xlsb`) | `read_csv`, `read_excel` (every sheet) |
| Spreadsheets & text | OpenDocument (`.ods`) · Tab-separated (`.tsv` `.tab`) · Delimited text (`.txt` `.dat` `.psv`) · Fixed-width (`.fwf` `.prn`) | `read_excel(engine="calamine")`, `read_csv`, `read_fwf` |
| Web & data exchange | JSON (`.json`) · JSON Lines (`.jsonl` `.ndjson`) · HTML tables (`.html` `.htm`) · XML (`.xml`) | `read_json`, `read_html` (every table), `read_xml(parser="etree")` |
| Big-data & columnar | Parquet (`.parquet` `.pq`) · Feather/Arrow (`.feather` `.arrow`) · ORC (`.orc`) | `read_parquet`, `read_feather`, `read_orc` |
| Statistical software | Stata (`.dta`) · SAS (`.sas7bdat` `.xpt`) · SPSS (`.sav` `.zsav`) | `read_stata`, `read_sas`, `read_spss` |
| Compressed | `.zip` (may hold several files) · `.gz` · `.bz2` · `.xz` | unwrapped first, then read as above |

**Refused on purpose:** pickle (`.pkl`) and HDF5 (`.h5`) — opening them with pandas
can run code hidden in the file. SQL databases and BigQuery are not files; they
come through connectors (see CONNECTORS.md).

**Safety limits:** compressed files may expand to at most `MAX_DECOMPRESSED_MB`
(default 200 MB) to block "zip bombs"; XML is parsed without fetching external
entities; SPSS is the one format written to a private temp folder (its reader
needs a path) and deleted immediately.

**Delimiters:** for `.txt`/`.dat`/`.psv`, the separator is the one of comma,
tab, pipe, or semicolon that splits every line into the same number of columns.
(pandas' own "sniffer" sometimes chose a letter — e.g. splitting "customer" on "u".)

- **Header row:** the first row is assumed to hold column names.
- **Encodings tried:** UTF-8 (with/without BOM), Windows-1252, Latin-1 — covers
  most US exports, including older Excel "Save as CSV" files.
- **Column names:** trimmed of spaces; duplicates made unique (`id`, `id.1`).

## 2. One file or many?

**One table** → straight to cleaning.

**Several tables** → grouped by how many columns they share:

| Situation | Example | What happens |
|---|---|---|
| Same columns, different years | `sales_2023.csv`, `sales_2024.csv` | Labeled *partitioned by year*; stacked into one table with a `source_file` column |
| Same columns, no year difference | `store_a.csv`, `store_b.csv` | Labeled *same structure*; stacked the same way |
| Different columns | `orders.csv`, `customers.csv` | Kept separate; each gets an entity label (Transactions, Customers…) |
| Different tables sharing an ID | `orders.customer_id` ↔ `customers.customer_id` | Shown as a suggested link (🔗) for analysis/dashboards |
| Different tables, no shared ID, but names/emails/phones | `sales.customer` ≈ QuickBooks `DisplayName` | Suggested **fuzzy** link (≈), see CONNECTORS.md |

Entity labels are a keyword-based guess and are shown to clients as such.

## 3. Cleaning steps

| Step | What | Example |
|---|---|---|
| 1. Tidy text | Trim spaces; blank text → missing | `" Acme "` → `"Acme"`, `"  "` → missing |
| 2. Empty rows | Drop rows with no values at all | Blank lines at the bottom of a spreadsheet |
| 3. Duplicates | Drop rows identical in every column (keep first) | Same order exported twice |
| 3b. Whole numbers | `1001.0` → `1001` when a column only has whole numbers | ID columns with some blanks |
| 4. Missing report | Count and % missing per column | `phone`: 31% missing |
| 5. Strategy | **Keep** rows with gaps (default) or **remove** them | Client picks on the upload step |
| 6. Profile | Type, unique values, examples per column | `order_date`: date (stored as text) |

Steps 2 and 3 ignore the platform's own `source_file` column, so a blank row is
still blank and a record repeated across two overlapping exports is still a
duplicate.

### What we deliberately do NOT do automatically

Fill in missing values, remove near-duplicates, drop columns, remove outliers, or
reformat dates/currency. All of these need business context — they are part of
the consultant-assisted services.

## 4. Preview and download

- **Preview:** every cleaned row is sent to the browser (up to
  `PREVIEW_MAX_ROWS`, default 50,000) and shown 50 per page.
- **Download:** requires an account. CSV (UTF-8 with BOM so Excel shows accents
  correctly) or Excel (with a second sheet holding the missing-value report).
  Downloads always contain every row.

## 5. "Is there enough data?"

After cleaning, a sample-size snapshot (adapted from Daniel Rosehill's
`sample-size` skill) reports complete rows and green/yellow/red checks for common
questions. See `data_skills/sample-size/SKILL.md`.
