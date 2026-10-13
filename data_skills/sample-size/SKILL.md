---
name: sample-size
description: Describe and assess the sample size of a client dataset — not just row count, but effective sample size for the specific question the client wants answered. Flags underpowered segments, imbalanced classes, small group cells and missingness, and gives a "you can / cannot reliably claim X" verdict. Use before any Data Exploration, Data Science / ML, or dashboard engagement, and when reviewing the automatic snapshot produced by api/services/sample_size.py.
---

# Sample Size

> **Attribution.** Adapted from the `sample-size` skill in
> [Daniel Rosehill's Claude-Data-Analyst-plugin](https://github.com/danielrosehill/Claude-Data-Analyst-plugin/tree/master/skills/sample-size)
> (MIT License, © 2026 Daniel Rosehill). Changes for this project: pandas instead of
> DuckDB as the default tool, a mapping to the platform's automatic snapshot, and
> client-facing wording guidance. See `LICENSE-NOTICE.md` in this folder.

Row count is not sample size. This skill characterizes the *effective* sample a
client has for the *specific* analysis they want, and says whether it is enough.

## How this relates to the platform code

The web app runs the automatic part of this skill on every upload —
`api/services/sample_size.py` (Steps 1, 4 and 5 below) — and shows it on the
"Is there enough data?" panel. Steps 2, 3 and 6 need the client's actual
question, so they happen in a paid **Data Exploration & Analysis** or
**Data Science & ML** engagement, using this skill.

## Inputs

- A dataset: CSV / Excel / Parquet file, or a DataFrame from the platform.
- Optional: the analytical question (e.g. "compare repeat-purchase rate between
  stores", "forecast next quarter's sales", "predict which customers churn").
- Optional: target column, predictor columns, grouping columns, and the effect
  size or precision the client cares about.

## Recommended tooling

- `pandas` — counts, `groupby().size()`, null rates (`df.isna().mean()`).
- `statsmodels.stats.power` — power calculations
  (`TTestIndPower().solve_power(...)`, `NormalIndPower` for proportions).
- Optionally `duckdb` for very large files.

## Procedure

### Step 1 — Raw size description *(automated in the app)*

Report: total rows and columns; non-null rows per column; unique rows; time span
of any date column (first, last, days covered, rows per day); memory footprint.

### Step 2 — Unit of analysis

Ask or infer: what is one row? Customer? Transaction? Store-month?

The honest sample size for most claims is the **number of independent units**,
not the number of rows. 1,000,000 transactions from 50 customers is n = 50 for a
question about customer behavior. Say this plainly — it is the single biggest
source of overclaiming.

### Step 3 — Per-group and per-class cells

For each grouping column or categorical target:
- group sizes (`df[col].value_counts()`)
- **smallest cell** — the binding constraint on any comparison
- imbalance ratio (largest / smallest); flag above 10:1
- for two categorical variables, the smallest cell of the cross-tab; chi-square
  is unreliable when any expected cell is below 5 (use Fisher's exact test)

### Step 4 — Effective n after missingness *(automated: "complete rows")*

For each planned set of columns, count rows where **all** of them are non-null:
`df[cols].dropna().shape[0]`. Flag loudly if more than 20% of rows drop out.

### Step 5 — Adequacy rules of thumb *(automated for the generic questions)*

| Question type | Rough minimum n | Notes |
|---|---|---|
| Single-group mean, SE ≈ SD/10 | ~100 | ±10%-of-SD precision |
| Proportion estimate (±5 pts, 95% CI) | ~385 | Classic survey number |
| Two groups, medium effect (d = 0.5) | ~64 per group | 80% power, α = 0.05 |
| Two groups, small effect (d = 0.2) | ~394 per group | 80% power |
| Chi-square 2×2 | expected cell ≥ 5 | Fisher's exact below that |
| Linear regression | 10–20 rows per predictor | Harrell's rule |
| Logistic regression | 10–20 **events** per predictor | Rare events bite here |
| Any ML model | ≥ 10× predictors, more for flexible models | Validate with cross-validation |
| Time-series forecasting | ≥ 2 full seasonal cycles | e.g. 2 years for yearly seasonality |

When the client states an effect size or precision target, run the matching
statsmodels power calculation and report required n next to observed n.

### Step 6 — Verdict per question

- **Green** — enough data to answer reliably at the stated precision.
- **Yellow** — marginal; wide confidence intervals or only large effects
  detectable. State the smallest effect detectable at 80% power.
- **Red** — underpowered; name the smallest cell or missingness that causes it
  and the n that would be needed.

### Step 7 — Report

Write `outputs/sample-size/report.md` (git-ignored) with: size snapshot, unit of
analysis and honest n, group/cell table with the smallest-cell flag, effective n
per analysis, and one verdict line per question with a plain-English "so what".

**Client wording:** small-business owners are not statisticians. Prefer
"You have enough sales history to compare your two stores, but not enough to
compare individual products yet" over "power = 0.43".

## Notes

- Clustered data (transactions within customers, visits within stores) has a
  design effect that lowers effective n further — suggest mixed-effects or
  cluster-robust methods instead of treating rows as independent.
- For imbalanced classification, report the event count and events per
  predictor — that is the real constraint.
- Pairs with the Anthropic data plugin's `statistical-analysis` and
  `validate-data` skills (see `data_skills/README.md`).

## Run it (platform implementation)

This procedure is also runnable code: [`skill.py`](skill.py) in this folder (category: **Data Strategy & Discovery**).

```bash
python -m data_skills run sample-size samples/sales.csv --param KEY=VALUE
```

Web: **Explore your data → Run a skill → "Is there enough data?" assessment** · API: `POST /api/skills/sample-size/run`.
Input: one table.

| Option | Type | Default | What it does |
|---|---|---|---|
| `unit_column` | column | auto | What one independent unit is (e.g. customer_id) |
| `group_column` | column | auto | Grouping to compare (e.g. store, plan) |
