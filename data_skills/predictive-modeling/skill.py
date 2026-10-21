"""
predictive-modeling — a documented, step-by-step modeling process (Data Science & ML).

Python implementation of README.md in this folder, with scikit-learn. Every run
writes an ANALYSIS REPORT (artifacts["markdown_analysis_report"]) of each step,
what it decided and why, and how long it took:

  1. Preprocessing — frame the task (regression: predict a number; classification:
                     predict a category); median-fill + scale numbers; most-common-fill
                     + ONE-HOT encode categories (safe when it's unclear whether a
                     category is ordered). The data is assumed cleaned already
                     (Data Cleaning runs first).
  2. Feature engineering — drop ID-like columns and `exclude`; drop constant and
                     near-constant columns; GROUP numbers that move together (Spearman
                     correlation + hierarchical clustering, |rho| >= 0.9 — the owner's
                     dissertation method) and keep one per group (the one most related
                     to a number target); optional PCA (`pca=True`, 95% of the variance).
  3. Split         — hold out 20% for the final test (the newest 20% when a date column
                     is given). Cross-validation on the rest: LEAVE-ONE-OUT for small
                     data (<= 60 training rows), else STRATIFIED k-fold for categories /
                     k-fold for numbers, with K CHOSEN: the smallest of 3, 5, 10 after
                     which a bigger K no longer changes the score (by more than 0.01).
  4. Training      — cheapest first: a baseline (most common / median), linear or
                     logistic regression, then gradient boosting. Skewed categories are
                     WEIGHTED (like oversampling the rare ones, without copying rows).
                     The best model is picked by cross-validated F1 (categories; mean ±
                     standard deviation across folds) or R² (numbers; plus RMSE and a
                     check that the linear model's errors look normal).
  5. Importance    — permutation importance on the test set: which ORIGINAL columns the
                     model relies on (still readable with PCA, because whole columns are
                     shuffled). Associations, not causes.
  Optional THOROUGH mode (`thorough=True`, off by default to keep compute low):
                     adds a Random Forest and a small grid search (4 settings) on
                     gradient boosting — usually the most accurate, at several times
                     the compute.
  6. Test & summary — the chosen model on the held-out 20%, ending with example
                     PREDICTIONS (predicted vs. actual) and the recommendation. People see ACCURACY (and,
                     for numbers, the typical error), a recommendation, and DISCLAIMER;
                     F1, R² and the rest are in the analysis report.
"""

from __future__ import annotations

import time

import numpy as np
import pandas as pd
from scipy import stats
from scipy.cluster import hierarchy
from scipy.spatial.distance import squareform
from sklearn.compose import ColumnTransformer
from sklearn.decomposition import PCA
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.ensemble import (HistGradientBoostingClassifier, HistGradientBoostingRegressor, RandomForestClassifier,
                              RandomForestRegressor)
from sklearn.impute import SimpleImputer
from sklearn.inspection import permutation_importance
from sklearn.linear_model import LinearRegression, LogisticRegression
from sklearn.metrics import (accuracy_score, f1_score, mean_absolute_error, mean_squared_error, r2_score,
                             roc_auc_score)
from sklearn.model_selection import (GridSearchCV, KFold, LeaveOneOut, StratifiedKFold, cross_val_predict,
                                     cross_validate, train_test_split)
from sklearn.pipeline import Pipeline, make_pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, as_datetime, bar_chart, categorical_columns, fmt_num,
    numeric_columns,
)

SKILL = SkillSpec(
    id="predictive-modeling",
    title="Forecasts & predictive models",
    description="A documented modeling process: preprocessing, feature engineering, a chosen validation split, "
                "baseline / linear / gradient-boosting models, the top drivers, and a test-set summary.",
    params={
        "target": Param("column", None, "Column to predict"),
        "features": Param("columns", None, "Columns to use (default: all usable)"),
        "exclude": Param("columns", None, "Columns to leave out (e.g. ones only known after the fact)"),
        "date_column": Param("column", None, "If given, test on the most recent 20% (time split)"),
        "pca": Param("bool", False, "Compress the features with PCA (keeps 95% of the variance)"),
        "thorough": Param("bool", False, "Also try Random Forest and tune gradient boosting (several times the compute)"),
        "max_rows": Param("int", 50000, "Sample at most this many rows (keeps runs fast)"),
    },
)

