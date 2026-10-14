"""
multivariate-analysis — how three or more variables behave together (Data Science & ML).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). With numpy/scikit-learn:

  1. Partial correlations — the correlation of each pair after removing the
     influence of all the other columns (from the inverse correlation matrix).
     A pair that is correlated only through a third factor drops toward 0.
  2. Multicollinearity — VIF (variance inflation factor) per column;
     VIF > 5 means the column is largely predictable from the others, so a
     model can't separate their effects.
  3. Dimensionality — PCA: how many components explain 80% of the variation,
     and which columns load on the first component.
"""

from __future__ import annotations

import itertools

import numpy as np
import pandas as pd
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import StandardScaler

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, bar_chart, numeric_columns

SKILL = SkillSpec(
    id="multivariate-analysis",
    title="Multi-factor (multivariate) analysis",
    description="Partial correlations, VIF multicollinearity, and PCA across 3+ number columns.",
    params={"columns": Param("columns", None, "Number columns (default: all, 3+ needed)")},
)


def run(df, columns=None) -> SkillResult:
    cols = columns or numeric_columns(df)
    data = df[cols].dropna()
    data = data.loc[:, data.std() > 0]
    cols = list(data.columns)
    if len(cols) < 3 or len(data) < 20:
        raise SkillInputError("Multivariate analysis needs 3+ varying number columns and 20+ complete rows.")

    corr = data.corr().to_numpy()
    prec = np.linalg.pinv(corr)
    partial = []
    for i, j in itertools.combinations(range(len(cols)), 2):
        pc = -prec[i, j] / np.sqrt(prec[i, i] * prec[j, j])
        partial.append({"column_a": cols[i], "column_b": cols[j], "correlation": round(float(corr[i, j]), 4),
                        "partial_correlation": round(float(pc), 4)})
    partial.sort(key=lambda r: abs(r["correlation"] - r["partial_correlation"]), reverse=True)

    vif = []
    for c in cols:
        others = [o for o in cols if o != c]
        r2 = LinearRegression().fit(data[others], data[c]).score(data[others], data[c])
        vif.append({"column": c, "vif": round(float(1 / (1 - r2)) if r2 < 0.9999 else float("inf"), 2)})

    pca = PCA().fit(StandardScaler().fit_transform(data))
    cum = np.cumsum(pca.explained_variance_ratio_)
    k80 = int(np.searchsorted(cum, 0.8) + 1)
    loadings = pd.Series(pca.components_[0], index=cols).abs().sort_values(ascending=False)

    findings = []
    top = partial[0]
    if abs(top["correlation"] - top["partial_correlation"]) > 0.2:
        findings.append(f"{top['column_a']} ↔ {top['column_b']}: r = {top['correlation']:+.2f} overall but "
                        f"{top['partial_correlation']:+.2f} after accounting for the other columns — "
                        "much of the relationship runs through a third factor.")
    high = [v for v in vif if v["vif"] > 5]
    findings.append("Multicollinearity: " + (", ".join(f"{v['column']} (VIF {v['vif']})" for v in high)
                                             + " — don't interpret these columns' effects separately in a model."
                                             if high else "none (all VIF ≤ 5)."))
    findings.append(f"{k80} of {len(cols)} components explain 80% of the variation; the first is driven by "
                    + ", ".join(loadings.head(3).index) + ".")
    return SkillResult(skill=SKILL.id, summary=f"Analyzed {len(cols)} columns over {len(data):,} complete rows.",
                       findings=findings,
                       tables={"partial_correlations": partial, "vif": vif,
                               "pca": [{"component": i + 1, "explained_pct": round(float(v) * 100, 2),
                                        "cumulative_pct": round(float(c) * 100, 2)}
                                       for i, (v, c) in enumerate(zip(pca.explained_variance_ratio_, cum))]},
                       charts=[bar_chart("Variation explained by each component (%)",
                                         [f"PC{i + 1}" for i in range(len(cum))],
                                         [round(float(v) * 100, 2) for v in pca.explained_variance_ratio_], "%")])
