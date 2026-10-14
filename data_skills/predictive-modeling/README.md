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

### 1. Frame the problem
- **Target**: one column, one row = one unit (customer, week, order).
- **Type**: regression (how much), classification (yes/no, which), or
  time-series forecast (how much, when).
- **Decision**: what the client will do with a prediction, and how wrong is
  "too wrong" (this picks the metric).

### 2. Guard against leakage
Remove any column that would not be known **at prediction time** (e.g.
`cancel_date` when predicting churn). For time data, split by time — never
shuffle the future into the training set.

### 3. Split
- Cross-sectional: `train_test_split(test_size=0.2, stratify=y for classes)`.
- Time series: last 20% of the timeline (or last full season) as test;
  `TimeSeriesSplit` for cross-validation.

### 4. Baseline first
| Problem | Baseline |
|---|---|
| Regression | predict the training mean / median |
| Classification | predict the most common class |
| Forecast | last value, or same period last year (seasonal naive) |

### 5. Models (in this order)
1. Linear / logistic regression (`Pipeline` with `StandardScaler`,
   `OneHotEncoder(handle_unknown="ignore")`).
2. Gradient boosting (`HistGradientBoostingRegressor/Classifier`).
Use 5-fold CV (or `TimeSeriesSplit`); keep the more complex model only if it
beats the simple one by a margin that matters to the business.

### 6. Metrics
- Regression/forecast: MAE (in dollars/units the client understands), MAPE if
  no zeros.
- Classification: precision, recall, ROC AUC; for imbalanced data, PR AUC and
  a confusion matrix at the chosen threshold.

### 7. Explain
Permutation importance (`sklearn.inspection.permutation_importance`) for the
top drivers. Describe them as associations, not causes.

### 8. Report
`outputs/models/<client>-<target>.md`: question, data used, baseline vs model
metric, top drivers, limits, and "how to use this" in two sentences.

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
| `max_rows` | int | 50000 | Sample at most this many rows (keeps runs fast) |