LOO_MAX_ROWS = 60           # leave-one-out only on small training sets (it fits one model per row)
K_CANDIDATES = (3, 5, 10)   # folds tried, smallest first
K_STABLE = 0.01             # a bigger K must move the score more than this to be worth its compute
CORR_DROP = 0.9             # numbers this correlated (Spearman) carry the same information: keep one per group
SKEWED = 0.35               # a category this rare (share of rows) or rarer: weight the classes
NEAR_CONSTANT = 0.99        # a column whose most common value is this share of rows tells nothing
DISCLAIMER = ("These are recommendations based on the data analyzed. They assume the data provided is accurate "
              "and complete; a model finds patterns (associations), not causes. Check important decisions "
              "with someone who knows the business.")


class _Log:
    """The analysis report: one section per step, with how long it took."""

    def __init__(self):
        self.sections: list[str] = []
        self.timings: list[dict] = []
        self._t = time.perf_counter()

    def step(self, title: str, lines: list[str]) -> None:
        """Close a step: its title, what happened, and the seconds since the last step."""
        secs = time.perf_counter() - self._t
        self._t = time.perf_counter()
        n = len(self.timings) + 1
        self.timings.append({"step": f"{n}. {title}", "seconds": round(secs, 2)})
        self.sections.append(f"## {n}. {title}  _({secs:.1f} s)_\n\n" + "\n".join(f"- {x}" for x in lines))

    def markdown(self, header: str) -> str:
        return header + "\n\n" + "\n\n".join(self.sections) + f"\n\n---\n\n_{DISCLAIMER}_\n"


def _engineer(data: pd.DataFrame, num_f: list[str], cat_f: list[str],
              target: pd.Series | None = None) -> tuple[list[str], list[str], list[str]]:
    """
    Step 2: drop columns that can't help — constant, near-constant, and redundant numbers.

    Redundant numbers are found the way the owner's dissertation did it: Spearman
    rank correlation between the number columns, then hierarchical clustering
    (complete linkage on 1 - |rho|), cut so every column in a group has
    |rho| >= CORR_DROP with every other. Each group keeps ONE column: the one most
    related to the target when the target is a number, else the first.

    Args:
        data: pd.DataFrame, the rows used.
        num_f, cat_f: list[str], the number and category feature columns.
        target: pd.Series | None, the target (a number target picks each group's keeper).

    Returns:
        tuple: (numbers kept, categories kept, notes on what was dropped and why).
    """
    notes, drop = [], set()
    for c in num_f + cat_f:
        counts = data[c].value_counts(dropna=False, normalize=True)
        if len(counts) <= 1:
            drop.add(c)
            notes.append(f"Dropped `{c}`: the same value on every row.")
        elif counts.iloc[0] >= NEAR_CONSTANT:
            drop.add(c)
            notes.append(f"Dropped `{c}`: {counts.iloc[0]:.1%} of rows have the same value.")
    kept_nums = [c for c in num_f if c not in drop]
    if len(kept_nums) > 1:
        rho = data[kept_nums].corr(method="spearman").abs().fillna(0).to_numpy(copy=True)
        np.fill_diagonal(rho, 1.0)
        dist = squareform(np.clip(1 - rho, 0, None), checks=False)
        groups = hierarchy.fcluster(hierarchy.linkage(dist, method="complete"), t=1 - CORR_DROP,
                                    criterion="distance")
        numeric_target = target is not None and pd.api.types.is_numeric_dtype(target)
        for g in sorted(set(groups)):
            members = [c for c, k in zip(kept_nums, groups) if k == g]
            if len(members) < 2:
                continue
            keep = (max(members, key=lambda c: abs(data[c].corr(target, method="spearman")) or 0)
                    if numeric_target else members[0])
            for c in members:
                if c != keep:
                    drop.add(c)
                    r = rho[kept_nums.index(c), kept_nums.index(keep)]
                    notes.append(f"Dropped `{c}`: moves with `{keep}` (Spearman {r:.2f}) — they'd affect "
                                 "the prediction the same way, so one is kept.")
    return [c for c in num_f if c not in drop], [c for c in cat_f if c not in drop], notes


