---
name: data-cleaning
description: Clean a client's flat file (CSV, Excel, JSON, Parquet) to this platform's standard — trim text, treat blanks as missing, drop fully empty rows, remove exact duplicate rows, restore whole numbers, report missing values per column with remove/fill advice (20% / 90% rules, mean vs. median by skewness), and fill or drop columns only when the client asks. Use when hand-cleaning a client file outside the web app, when changing api/services/cleaning.py, or when explaining a cleaning report to a client.
---

# Data Cleaning (platform standard)

Data cleaning is the automatic first step of every engagement. This skill is the
written standard; `api/services/cleaning.py` is the code that implements it. **If
you change one, change the other** and update `docs/DATA_CLEANING.md`.

## Principles

1. **Never destroy the raw data.** Work on a copy (`df.copy()`). The client can
   always get back what they gave us.
2. **Only remove what is provably redundant.** Exact duplicates and fully empty
   rows carry no information. Everything else needs business context.
3. **Measure, advise, don't guess.** We never fill in (impute) missing values on
   our own. We report where they are, advise what to do, and fill or remove a
   column only when the client chooses to.
4. **Explain every change** in plain English, with counts.

## Assumptions

- The first row holds the column names.
- Each file is one table (each Excel sheet is treated as its own table).

## Procedure

Run these steps in order. Each maps to a function or block in `cleaning.py`.

| # | Step | pandas | Why |
|---|---|---|---|
| 0a | Remove pictures stored in cells (first) | object / string columns: values that are bytes, or text starting like base64 images (`data:image/`, `iVBORw0KGgo` PNG, `/9j/` JPEG, …) — 50%+ of a column's filled cells → drop the column, else blank those cells | Pictures can't be analyzed and swell the file; recommend a link column (`photo_url`) to cloud storage instead (`image_columns_removed`, `image_cells_removed`) |
| 0 | Clean column names | `clean_column_names(df.columns)`: lowercase, non-letters → `_`, split camelCase, unique; then the client's own names (`column_names`) | `Order Date` → `order_date`, `CustomerID` → `customer_id`; easy to use in Excel, SQL and Python (`columns_renamed`). Flags blank headers (`unnamed_column_4`) and repeated ones (`amount`, `amount_2`) as `name_problem` until the client renames or removes them |
| 0b | Header steps (client) | "Use first row as headers" `df.columns = df.iloc[0]`; "Remove top rows" `df.iloc[n:]` | Title rows in exports — `transforms` (see `api/services/transforms.py`) |
| 1 | Tidy text | `s.str.strip()`; `""` → `NaN` | `" Acme "` and `"Acme"` are the same; blank text is really missing |
| 1b | Transform steps (client) | change type (`pd.to_numeric` / `pd.to_datetime`), replace values, unusual-values rule (`clip` / filter / `NaN`), unpivot (`melt`), pivot (`groupby().unstack()`) | Power Query-style "Applied steps", in order (`transforms_applied`) |
| 2 | Drop fully empty rows | `df[data_cols].isna().all(axis=1)` | Excel exports often end in blank rows |
| 3 | Remove exact duplicates | `df.duplicated(subset=data_cols, keep="first")` | All values equal = a copy |
| 3b | Restore whole numbers | float column with all `% 1 == 0` → `Int64` | IDs like `1001.0` → `1001` |
| 4 | Missing-value report + advice | `df.isna().sum()`, `/ len(df) * 100`, `s.skew()` | Count, %, a level and a fill suggestion per column (below) |
| 5a | Column choices (client) | `df.drop(columns=…)`, `s.fillna(s.mean() / s.median() / s.mode()[0])`, `df[s.notna()]` | Keep (default), remove, fill, or remove rows with no ID (`drop_rows`) — `column_actions`, by cleaned name |
| 5b | Row strategy (client) | `keep` (default) or `dropna(how="any")` after 5a | Warn if > 20% of rows drop |
| 6 | Column profile + checks | inferred type, unique count, 3 example values; **wrong data**: numbers outside Q1 − 3×IQR … Q3 + 3×IQR, dates in the future or before 1900 (`outliers`); **wrong format**: text that is 80%+ numbers or dates (`format_issue`) | Sanity-check for the client; checks only report — fixes are steps the client picks |
| 7 | Enough rows? | `rows_before >= 30 and rows_after >= 30` | Under 30 rows (before OR after cleaning) = clean and download only; no analysis (`analysis_ready`, `analysis_note`) |

