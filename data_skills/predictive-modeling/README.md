---
name: predictive-modeling
description: Build, validate, and explain a predictive model (forecast, classification, or regression) on cleaned client data with pandas and scikit-learn — problem framing, leakage checks, baseline, cross-validation, metrics, and a plain-English results summary. Use for Data Science & Machine Learning engagements such as sales forecasting, churn risk, demand prediction, or lead scoring.
---

# Predictive Modeling (Data Science & Machine Learning)

A repeatable path from "can you predict X?" to a model a small business can
trust. Prefer the **simplest model that beats the baseline**.

## Prerequisites

1. Data cleaned to the platform standard (`data-cleaning`).
2. `sample-size` verdict is green or yellow for the target — if red, stop and
   say what data is needed.
3. `multivariate-analysis` run on candidate predictors (multicollinearity,
   interactions) when there are many columns.

## Procedure

Every run writes an **analysis report** (`markdown_analysis_report`, Markdown):
each step below, what it decided and why, and how long it took.

### 1. Preprocessing
- Frame the task: **regression** (a number with more than 10 values) or
  **classification** (a category).
- Numbers: fill gaps with the median, then scale.
- Categories: fill gaps with the most common value, then **one-hot encode**
  (safe whether or not the categories have an order). The data is assumed
  cleaned already (Data Cleaning runs first).

### 2. Feature engineering
- Leave out ID-like columns, the target, and anything in `exclude` (leakage:
  columns only known after the fact).
- Drop **constant** and **near-constant** columns (one value on 99%+ of rows).
- Group the numbers that move together and keep **one per group** — Spearman
  rank correlation, then hierarchical clustering (every pair in a group has
  |rho| >= 0.9), the method from the owner's dissertation. The keeper is the
  one most related to a number target (else the first). They'd affect the
  prediction the same way, so the rest are dropped.
- Optional **PCA** (`pca=true`): compress to the components keeping 95% of the variance.

### 3. Split
- Hold out **20%** for the final test — the newest 20% when `date_column` is
  given, otherwise random (stratified for categories).
- Cross-validation on the rest:
  - **leave-one-out** for small data (60 training rows or fewer);
  - **stratified k-fold** (categories) or **k-fold** (numbers) otherwise, with
    **K chosen**: try 3, 5, 10 with the fast linear model and keep the smallest
    K after which the score changes by 0.01 or less (else the largest tried).

### 4. Training (cheapest first)
1. Baseline: the most common category / the median.
2. Logistic / linear regression.
3. Gradient boosting.

Skewed categories (the rarest under 35% of rows) are **weighted** — the same
effect as oversampling the rare ones, without copying rows.

Pick the best by cross-validated **F1** (macro: every category counts, even
rare ones) for categories, **R²** for numbers; report each with its spread
across folds (± standard deviation). For numbers also report **RMSE**, and
check the linear model's errors look normal (D'Agostino-Pearson) — if not, its
exact numbers are less reliable. The baseline is the yardstick.

**Thorough mode** (`thorough=true`, off by default to keep compute low): also
try a **Random Forest** and **tune gradient boosting** with a small grid search
(learning rate × leaf count, 4 settings, 3-fold inside the training rows) —
usually the most accurate, at about 5× the compute.

### 5. Feature importance
Permutation importance on the test rows: shuffle one column at a time and
measure how much worse the model gets. It shuffles the original columns, so it
stays readable with PCA. Associations, not causes.

### 6. Test & summary
- The chosen model on the held-out 20%.
- End with **example predictions**: 10 test rows, predicted vs. actual.
- People see **accuracy** (categories) or the **typical error** (numbers), a
  verdict against guessing, and a **recommendation** (the top drivers to watch).
- F1, R², ROC AUC and the cross-validation scores are in the report.
- Always end with the disclaimer: these are recommendations based on the data
  analyzed, assuming it's accurate and complete; patterns, not causes.

## Wording for clients
"On weeks we held back for testing, the forecast was off by about $420 on
average (baseline: $1,150)." — not "MAE = 420.3".

## Related skills
`sample-size` (before) · `multivariate-analysis` (before) ·
`create-viz` (charts of actual vs predicted) · `validate-data` (QA before delivery).

## Run it (platform implementation)

This procedure is also runnable code: [`skill.py`](skill.py) in this folder (category: **Data Science & Machine Learning**).

```bash
python -m data_skills run predictive-modeling samples/sales.csv --param KEY=VALUE
```

Web: **Explore your data → Run a skill → Forecasts & predictive models** (unlocked when the client chooses **Data Science & Machine Learning**) · API: `POST /api/skills/predictive-modeling/run`.
Input: one table.

| Option | Type | Default | What it does |
|---|---|---|---|
| `target` | column | auto | Column to predict |
| `features` | columns | auto | Columns to use (default: all usable) |
| `exclude` | columns | auto | Columns to leave out (e.g. ones only known after the fact) |
| `date_column` | column | auto | If given, test on the most recent 20% (time split) |
| `pca` | bool | no | Compress the features with PCA (keeps 95% of the variance) |
| `thorough` | bool | no | Also try Random Forest and tune gradient boosting (several times the compute) |
| `max_rows` | int | 50000 | Sample at most this many rows (keeps runs fast) |