def _cv_scores(pipe, X, y, cv, is_reg: bool) -> dict:
    """
    Cross-validated scores from out-of-fold predictions (works for leave-one-out too,
    where each fold has one row and F1 / R² can't be computed per fold), plus — for
    k-fold — the F1 / R² of each fold, summarized as a standard deviation (how
    steady the model is; the dissertation reported mean, variance and std).
    """
    pred = cross_val_predict(pipe, X, y, cv=cv)
    out = ({"r2": float(r2_score(y, pred)), "mae": float(mean_absolute_error(y, pred))} if is_reg
           else {"f1": float(f1_score(y, pred, average="macro")), "accuracy": float(accuracy_score(y, pred))})
    if not isinstance(cv, LeaveOneOut):
        folds = cross_validate(pipe, X, y, cv=cv, scoring="r2" if is_reg else "f1_macro")["test_score"]
        out["fold_std"] = float(np.std(folds))
    return out


def _choose_cv(pipe, X, y, is_reg: bool) -> tuple[object, str, list[dict]]:
    """
    Step 3: the cross-validation for this data.

    Leave-one-out for small data; otherwise (stratified) k-fold with the SMALLEST K
    after which the score stops changing (|change| <= K_STABLE), tried with the fast
    linear model so the search costs little.

    Returns:
        tuple: (the splitter, a plain-words description, the K search rows [{"k", "score"}]).
    """
    n = len(X)
    if n <= LOO_MAX_ROWS:
        return LeaveOneOut(), f"leave-one-out ({n} training rows: every row is tested once)", []
    limit = n // 5
    if not is_reg:
        limit = min(limit, int(y.value_counts().min()))
    ks = [k for k in K_CANDIDATES if 2 <= k <= limit] or [2]
    make = (lambda k: KFold(k, shuffle=True, random_state=0)) if is_reg else \
        (lambda k: StratifiedKFold(k, shuffle=True, random_state=0))
    key = "r2" if is_reg else "f1"
    search = [{"k": k, "score": _cv_scores(pipe, X, y, make(k), is_reg)[key]} for k in ks]
    chosen, why = search[-1]["k"], "the largest tried: the score kept moving by more than 0.01"
    for a, b in zip(search, search[1:]):
        if abs(b["score"] - a["score"]) <= K_STABLE:
            chosen, why = a["k"], "the score stopped changing beyond it"
            break
    if len(search) == 1:
        why = "the most this data allows"
    kind = "k-fold" if is_reg else "stratified k-fold"
    return make(chosen), f"{kind}, K = {chosen} ({why})", search


