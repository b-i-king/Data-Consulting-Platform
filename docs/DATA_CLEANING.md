# Data cleaning and multi-file intake

This page explains what the platform does to a client's files, in the order it
happens. Code: `api/services/file_reader.py`, `categorizer.py`, `cleaning.py`.
Procedures: `.claude/skills/flat-file-intake` and `.claude/skills/data-cleaning`.

## 1. Reading files

| Extension | Reader | Notes |
|---|---|---|
| `.csv` | `pd.read_csv(sep=",")` | |
| `.tsv` | `pd.read_csv(sep="\t")` | |
| `.txt` | `pd.read_csv(sep=None)` | Delimiter auto-detected (comma, tab, pipe, semicolon) |
| `.xlsx` `.xlsm` | `pd.read_excel(engine="openpyxl")` | Every non-empty sheet becomes its own table |
| `.xls` | `pd.read_excel(engine="xlrd")` | |
| `.json` | `pd.read_json` | Falls back to JSON Lines |
| `.parquet` | `pd.read_parquet` | |

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
| Different tables sharing an ID | `orders.customer_id` ↔ `customers.customer_id` | Shown as a suggested link for analysis/dashboards |

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
questions. See `.claude/skills/sample-size/SKILL.md`.
