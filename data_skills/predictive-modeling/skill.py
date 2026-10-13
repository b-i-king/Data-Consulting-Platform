"""
predictive-modeling — baseline vs. model, honestly validated (Data Science & ML).

Python implementation of SKILL.md in this folder, with scikit-learn:

  1. Frame      — regression (predict a number) or classification (predict a
                  category), chosen from the target column's type
  2. Leakage    — drop ID-like columns and any column named in `exclude`
  3. Split      — random 80/20, or by time (last 20%) if a date column is given
  4. Baseline   — mean/median (regression) or most-common class (classification)
  5. Models     — linear/logistic regression, then gradient boosting; 5-fold
                  cross-validation on the training set
  6. Metrics    — MAE for numbers (in the target's own units), accuracy and
                  ROC AUC for yes/no; reported for baseline vs. models on the
                  held-out test set
  7. Explain    — permutation importance: the top drivers (associations, not causes)
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import HistGradientBoostingClassifier, HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import accuracy_score, mean_absolute_error, roc_auc_score
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, as_datetime, bar_chart, categorical_columns, fmt_num,
    numeric_columns,
)

SKILL = SkillSpec(
    id="predictive-modeling",
    title="Forecasts & predictive models",
    description="Train baseline, linear, and gradient-boosting models with cross-validation and explain the drivers.",
    params={
        "target": Param("column", None, "Column to predict"),
        "features": Param("columns", None, "Columns to use (default: all usable)"),
        "exclude": Param("columns", None, "Columns to leave out (e.g. ones only known after the fact)"),
        "date_column": Param("column", None, "If given, test on the most recent 20% (time split)"),
        "max_rows": Param("int", 50000, "Sample at most this many rows (keeps runs fast)"),
    },
)


def run(df, target=None, features=None, exclude=None, date_column=None, max_rows=50000) -> SkillResult:
    if not target or target not in df.columns:
        raise SkillInputError("Choose a 'target' column to predict.")
    data = df.dropna(subset=[target])
    if len(data) > max_rows:
        data = data.sample(max_rows, random_state=0)
    if len(data) < 50:
        raise SkillInputError("Need at least 50 rows with a target value to build a model.")
    is_reg = pd.api.types.is_numeric_dtype(data[target]) and data[target].nunique() > 10
    exclude = set(exclude or []) | {target}
    nums = [c for c in numeric_columns(data) if c not in exclude]
    cats = [c for c in categorical_columns(data, max_unique=50) if c not in exclude]
    feats = [f for f in (features or nums + cats) if f not in exclude]
    if not feats:
        raise SkillInputError("No usable feature columns.")
    num_f = [f for f in feats if f in nums or pd.api.types.is_numeric_dtype(data[f])]
    cat_f = [f for f in feats if f not in num_f]

    if date_column:
        data = data.assign(_d=as_datetime(data[date_column])).sort_values("_d")
        cut = int(len(data) * 0.8)
        train, test = data.iloc[:cut], data.iloc[cut:]
        split_note = f"time split: trained before {data['_d'].iloc[cut].date()}, tested after"
    else:
        strat = data[target] if not is_reg and data[target].value_counts().min() >= 2 else None
        train, test = train_test_split(data, test_size=0.2, random_state=0, stratify=strat)
        split_note = "random 80/20 split"

    pre = ColumnTransformer([
        ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), num_f),
        ("cat", make_pipeline(SimpleImputer(strategy="most_frequent"), OneHotEncoder(handle_unknown="ignore", sparse_output=False)), cat_f),
    ])
    X_tr, y_tr, X_te, y_te = train[feats], train[target], test[feats], test[target]

    if is_reg:
        models = {"Baseline (median)": DummyRegressor(strategy="median"), "Linear regression": LinearRegression(),
                  "Gradient boosting": HistGradientBoostingRegressor(random_state=0)}
        scoring, metric_name = "neg_mean_absolute_error", "MAE"
    else:
        models = {"Baseline (most common)": DummyClassifier(strategy="most_frequent"),
                  "Logistic regression": LogisticRegression(max_iter=1000),
                  "Gradient boosting": HistGradientBoostingClassifier(random_state=0)}
        scoring, metric_name = "accuracy", "accuracy"

    rows, fitted = [], {}
    for name, model in models.items():
        pipe = Pipeline([("pre", pre), ("model", model)])
        cv = cross_val_score(pipe, X_tr, y_tr, cv=5, scoring=scoring)
        pipe.fit(X_tr, y_tr)
        pred = pipe.predict(X_te)
        row = {"model": name, "cv_score": float(-cv.mean() if is_reg else cv.mean())}
        if is_reg:
            row["test_MAE"] = float(mean_absolute_error(y_te, pred))
        else:
            row["test_accuracy"] = float(accuracy_score(y_te, pred))
            if y_te.nunique() == 2 and hasattr(pipe, "predict_proba"):
                row["test_roc_auc"] = float(roc_auc_score(y_te, pipe.predict_proba(X_te)[:, 1]))
        rows.append(row)
        fitted[name] = pipe

    key = "test_MAE" if is_reg else "test_accuracy"
    best = min(rows, key=lambda r: r[key]) if is_reg else max(rows, key=lambda r: r[key])
    base = rows[0]
    imp = permutation_importance(fitted[best["model"]], X_te, y_te, n_repeats=5, random_state=0,
                                 scoring="neg_mean_absolute_error" if is_reg else "accuracy")
    drivers = pd.Series(imp.importances_mean, index=feats).sort_values(ascending=False)
    if is_reg:
        better = base["test_MAE"] - best["test_MAE"]
        summary = (f"Best model: {best['model']} — off by {fmt_num(best['test_MAE'])} on average "
                   f"(baseline {fmt_num(base['test_MAE'])}), predicting {target}.")
        verdict = (f"Improves on the baseline by {fmt_num(better)} per prediction." if better > 0
                   else "Does not beat the baseline — the features don't predict the target well.")
    else:
        summary = (f"Best model: {best['model']} — {best['test_accuracy']:.0%} accurate predicting {target} "
                   f"(baseline {base['test_accuracy']:.0%}).")
        verdict = ("Beats the baseline." if best["test_accuracy"] > base["test_accuracy"] + 0.01
                   else "Barely beats guessing the most common answer — treat with caution.")
    findings = [verdict, f"Validated with a {split_note} and 5-fold cross-validation.",
                "Top drivers: " + ", ".join(f"{k} ({v:.3f})" for k, v in drivers.head(5).items()),
                "Drivers are associations, not causes."]
    return SkillResult(skill=SKILL.id, summary=summary, findings=findings,
                       tables={"model_comparison": rows,
                               "drivers": [{"feature": k, "importance": float(v)} for k, v in drivers.items()]},
                       charts=[bar_chart("What the model relies on (permutation importance)",
                                         list(drivers.head(10).index), [float(v) for v in drivers.head(10)])],
                       data={"task": "regression" if is_reg else "classification", "metric": metric_name})
