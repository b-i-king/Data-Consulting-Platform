"""
correlation-analysis — what moves together (Data Exploration & Analysis).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT).

  * Pearson  — straight-line relationships (sensitive to outliers)
  * Spearman — any steadily rising/falling relationship (rank-based, robust)
  * Kendall  — like Spearman, better for small samples with ties

Reports the full matrix and the strongest positive and negative pairs, with
a p-value for each top pair (scipy). Strength words: |r| ≥ 0.7 strong,
≥ 0.4 moderate, ≥ 0.2 weak, else negligible. Correlation is not causation.
"""

from __future__ import annotations

import itertools

import pandas as pd
from scipy import stats

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, numeric_columns

SKILL = SkillSpec(
    id="correlation-analysis",
    title="What moves together (correlations)",
    description="Pearson/Spearman/Kendall correlation matrix and the strongest pairs, with p-values.",
    params={
        "method": Param("choice", "spearman", "Correlation method", ("pearson", "spearman", "kendall")),
        "columns": Param("columns", None, "Number columns to include (default: all)"),
    },
)

_TEST = {"pearson": stats.pearsonr, "spearman": stats.spearmanr, "kendall": stats.kendalltau}


def strength(r: float) -> str:
    """
    Describe a correlation coefficient in words.

    Uses the absolute value: 0.7 or more strong, 0.4 or more moderate, 0.2 or more
    weak, else negligible.

    Args:
        r: correlation coefficient between -1 and 1.

    Returns:
        str: "strong", "moderate", "weak" or "negligible".

    Example:
        >>> strength(-0.75), strength(0.3)
        ('strong', 'weak')
    """
    a = abs(r)
    return "strong" if a >= 0.7 else "moderate" if a >= 0.4 else "weak" if a >= 0.2 else "negligible"


def run(df, method="spearman", columns=None) -> SkillResult:
    """
    Measure which number columns move together and report the strongest pairs.

    Computes the full correlation matrix, then tests every pair of columns on the
    rows where both have values (pairs with fewer than 5 rows or a constant column
    are skipped) and sorts pairs by |r|. A pair is "significant" when p < 0.05.

    Args:
        df: the table.
        method: "spearman" (default, rank-based and robust), "pearson" (straight-line)
            or "kendall" (small samples with ties).
        columns: number columns to include (default: all number columns, excluding IDs).

    Returns:
        SkillResult: summary naming the strongest pair; findings for the top 6 pairs
        plus a "correlation is not causation" reminder; tables "pairs" (column_a,
        column_b, r, p_value, n, strength) and "matrix".

    Raises:
        SkillInputError: fewer than two number columns.

    Example:
        >>> run(pd.read_csv("samples/sales.csv")).summary
        # 'Strongest relationship: quantity and amount (r = +0.77, strong).'
    """
    cols = columns or numeric_columns(df)
    if len(cols) < 2:
        raise SkillInputError("Correlation needs at least two number columns.")
    matrix = df[cols].corr(method=method)
    pairs = []
    for a, b in itertools.combinations(cols, 2):
        sub = df[[a, b]].dropna()
        if len(sub) < 5 or sub[a].nunique() < 2 or sub[b].nunique() < 2:
            continue
        r, p = _TEST[method](sub[a], sub[b])
        pairs.append({"column_a": a, "column_b": b, "r": round(float(r), 4), "p_value": float(p),
                      "n": len(sub), "strength": strength(r)})
    pairs.sort(key=lambda x: abs(x["r"]), reverse=True)
    findings = [f"{p['column_a']} ↔ {p['column_b']}: r = {p['r']:+.2f} ({p['strength']}, "
                f"{'significant' if p['p_value'] < 0.05 else 'not significant'}, n = {p['n']:,})" for p in pairs[:6]]
    findings.append("Correlation shows association, not cause — check for a third factor before acting.")
    top = pairs[0] if pairs else None
    return SkillResult(
        skill=SKILL.id,
        summary=(f"Strongest relationship: {top['column_a']} and {top['column_b']} (r = {top['r']:+.2f}, {top['strength']})."
                 if top else "No pairs had enough data to compare."),
        findings=findings,
        tables={"pairs": pairs, "matrix": [{"column": i, **{c: round(float(v), 4) for c, v in row.items()}}
                                           for i, row in matrix.iterrows()]},
    )
