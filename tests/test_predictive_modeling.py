"""
The documented modeling process (data_skills/predictive-modeling/skill.py):
preprocessing, feature engineering, the chosen validation split, models picked
by F1 / R², drivers, a test-set summary with the disclaimer, and the report.
"""

import numpy as np
import pandas as pd

from data_skills.registry import load_skill

skill = load_skill("predictive-modeling")


def _table(n=400, seed=0):
    """A churn-like table: `churned` follows `visits` and `plan`; `noise` is random; `constant` never changes."""
    rng = np.random.default_rng(seed)
    visits = rng.integers(0, 30, n)
    plan = rng.choice(["basic", "plus", "pro"], n)
    churned = np.where((visits < 8) | ((plan == "basic") & (visits < 14)), "yes", "no")
    return pd.DataFrame({"visits": visits, "visits_x2": visits * 2.0, "plan": plan, "noise": rng.normal(size=n),
                         "constant": 1, "churned": churned})


def test_the_six_steps_are_documented():
    res = skill.run(_table(), target="churned")
    report = res.artifacts["markdown_analysis_report"]
    for step in ("1. Preprocessing", "2. Feature engineering", "3. Split", "4. Training",
                 "5. Feature importance", "6. Test & summary"):
        assert f"## {step}" in report
    assert [s["step"] for s in res.tables["steps"]][0] == "1. Preprocessing"
    assert "one-hot" in report and skill.DISCLAIMER in report
    assert res.findings[-1] == skill.DISCLAIMER
    assert any(f.startswith("Recommendation:") for f in res.findings)


def test_feature_engineering_drops_constant_and_redundant_columns():
    res = skill.run(_table(), target="churned")
    report = res.artifacts["markdown_analysis_report"]
    assert "Dropped `constant`" in report and "Dropped `visits_x2`" in report     # same as visits x 2
    assert {d["feature"] for d in res.tables["drivers"]} == {"visits", "plan", "noise"}


def test_categories_use_stratified_k_fold_chosen_by_the_score_and_are_picked_by_f1():
    res = skill.run(_table(), target="churned")
    assert res.data["task"] == "classification" and res.data["validation"].startswith("stratified k-fold, K = ")
    assert [r["k"] for r in res.tables["k_search"]][0] == 3
    best = res.data["best_model"]
    scores = {r["model"]: r["cv_f1"] for r in res.data["all_scores"][1:]}
    assert scores[best] == max(scores.values())                                   # picked by F1
    # People see accuracy; F1 stays in the report and the data.
    assert set(res.tables["model_comparison"][0]) == {"model", "test_accuracy"}
    assert "f1" in res.data["test"] and "accurate" in res.summary
    assert res.tables["drivers"][0]["feature"] == "visits"                       # the real driver comes first


def test_small_data_uses_leave_one_out():
    res = skill.run(_table(n=70), target="churned")                              # 56 training rows
    assert res.data["validation"].startswith("leave-one-out") and res.tables["k_search"] == []


def test_numbers_use_k_fold_and_pca_keeps_drivers_readable():
    df = _table()
    df["spend"] = df["visits"] * 3.5 + np.random.default_rng(1).normal(scale=2, size=len(df))
    res = skill.run(df.drop(columns=["churned"]), target="spend", pca=True)
    assert res.data["task"] == "regression" and res.data["validation"].startswith("k-fold")
    assert "PCA: the encoded columns" in res.artifacts["markdown_analysis_report"]
    assert res.tables["drivers"][0]["feature"] == "visits"                       # original columns, even with PCA
    assert res.data["test"]["r2"] > 0.8


def test_correlated_groups_keep_the_column_most_related_to_a_number_target():
    """Spearman + hierarchical clustering (the dissertation method): one column per group."""
    rng = np.random.default_rng(2)
    n = 300
    base = rng.normal(size=n)
    df = pd.DataFrame({"a": base, "a_noisy": base + rng.normal(scale=0.05, size=n),   # moves with a
                       "b": rng.normal(size=n)})
    df["y"] = 3 * df["a_noisy"] + df["b"] + rng.normal(scale=0.1, size=n)
    res = skill.run(df, target="y")
    kept = {d["feature"] for d in res.tables["drivers"]}
    assert len(kept & {"a", "a_noisy"}) == 1 and "b" in kept                     # one of the pair, b untouched
    assert "Spearman" in res.artifacts["markdown_analysis_report"]
    assert res.data["test"]["rmse"] > 0 and "residuals_normal_p" in res.data["all_scores"][1]


def test_skewed_categories_are_weighted_and_folds_report_their_spread():
    rng = np.random.default_rng(3)
    n = 400
    x = rng.normal(size=n)
    df = pd.DataFrame({"x": x, "z": rng.normal(size=n), "rare": np.where(x > 1.2, "yes", "no")})   # ~12% "yes"
    res = skill.run(df, target="rare")
    report = res.artifacts["markdown_analysis_report"]
    assert "skewed" in report and "weighted" in report
    assert "across folds" in report and "cv_fold_std" in res.data["all_scores"][1]


def test_thorough_mode_adds_forest_and_tuned_boosting_and_every_run_ends_with_predictions():
    quick = skill.run(_table(), target="churned")
    assert {r["model"] for r in quick.data["all_scores"]} == {
        "Baseline (most common)", "Logistic regression", "Gradient boosting"}           # cheap by default
    assert len(quick.tables["predictions"]) == 10 and {"actual", "predicted"} <= set(quick.tables["predictions"][0])
    assert "Example predictions" in quick.artifacts["markdown_analysis_report"]
    full = skill.run(_table(), target="churned", thorough=True)
    assert {"Random forest", "Gradient boosting (tuned)"} <= {r["model"] for r in full.data["all_scores"]}
