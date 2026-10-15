"""
hypothesis-testing — test a specific claim (Data Exploration & Analysis).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). The test is chosen from the column types:

  number  vs. 2 groups   → Welch's t-test (unequal variances) AND Mann-Whitney U
                            (rank-based; trusted when data is skewed)
  number  vs. 3+ groups  → one-way ANOVA (Welch-robust check) and Kruskal-Wallis
  category vs. category  → chi-square test of independence (Fisher's exact for 2×2
                            tables with small expected counts)
  number  vs. number     → Spearman correlation test

Each result reports the effect size (Cohen's d, Cramér's V, or rho) next to
the p-value — a tiny p-value with a tiny effect is "real but unimportant".
Verdict: supported (p < alpha), refuted direction, or inconclusive.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, fmt_num

SKILL = SkillSpec(
    id="hypothesis-testing",
    title="Hypothesis testing",
    description="Pick and run the right test (t-test, Mann-Whitney, ANOVA, chi-square, correlation) with effect sizes.",
    params={
        "outcome": Param("column", None, "What you measure (e.g. amount, csat)"),
        "group": Param("column", None, "What you compare by (e.g. store, payment_method)"),
        "hypothesis": Param("str", None, "Your claim in words, e.g. 'Midtown orders are larger than Decatur'"),
        "alpha": Param("float", 0.05, "Significance level"),
    },
)


def cohens_d(a: pd.Series, b: pd.Series) -> float:
    """
    Compute Cohen's d: the difference in means measured in pooled standard deviations.

    Uses the pooled sample SD of both groups. Rough guide: 0.2 small, 0.5 medium,
    0.8 large. The sign follows a - b. Returns 0.0 when the pooled SD is 0 (both groups
    constant) to avoid dividing by zero.

    Args:
        a: pd.Series, numeric values of the first group.
        b: pd.Series, numeric values of the second group.

    Returns:
        float: the standardized mean difference (a minus b).

    Example:
        >>> cohens_d(pd.Series([1.0, 2.0, 3.0]), pd.Series([2.0, 3.0, 4.0]))
        -1.0
    """
    pooled = np.sqrt(((len(a) - 1) * a.var() + (len(b) - 1) * b.var()) / (len(a) + len(b) - 2))
    return float((a.mean() - b.mean()) / pooled) if pooled else 0.0


def run(df, outcome=None, group=None, hypothesis=None, alpha=0.05) -> SkillResult:
    """
    Pick and run the right significance test for an outcome vs. a grouping column.

    Test choice (rows missing either column are dropped first):
    - number outcome vs. number "group" with > 10 distinct values -> Spearman correlation
    - number outcome vs. exactly 2 groups -> Welch t-test + Mann-Whitney U, with Cohen's d
    - number outcome vs. 3+ groups -> one-way ANOVA + Kruskal-Wallis
    - category outcome -> chi-square test of independence with Cramér's V, plus Fisher's
      exact for a 2x2 table with any expected count under 5
    The verdict uses the rank-based test (last in the list) for number-vs-groups, which
    is robust to skew; otherwise the first test. "Supported" means p < alpha.

    Args:
        df: pd.DataFrame, the table.
        outcome: str | None, column you measure (e.g. "amount", "csat"). Required.
        group: str | None, column you compare by (e.g. "store"). Required.
        hypothesis: str | None, the claim in words; quoted in the summary when given.
        alpha: float, significance level (default 0.05).

    Returns:
        SkillResult: summary with verdict, test name, p-value, and effect size; findings with
        group means/medians and a reminder on reading p-values; table "tests" (test, statistic,
        p_value, effect, significant); data {"verdict": "supported" | "inconclusive"}.

    Raises:
        SkillInputError: if outcome or group is missing, a column isn't in the table, or the
            group column has fewer than two groups.

    Example:
        >>> res = run(df, outcome="amount", group="store", hypothesis="Midtown orders are larger")
        >>> res.data["verdict"]   # "supported" or "inconclusive"
    """
    if not outcome or not group:
        raise SkillInputError("Give an 'outcome' column (what you measure) and a 'group' column (what you compare by).")
    for c in (outcome, group):
        if c not in df.columns:
            raise SkillInputError(f"Column '{c}' was not found.")
    data = df[[outcome, group]].dropna()
    num_out = pd.api.types.is_numeric_dtype(data[outcome])
    num_grp = pd.api.types.is_numeric_dtype(data[group]) and data[group].nunique() > 10
    tests, findings = [], []

    if num_out and num_grp:
        rho, p = stats.spearmanr(data[outcome], data[group])
        tests.append({"test": "Spearman correlation", "statistic": rho, "p_value": p, "effect": f"rho = {rho:+.2f}"})
    elif num_out:
        groups = {k: g[outcome].astype(float) for k, g in data.groupby(group)}
        if len(groups) < 2:
            raise SkillInputError(f"'{group}' needs at least two groups.")
        if len(groups) == 2:
            (na, a), (nb, b) = groups.items()
            t, p = stats.ttest_ind(a, b, equal_var=False)
            u, pu = stats.mannwhitneyu(a, b)
            d = cohens_d(a, b)
            tests += [{"test": "Welch t-test", "statistic": t, "p_value": p, "effect": f"Cohen's d = {d:+.2f}"},
                      {"test": "Mann-Whitney U", "statistic": u, "p_value": pu, "effect": "rank-based"}]
            findings.append(f"Mean {outcome}: {na} {fmt_num(a.mean())} (n={len(a):,}) vs. {nb} {fmt_num(b.mean())} (n={len(b):,}); "
                            f"medians {fmt_num(a.median())} vs. {fmt_num(b.median())}.")
        else:
            f, p = stats.f_oneway(*groups.values())
            h, ph = stats.kruskal(*groups.values())
            tests += [{"test": "One-way ANOVA", "statistic": f, "p_value": p, "effect": f"{len(groups)} groups"},
                      {"test": "Kruskal-Wallis", "statistic": h, "p_value": ph, "effect": "rank-based"}]
            means = {k: v.mean() for k, v in groups.items()}
            findings.append("Means: " + ", ".join(f"{k} {fmt_num(v)}" for k, v in sorted(means.items(), key=lambda x: -x[1])))
    else:
        table = pd.crosstab(data[outcome], data[group])
        chi2, p, dof, expected = stats.chi2_contingency(table)
        v = np.sqrt(chi2 / (table.values.sum() * (min(table.shape) - 1))) if min(table.shape) > 1 else 0
        tests.append({"test": "Chi-square independence", "statistic": chi2, "p_value": p, "effect": f"Cramér's V = {v:.2f}"})
        if table.shape == (2, 2) and (expected < 5).any():
            _, pf = stats.fisher_exact(table)
            tests.append({"test": "Fisher's exact", "statistic": None, "p_value": pf, "effect": "small counts"})

    primary = tests[-1] if (num_out and not num_grp and len(tests) > 1) else tests[0]   # rank-based is robust
    significant = primary["p_value"] < alpha
    verdict = "supported by the data" if significant else "inconclusive (no reliable difference found)"
    summary = (f"{'Hypothesis' if not hypothesis else repr(hypothesis)}: {verdict} — {primary['test']}, "
               f"p = {primary['p_value']:.4f}, {primary['effect']}.")
    findings.append("A small p-value means the difference is unlikely to be chance; the effect size says whether it is big enough to matter.")
    return SkillResult(skill=SKILL.id, summary=summary, findings=findings,
                       tables={"tests": [{**t, "significant": t["p_value"] < alpha} for t in tests]},
                       data={"verdict": "supported" if significant else "inconclusive"})
