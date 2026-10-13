---
name: data-cleaning
description: Clean a client's flat file (CSV, Excel, JSON, Parquet) to this platform's standard — trim text, treat blanks as missing, drop fully empty rows, remove exact duplicate rows, restore whole numbers, and report missing values per column — without guessing at business rules. Use when hand-cleaning a client file outside the web app, when changing api/services/cleaning.py, or when explaining a cleaning report to a client.
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
3. **Measure, don't guess.** We do not fill in (impute) missing values. We report
   where they are and let the client (or a consultant) decide.
4. **Explain every change** in plain English, with counts.

## Assumptions

- The first row holds the column names.
- Each file is one table (each Excel sheet is treated as its own table).

## Procedure

Run these steps in order. Each maps to a function or block in `cleaning.py`.

| # | Step | pandas | Why |
|---|---|---|---|
| 1 | Tidy text | `s.str.strip()`; `""` → `NaN` | `" Acme "` and `"Acme"` are the same; blank text is really missing |
| 2 | Drop fully empty rows | `df[data_cols].isna().all(axis=1)` | Excel exports often end in blank rows |
| 3 | Remove exact duplicates | `df.duplicated(subset=data_cols, keep="first")` | All values equal = a copy |
| 3b | Restore whole numbers | float column with all `% 1 == 0` → `Int64` | IDs like `1001.0` → `1001` |
| 4 | Missing-value report | `df.isna().sum()`, `/ len(df) * 100` | Count and % missing per column |
| 5 | Missing-value strategy | `keep` (default) or `dropna(how="any")` | Client's choice; warn if > 20% of rows drop |
| 6 | Column profile | inferred type, unique count, 3 example values | Sanity-check for the client |

`data_cols` = every column except ones the platform adds (`source_file`), so a
stacked blank row is still "empty" and the same record in two overlapping
exports is still a duplicate.

## What NOT to do automatically

- Do not impute (mean/median/forward-fill) — needs business context.
- Do not remove near-duplicates ("Jon Smith" vs "John Smith") — flag them for a
  consultant instead.
- Do not drop columns, even fully empty ones — report them.
- Do not remove outliers — a $50,000 order may be real.
- Do not change date or currency formats in place without telling the client.

## Ideas for the next version (consultant-assisted)

- Detect numbers stored as text (`"$1,200"`) and offer to convert.
- Detect dates stored as text and offer to convert.
- Standardize categorical spelling (`"CA"`, `"Calif."`, `"California"`).
- Near-duplicate detection on name/email columns.

## Reporting to the client

Lead with outcome, then detail:

> "We removed 12 exact duplicate rows and 3 blank rows. 4% of cells are empty —
> mostly in `phone` (31% missing). Nothing else was changed."

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

Web: **Explore your data → Run a skill → Duplicate & missing-value cleanup** · API: `POST /api/skills/data-cleaning/run`.
Input: one table. Runs on the data as uploaded, before cleaning.

| Option | Type | Default | What it does |
|---|---|---|---|
| `missing_strategy` | choice (keep, drop_any) | keep | Keep rows with gaps, or drop them |