def run(df, target=None, features=None, exclude=None, date_column=None, pca=False, thorough=False,
        max_rows=50000) -> SkillResult:
    """
    Run the documented modeling process (steps 1-6 above) and report it.

    Args:
        df: pd.DataFrame, the table (already cleaned).
        target: str | None, column to predict. Required.
        features: list[str] | None, columns to use (default: all usable columns).
        exclude: list[str] | None, columns to leave out, e.g. ones only known after the fact (leakage).
        date_column: str | None, date column for a time-based test split (newest 20%).
        pca: bool, compress the features with PCA (95% of the variance) before the models.
        thorough: bool, also try a Random Forest and tune gradient boosting with a small grid
            search (learning rate x leaf count, 4 settings) — more accurate, more compute.
        max_rows: int, sample at most this many rows (default 50,000) to keep runs fast.

    Returns:
        SkillResult: summary (accuracy, or the typical error, of the best model vs. the
        baseline); findings (verdict, recommendation, validation used, top drivers, the
        disclaimer); tables "model_comparison", "drivers", "steps" (seconds per step) and
        "k_search"; a bar chart of the top 10 drivers; artifacts["markdown_analysis_report"];
        data {"task", "metric", "best_model", "validation", "test", "disclaimer"}.

    Raises:
        SkillInputError: if no valid target is given, fewer than 50 rows have a target, or no
            usable feature columns remain.

    Example:
        >>> res = run(df, target="churned", exclude=["account_id"])
        >>> res.data["validation"]
        'stratified k-fold, K = 5 (the score stopped changing beyond it)'
    """
    log = _Log()
    if not target or target not in df.columns:
        raise SkillInputError("Choose a 'target' column to predict.")
    data = df.dropna(subset=[target])
    sampled = len(data) > max_rows
    if sampled:
        data = data.sample(max_rows, random_state=0)
    if len(data) < 50:
        raise SkillInputError("Need at least 50 rows with a target value to build a model.")

    # ── 1. Preprocessing ───────────────────────────────────────────────────────
    is_reg = pd.api.types.is_numeric_dtype(data[target]) and data[target].nunique() > 10
    exclude = set(exclude or []) | {target}
    nums = [c for c in numeric_columns(data) if c not in exclude]
    cats = [c for c in categorical_columns(data, max_unique=50) if c not in exclude]
    feats = [f for f in (features or nums + cats) if f not in exclude and f in data.columns]
    if not feats:
        raise SkillInputError("No usable feature columns.")
    num_f = [f for f in feats if f in nums or pd.api.types.is_numeric_dtype(data[f])]
    cat_f = [f for f in feats if f not in num_f]
    task = ("regression — predicting a number" if is_reg
            else f"classification — predicting one of {data[target].nunique()} categories")
    log.step("Preprocessing", [
        f"Target: `{target}` ({task}).",
        f"{len(data):,} rows with a target{' (a random sample, to keep it fast)' if sampled else ''}.",
        f"{len(num_f)} number columns: missing values filled with the median, then scaled.",
        f"{len(cat_f)} category columns: missing values filled with the most common value, then one-hot encoded "
        "(one yes/no column per value — safe whether or not the categories have an order).",
        "The data was cleaned first (Data Cleaning), so duplicates and types are already handled.",
    ])

    # ── 2. Feature engineering ────────────────────────────────────────────────
    num_f, cat_f, notes = _engineer(data, num_f, cat_f, data[target])
    feats = num_f + cat_f
    if not feats:
        raise SkillInputError("Every feature column was constant or redundant — nothing left to learn from.")
    log.step("Feature engineering", [
        *(notes or ["Every column carries information: none dropped."]),
        f"{len(feats)} columns used: " + ", ".join(f"`{f}`" for f in feats) + ".",
        ("PCA: the encoded columns are compressed to the components that keep 95% of the variance."
         if pca else "No PCA (the default): the model sees the original columns, so its drivers are easy to read."),
    ])

    # ── 3. Split ──────────────────────────────────────────────────────────────
    if date_column:
        data = data.assign(_d=as_datetime(data[date_column])).sort_values("_d")
        cut = int(len(data) * 0.8)
        train, test = data.iloc[:cut], data.iloc[cut:]
        holdout = f"time split: trained on rows before {data['_d'].iloc[cut].date()}, tested on the newest 20%"
    else:
        strat = data[target] if not is_reg and data[target].value_counts().min() >= 2 else None
        train, test = train_test_split(data, test_size=0.2, random_state=0, stratify=strat)
        holdout = "random 80/20 split" + (" (stratified: each category kept in proportion)" if strat is not None else "")
    pre = ColumnTransformer([
        ("num", make_pipeline(SimpleImputer(strategy="median"), StandardScaler()), num_f),
        ("cat", make_pipeline(SimpleImputer(strategy="most_frequent"),
                              OneHotEncoder(handle_unknown="ignore", sparse_output=False)), cat_f),
    ])
    steps_before = [("pre", pre)] + ([("pca", PCA(n_components=0.95, svd_solver="full"))] if pca else [])
    X_tr, y_tr, X_te, y_te = train[feats], train[target], test[feats], test[target]
    linear = LinearRegression() if is_reg else LogisticRegression(max_iter=1000)
    cv, validation, k_search = _choose_cv(Pipeline([*steps_before, ("model", linear)]), X_tr, y_tr, is_reg)
    log.step("Split", [
        f"Final test: {holdout} — {len(test):,} rows the models never see while training.",
        f"Cross-validation on the {len(train):,} training rows: {validation}.",
        *([("K tried: " + ", ".join(f"K={r['k']} → {r['score']:.3f}" for r in k_search)
            + f" ({'R²' if is_reg else 'F1'}).")] if k_search else []),
    ])

    # ── 4. Training ───────────────────────────────────────────────────────────
    skewed = (not is_reg) and float(y_tr.value_counts(normalize=True).min()) <= SKEWED
    weight = "balanced" if skewed else None
    if is_reg:
        models = {"Baseline (median)": DummyRegressor(strategy="median"), "Linear regression": linear,
                  "Gradient boosting": HistGradientBoostingRegressor(random_state=0)}
    else:
        models = {"Baseline (most common)": DummyClassifier(strategy="most_frequent"),
                  "Logistic regression": LogisticRegression(max_iter=1000, class_weight=weight),
                  "Gradient boosting": HistGradientBoostingClassifier(random_state=0, class_weight=weight)}
    if thorough:
        # Usually the most accurate, at a cost: a forest, and gradient boosting tuned on a small grid
        # (3-fold inside the training rows, so the search itself stays cheap).
        grid = {"learning_rate": [0.05, 0.1], "max_leaf_nodes": [15, 31]}
        inner = 3 if len(X_tr) >= 30 else 2
        if is_reg:
            models["Random forest"] = RandomForestRegressor(n_estimators=200, random_state=0)
            models["Gradient boosting (tuned)"] = GridSearchCV(HistGradientBoostingRegressor(random_state=0), grid,
                                                               cv=inner, scoring="r2")
        else:
            models["Random forest"] = RandomForestClassifier(n_estimators=200, random_state=0, class_weight=weight)
            models["Gradient boosting (tuned)"] = GridSearchCV(
                HistGradientBoostingClassifier(random_state=0, class_weight=weight), grid, cv=inner, scoring="f1_macro")
    rows, fitted = [], {}
    for name, model in models.items():
        pipe = Pipeline([*steps_before, ("model", model)])
        scores = _cv_scores(pipe, X_tr, y_tr, cv, is_reg)
        pipe.fit(X_tr, y_tr)
        pred = pipe.predict(X_te)
        row = {"model": name}
        if is_reg:
            row.update(cv_r2=scores["r2"], cv_mae=scores["mae"], test_r2=float(r2_score(y_te, pred)),
                       test_mae=float(mean_absolute_error(y_te, pred)),
                       test_rmse=float(np.sqrt(mean_squared_error(y_te, pred))))
            if name == "Linear regression" and len(y_te) >= 8:
                # The linear model's assumption: its errors look normal (D'Agostino-Pearson test).
                row["residuals_normal_p"] = float(stats.normaltest(np.asarray(y_te) - pred).pvalue)
        else:
            row.update(cv_f1=scores["f1"], cv_accuracy=scores["accuracy"],
                       test_accuracy=float(accuracy_score(y_te, pred)),
                       test_f1=float(f1_score(y_te, pred, average="macro")))
            if y_te.nunique() == 2 and hasattr(pipe, "predict_proba"):
                row["test_roc_auc"] = float(roc_auc_score(y_te, pipe.predict_proba(X_te)[:, 1]))
        if "fold_std" in scores:
            row["cv_fold_std"] = scores["fold_std"]
        rows.append(row)
        fitted[name] = pipe
    pick = "cv_r2" if is_reg else "cv_f1"
    best = max(rows[1:], key=lambda r: r[pick])           # the baseline is the yardstick, not a candidate
    base = rows[0]
    linear_row = rows[1]
    normal_note = []
    if is_reg and "residuals_normal_p" in linear_row:
        p = linear_row["residuals_normal_p"]
        normal_note = [f"Linear regression's errors {'look normal' if p >= 0.05 else 'are NOT normal'} "
                       f"(p = {p:.3f}){'' if p >= 0.05 else ': its exact numbers are less reliable; trust the ranking'}."]
    log.step("Training", [
        *(["The categories are skewed (the rarest is under 35% of rows): each class is weighted, like "
           "oversampling the rare ones without copying rows."] if skewed else []),
        *(f"{r['model']}: cross-validated "
          + (f"R² {r['cv_r2']:.3f}, typical error {fmt_num(r['cv_mae'])}" if is_reg
             else f"F1 {r['cv_f1']:.3f}, accuracy {r['cv_accuracy']:.1%}")
          + (f" (± {r['cv_fold_std']:.3f} across folds)" if "cv_fold_std" in r else "") for r in rows),
        *normal_note,
        f"Chosen: **{best['model']}** — the best cross-validated {'R²' if is_reg else 'F1'} "
        "(F1 weighs every category fairly, even rare ones)." if not is_reg else
        f"Chosen: **{best['model']}** — the best cross-validated R².",
    ])

    # ── 5. Feature importance ─────────────────────────────────────────────────
    imp = permutation_importance(fitted[best["model"]], X_te, y_te, n_repeats=5, random_state=0,
                                 scoring="neg_mean_absolute_error" if is_reg else "f1_macro")
    drivers = pd.Series(imp.importances_mean, index=feats).sort_values(ascending=False)
    top = [k for k, v in drivers.head(5).items() if v > 0] or list(drivers.head(3).index)
    log.step("Feature importance", [
        "Permutation importance on the test rows: shuffle one column at a time and measure how much worse the "
        "model gets. " + ("It shuffles the ORIGINAL columns, so it stays readable with PCA." if pca else ""),
        "Top drivers: " + ", ".join(f"`{k}` ({v:.3f})" for k, v in drivers.head(5).items()) + ".",
        "These are associations, not causes.",
    ])

    # ── 6. Test & summary ─────────────────────────────────────────────────────
    if is_reg:
        better = base["test_mae"] - best["test_mae"]
        summary = (f"Best model: {best['model']} — predicts {target} within {fmt_num(best['test_mae'])} on "
                   f"average (guessing the median: {fmt_num(base['test_mae'])}).")
        verdict = (f"Improves on guessing by {fmt_num(better)} per prediction." if better > 0
                   else "Does not beat guessing — these columns don't predict the target well.")
        test = {"mae": best["test_mae"], "rmse": best["test_rmse"], "r2": best["test_r2"]}
    else:
        summary = (f"Best model: {best['model']} — {best['test_accuracy']:.0%} accurate predicting {target} "
                   f"(guessing the most common answer: {base['test_accuracy']:.0%}).")
        gap = best["test_accuracy"] - base["test_accuracy"]
        verdict = ("Beats guessing the most common answer." if gap > 0.01
                   else "About as accurate as guessing the most common answer — treat with caution." if gap >= -0.01
                   else "Less accurate than always guessing the most common answer: it was chosen for F1 (getting "
                        "the rarer categories right), which costs some overall accuracy. Treat with caution.")
        test = {"accuracy": best["test_accuracy"], "f1": best["test_f1"]}
    recommendation = (f"Recommendation: watch {', '.join(top[:3])} — they have the strongest link to {target}, "
                      f"so changes there are the most likely to show up in {target}.")
    log.step("Test & summary", [
        "On the held-out test rows: " + (f"typical error {fmt_num(best['test_mae'])}, RMSE "
                                          f"{fmt_num(best['test_rmse'])}, R² {best['test_r2']:.3f}"
                                          if is_reg else
                                          f"accuracy {best['test_accuracy']:.1%}, F1 {best['test_f1']:.3f}") + ".",
        verdict, recommendation,
    ])

    # Example predictions on test rows the model never saw: what it would say about new rows like these.
    sample = X_te.head(10)
    predicted = fitted[best["model"]].predict(sample)
    predictions = [{"row": int(i) if isinstance(i, (int, np.integer)) else str(i),
                    "actual": (float(a) if is_reg else str(a)), "predicted": (float(p) if is_reg else str(p))}
                   for i, a, p in zip(sample.index, y_te.head(10), predicted)]
    log.sections[-1] += ("\n- Example predictions (test rows): "
                         + "; ".join(f"actual {fmt_num(r['actual']) if is_reg else r['actual']} → predicted "
                                     f"{fmt_num(r['predicted']) if is_reg else r['predicted']}" for r in predictions[:5])
                         + ".")
    findings = [verdict, recommendation, f"Validated with a {holdout} and {validation}.",
                "Top drivers: " + ", ".join(f"{k} ({v:.3f})" for k, v in drivers.head(5).items()),
                DISCLAIMER]
    # What people see: accuracy (or the typical error); F1 / R² live in the analysis report.
    shown = [{"model": r["model"], **({"test_typical_error": r["test_mae"]} if is_reg
                                     else {"test_accuracy": r["test_accuracy"]})} for r in rows]
    report = log.markdown(f"# Analysis report: predicting `{target}`\n\n**{summary}**")
    return SkillResult(skill=SKILL.id, summary=summary, findings=findings,
                       tables={"model_comparison": shown,
                               "drivers": [{"feature": k, "importance": float(v)} for k, v in drivers.items()],
                               "steps": log.timings, "k_search": k_search, "predictions": predictions},
                       charts=[bar_chart("What the model relies on (permutation importance)",
                                         list(drivers.head(10).index), [float(v) for v in drivers.head(10)])],
                       artifacts={"markdown_analysis_report": report},
                       data={"task": "regression" if is_reg else "classification",
                             "metric": "typical error (MAE)" if is_reg else "accuracy",
                             "best_model": best["model"], "validation": validation, "test": test,
                             "disclaimer": DISCLAIMER, "all_scores": rows})
