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

**Delimiters:** for `.csv`, `.txt`, `.dat` and `.psv`, the separator is the one
of comma, tab, pipe, or semicolon that splits every line into the same number of
columns (separators inside quotes, like `"Smith, John"`, don't count). This
matters for `.csv` too: Excel in Europe and Latin America saves "CSV" files with
semicolons. (pandas' own "sniffer" sometimes chose a letter — e.g. splitting
"customer" on "u".)

**Decimal commas:** a semicolon file whose numbers look like `1.234,56` (decimal
comma, dot for thousands) is read as numbers (1234.56). US-style quoted numbers
like `"1,234.56"` in a comma file stay as text, and the report flags them as
"number (stored as text)" — we don't guess.

**JSON:** nested objects become columns (`{"meta": {"k": 1}}` → `meta.k`); a
`{"records": [...]}` wrapper is unwrapped; lists left inside cells are stored as
text so duplicate checks still work (same for XML/Parquet cells).

**Read errors** are explained in plain English — an empty file, a damaged or
renamed Excel file ("doesn't look like a real Excel file…"), or a row with more
columns than the header ("Row 3 … has 2 columns but the header has 1").

- **Header row:** the first row is assumed to hold column names.
- **Encodings tried:** UTF-8 (with/without BOM), Windows-1252, Latin-1 — covers
  most US exports, including older Excel "Save as CSV" files.
- **Column names:** trimmed of spaces; a blank header becomes `unnamed_column_<position>`, and a repeated one becomes `amount`, `amount_2` (pandas' `amount.1` included). Both are flagged for the client to fix (see Step 0 below).

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
| 0. Column names | Lowercase snake_case: spaces/punctuation → `_`, camelCase split, accents removed, names kept unique | `Order Date` → `order_date`, `Unit Price ($)` → `unit_price`, `CustomerID` → `customer_id`, second `Amount` → `amount_2` |
| 1. Tidy text | Trim spaces; blank text → missing | `" Acme "` → `"Acme"`, `"  "` → missing |
| 2. Empty rows | Drop rows with no values at all | Blank lines at the bottom of a spreadsheet |
| 3. Duplicates | Drop rows identical in every column (keep first) | Same order exported twice |
| 3b. Whole numbers | `1001.0` → `1001` when a column only has whole numbers | ID columns with some blanks |
| 4. Missing report + advice | Count, % missing, a level and a fill suggestion per column | `phone`: 31% missing → "think about removing" |
| 5a. Column choices | Per column: **keep** gaps (default), **remove** the column, **fill** with the mean / median / mode, or (ID columns) **remove rows with no ID** | Client picks on the results step, then **Apply changes** |
| 5b. Row strategy | **Keep** rows with gaps (default) or **remove** them (after 5a) | Client picks on the upload step |
| 6. Profile | Type, unique values, examples per column | `order_date`: date (stored as text) |

**Header problems** (Step 0 flags them; the results screen shows a warning and
the fixes):

| Problem | How it looks after reading | What the client can do |
|---|---|---|
| A blank header cell (one or several; common in Excel and CSV exports) | `unnamed_column_4` — the number is its position from the left | Type a name, **Remove all unnamed columns** (one button), or fix the header row in the file and upload again |
| Two columns with the same name | `amount`, `amount_2` (pandas' own `amount.1` is turned into this) | Rename them so the analysis can tell them apart, e.g. `amount_paid`, `amount_refunded` |

**Edit column names** (on the results screen) renames any column, 10 per page.
The client's names are sent as `column_names` (`{"0": {"amount_2":
"refund_amount"}}`, by column **key**), cleaned the same way (lowercase,
underscores) and must be unique — a clash is refused with a clear message.
Each column keeps its automatic name as a stable `key`, so missing-value
choices made before a rename still apply. Renaming or removing a column marks
its problem fixed (`name_problem`, `unnamed_columns`, `duplicate_columns` in the
report). Nothing is stored: the names travel with every request, like the
missing-value choices.

Step 0 runs first, so every later step, the column choices (`column_actions`
use the cleaned names), the preview, the skills and the download all use the
new names. The report lists each change in `columns_renamed`, and each column
carries its `original_name` (the app shows "was "Order Date"" under the column,
or "no name in your file"). The platform's own `source_file` column keeps its name.

Steps 2 and 3 ignore the platform's own `source_file` column, so a blank row is
still blank and a record repeated across two overlapping exports is still a
duplicate.

### Missing values: rules of thumb and fill options

| % missing in a column | What we say |
|---|---|
| under 20% | Fine — keep it, or fill the gaps |
| 20% or more | **Review:** think about removing the column (or fill it) |
| 90% or more | **Remove:** too little is left to trust or fill |
| 90%+ of all cells in the table | Book a consultation — a banner links to your booking page |

How gaps would be filled (the suggested method is starred in the dropdown):

- **Numbers:** the **mean** if the values are roughly symmetric (|skewness| below
  0.5), otherwise the **median** — sales and prices usually have a long tail of
  big values that drags the mean up. pandas: `s.skew()`, `s.mean()`, `s.median()`.
- **Text, categories, true/false:** the **mode** (most common value), `s.mode()[0]`.
- **Dates:** no fill.
- **IDs and codes** (`customer_id`, order numbers, zip codes, SKUs): never
  filled — an average ID means nothing. An ID **should not be blank** (like a
  `NOT NULL` rule in a database), so the suggestion is **Remove rows with no
  ID** (`drop_rows`). Removed rows are not lost: the Excel download lists them
  on a **`rows_missing_id`** sheet, so the client can look up the right IDs,
  fill them in, and upload again. (In the sample data a blank `customer_id` is a
  walk-in customer; there, keeping the gaps is a reasonable choice too.)
- Whole-number columns get a rounded value, so `1001` stays `1001`.

**Use our suggestions** removes 90%+ columns, removes rows with a blank ID
(ID columns under 90% missing), and fills small gaps (under 20%). Other columns
in between are left for the client: filling a third of a column with one
value changes the data a lot. Choices are sent as `column_actions` (JSON, by
table: `{"0": {"age_group": "mode"}}`) with every request that needs the data —
analyze, dashboard, skills, download — so all of them see the same cleaned data.
The report keeps the **before** numbers and adds what was done (`action`,
`filled_count`, `rows_dropped`, `missing_after`), plus `rows_missing_id_removed`
for the table.

### Enough rows to analyze? (30-row minimum)

Analysis needs at least **30 rows** — with fewer, averages, trends and tests
are too unreliable to show. The check runs twice:

| When | Example | Message |
|---|---|---|
| Before cleaning (as uploaded) | a 10-row file | "This file has only 10 rows — analysis needs at least 30…" |
| After cleaning (duplicates/empty rows removed, row choices applied) | 35 rows, 10 of them duplicates → 25 | "After cleaning, only 25 rows remain…" |

Below 30 at either point the file can still be **cleaned and downloaded**, but
the dashboard and every skill outside Data Cleaning are switched off for that
table, on screen and on the server (`api/services/skills_runner.py` refuses
them). The report carries `analysis_ready` and `analysis_note`. For skills that
use several tables (e.g. an ER diagram), at least one table must have 30+ rows.

### What we deliberately do NOT do automatically

Fill in missing values or drop columns without the client choosing to, fill IDs,
remove near-duplicates, remove outliers, or reformat dates/currency. These need
business context — they are the client's call or part of the consultant-assisted
services.

## 4. Preview and download

- **Summary tiles:** rows (before → after), **fields** (columns; how many were
  removed), duplicates removed, and % of cells missing (how many were filled).
- **Preview:** the first 5 cleaned rows, like pandas' `df.head()`
  (`PREVIEW_MAX_ROWS`, default 5 — raise it to page through more on screen).
  Kept short because the cleaning re-runs each time a missing-value choice is applied.
- **10 fields per page:** the "Missing values by column" table and the preview
  show 10 columns at a time, with First / Previous / Next / Last buttons.
- **Download:** requires an account. CSV (UTF-8 with BOM so Excel shows accents
  correctly) or Excel (with a second sheet holding the missing-value report, and
  a `rows_missing_id` sheet when rows were removed for a blank ID).
  Downloads always contain every row. Before it starts, a confirmation shows
  rows × fields, the estimated wait and the total charge (see
  [PRICING.md](PRICING.md) → Estimates and confirmations).

## 5. "Is there enough data?"

After cleaning, a sample-size snapshot (adapted from Daniel Rosehill's
`sample-size` skill) reports complete rows and green/yellow/red checks for common
questions. See `data_skills/sample-size/README.md`.