### Missing-value rules of thumb (Step 4)

| % missing in a column | Level | Advice |
|---|---|---|
| under 20% | `low` | Keep it, or fill the gaps |
| 20% or more | `review` | Think about removing the column (or fill it — the client decides) |
| 90% or more | `remove` | Remove the column; too little is left to trust or fill |
| 90%+ of **all** cells | — | Recommend a consultation (`consultation_recommended`) |

Fill suggestion:

| Column | Suggest | Why |
|---|---|---|
| Numbers, \|skewness\| < 0.5 | mean | Roughly symmetric, so the average is typical |
| Numbers, \|skewness\| ≥ 0.5 | median | A long tail (e.g. sales) drags the mean; the middle value is more typical |
| Text, categories, true/false | mode | The most common value |
| Dates | nothing | There's no sensible "average date" to fill in |
| IDs and codes (`*_id`, `*_number`, `zip`, `sku`…) | `drop_rows` — remove rows with no ID | Never filled ("an average customer ID" means nothing), and an ID should not be blank (NOT NULL). The removed rows go on the Excel download's `rows_missing_id` sheet so the client can find the right IDs and upload again |

Whole-number columns get a rounded fill value (1001 stays 1001). The column
report keeps the **before** numbers (`missing_count`, `missing_pct`) and adds
`action`, `filled_count`, `rows_dropped` and `missing_after`. "Use our
suggestions" in the app fills only `low` columns, removes rows with a blank ID,
and removes `remove` columns; other `review` columns are the client's call,
because filling a third of a column with one value changes it a lot.

`data_cols` = every column except ones the platform adds (`source_file`), so a
stacked blank row is still "empty" and the same record in two overlapping
exports is still a duplicate.

## What NOT to do automatically

- Do not impute (mean/median/mode/forward-fill) unless the client chooses it —
  then say exactly what was filled and with which value.
- Do not analyze columns with no name or two columns with the same name
  without flagging it: ask the client to name them, remove them, or fix the
  header row and upload again.
- Never fill IDs or codes, whatever is asked. Remove rows with a blank ID only
  when the client chooses it, and always hand those rows back (the
  `rows_missing_id` sheet) — don't silently lose them.
- Do not remove near-duplicates ("Jon Smith" vs "John Smith") — flag them for a
  consultant instead.
- Do not drop columns, even fully empty ones, unless the client chooses it — advise instead.
- Do not change unusual values on your own — a $50,000 order may be real.
  Flag them (3 × IQR) and let the client keep, cap, remove or blank them.
- Do not convert dates or currency without the client choosing it; show which
  values won't convert first.

## Ideas for the next version (consultant-assisted)

- Standardize categorical spelling (`"CA"`, `"Calif."`, `"California"`).
- Near-duplicate detection on name/email columns.

## Reporting to the client

Lead with outcome, then detail:

> "We removed 12 exact duplicate rows and 3 blank rows. 4% of cells are empty —
> mostly in `phone` (31% missing — worth deciding whether you need that column).
> Nothing else was changed."

## Related skills

- `flat-file-intake` — run first when there are several files.
- `sample-size` — run after cleaning, before analysis.
- Anthropic data plugin: `explore-data` (deeper profiling), `validate-data`
  (QA before sharing results).

## Run it (platform implementation)

This procedure is also runnable code: [`skill.py`](skill.py) in this folder (category: **Data Cleaning**).

```bash
python -m data_skills run data-cleaning samples/sales.csv --param KEY=VALUE
```

Web: **Explore your data → Run a skill → Duplicate & missing-value cleanup** (always included) · API: `POST /api/skills/data-cleaning/run`.
Input: one table. Runs on the data as uploaded, before cleaning.

| Option | Type | Default | What it does |
|---|---|---|---|
| `missing_strategy` | choice (keep, drop_any) | keep | Keep rows with gaps, or drop them |
| `column_actions` | json | auto | Per column (cleaned names): keep, drop, mean, median, mode, auto, or drop_rows (IDs), e.g. {"age_group": "mode", "order_id": "drop_rows"} |
| `column_names` | json | auto | Your own column names, by cleaned name, e.g. {"unnamed_column_4": "store_code", "amount_2": "refund_amount"} |
| `transforms` | json | auto | Transform steps in order (like Power Query), e.g. [{"op": "change_type", "column": "order_date", "to": "date"}] |
