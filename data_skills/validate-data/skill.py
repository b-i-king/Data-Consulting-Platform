"""
validate-data — QA an analysis before it goes to a client (Data Exploration & Analysis).

Python implementation of README.md in this folder (Anthropic data plugin,
Apache-2.0). Give it the table plus the numbers you plan to report; it
recomputes them independently and runs the classic pitfall checks:

  1. Reconcile    — recompute each reported number (metric/agg/group) and compare
  2. Parts = whole — group values must add up to the overall total
  3. Silent drops — rows with a missing metric or group are excluded from a
                    groupby without warning; how many?
  4. Duplicates   — exact duplicate rows inflate sums
  5. Small groups — groups under 30 rows give unstable averages
  6. Simpson check — if a second category is given, does the direction of
                    the comparison flip within its levels?

Verdict: PASS, PASS WITH WARNINGS, or FAIL.
"""

from __future__ import annotations

import pandas as pd

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, fmt_num, numeric_columns, require_column

SKILL = SkillSpec(
    id="validate-data",
    title="Analysis quality check",
    description="Recompute reported numbers and check for silent drops, duplicates, small groups, and Simpson's paradox.",
    params={
        "metric": Param("column", None, "Number column the analysis uses"),
        "group_by": Param("column", None, "Category the analysis breaks down by"),
        "agg": Param("choice", "sum", "Aggregation used", ("sum", "mean", "count")),
        "reported": Param("json", None, 'Numbers you plan to report, e.g. {"total": 12345, "Midtown": 7000}'),
        "control_column": Param("column", None, "Second category for the Simpson's-paradox check"),
        "tolerance_pct": Param("float", 0.5, "Allowed difference for reconciliation (%)"),
    },
)


def run(df, metric=None, group_by=None, agg="sum", reported=None, control_column=None, tolerance_pct=0.5) -> SkillResult:
    """
    QA an analysis: recompute reported numbers and run the classic pitfall checks.

    Checks (status "pass", "fail", or "warn"):
    - Reconcile each reported number ("total" or a group name) against a fresh recompute;
      fails beyond tolerance_pct or if the group doesn't exist
    - Parts add up to the whole (sum/count only) — a gap means rows with no group value
    - Rows silently dropped: missing metric (or group) values a groupby would skip
    - Exact duplicate rows that would inflate totals
    - Groups with fewer than 30 rows
    - Simpson's paradox (two groups + control_column): does the overall leader by mean lose
      within any level of the control column?
    Verdict: FAIL if any check fails, PASS WITH WARNINGS if any warns, else PASS.

    Args:
        df: pd.DataFrame, the table the analysis used.
        metric: str | None, number column (default: first number column).
        group_by: str | None, category the analysis breaks down by.
        agg: str, "sum" (default), "mean", or "count".
        reported: dict | None, numbers you plan to report, e.g. {"total": 12345, "Midtown": 7000}.
        control_column: str | None, second category for the Simpson's-paradox check.
        tolerance_pct: float, allowed reconciliation difference in percent (default 0.5).

    Returns:
        SkillResult: summary with the verdict and passed count; findings for each non-passing
        check; table "checks"; data {"verdict": "PASS" | "PASS WITH WARNINGS" | "FAIL"}.

    Raises:
        SkillInputError: if no number column is found or control_column is not in the table.

    Example:
        >>> res = run(df, metric="amount", group_by="store", reported={"total": 12345})
        >>> res.data["verdict"]   # e.g. "PASS WITH WARNINGS"
    """
    metric = require_column(df, metric, "number", numeric_columns(df))
    checks = []

    def add(name, ok, detail, severity="fail"):
        """
        Record one check result in the enclosing run()'s `checks` list.

        Args:
            name: str, short check name, e.g. "duplicate rows".
            ok: bool, True if the check passed.
            detail: str, human-readable explanation shown in the report.
            severity: str, status to record when ok is False: "fail" (default) or "warn".

        Returns:
            None. Appends {"check", "status", "detail"} to `checks`.
        """
        checks.append({"check": name, "status": "pass" if ok else severity, "detail": detail})

    total = df[metric].agg(agg)
    by = df.groupby(group_by)[metric].agg(agg) if group_by else None

    for key, value in (reported or {}).items():
        actual = total if key == "total" else (by.get(key) if by is not None else None)
        if actual is None:
            add(f"reconcile '{key}'", False, f"'{key}' is not a group in '{group_by}'.")
            continue
        diff = abs(actual - float(value)) / abs(actual) * 100 if actual else 0
        add(f"reconcile '{key}'", diff <= tolerance_pct,
            f"reported {fmt_num(float(value))}, recomputed {fmt_num(actual)} ({diff:.2f}% apart)")

    if by is not None and agg in ("sum", "count"):
        parts = by.sum()
        add("parts add up to the total", abs(parts - total) <= abs(total) * 1e-9 + 1e-9,
            f"groups sum to {fmt_num(parts)}, overall {fmt_num(total)}"
            + (" — the gap is rows with no group value." if abs(parts - total) > 1e-9 else ""), "warn")

    dropped = int(df[metric].isna().sum() + (df[group_by].isna() & df[metric].notna()).sum() if group_by else df[metric].isna().sum())
    add("rows silently dropped", dropped == 0, f"{dropped:,} rows have a missing {metric}{' or ' + group_by if group_by else ''}", "warn")

    dups = int(df.duplicated().sum())
    add("duplicate rows", dups == 0, f"{dups:,} exact duplicate rows would inflate totals", "warn")

    if group_by:
        sizes = df[group_by].value_counts()
        small = sizes[sizes < 30]
        add("groups large enough (n ≥ 30)", small.empty,
            "small groups: " + ", ".join(f"{k} ({v})" for k, v in small.items()) if len(small) else "all groups have 30+ rows", "warn")

    if group_by and control_column:
        if control_column not in df.columns:
            raise SkillInputError(f"Column '{control_column}' was not found.")
        overall = df.groupby(group_by)[metric].mean()
        if len(overall) == 2:
            winner = overall.idxmax()
            flips = [str(lvl) for lvl, sub in df.groupby(control_column)
                     if sub[group_by].nunique() == 2 and sub.groupby(group_by)[metric].mean().idxmax() != winner]
            add("no Simpson's paradox", not flips,
                f"{winner} leads overall but not within: {', '.join(flips)}" if flips else
                f"{winner} leads overall and within every {control_column}", "warn")

    fails = [c for c in checks if c["status"] == "fail"]
    warns = [c for c in checks if c["status"] == "warn"]
    verdict = "FAIL" if fails else "PASS WITH WARNINGS" if warns else "PASS"
    return SkillResult(skill=SKILL.id, summary=f"{verdict}: {len(checks) - len(fails) - len(warns)} of {len(checks)} checks passed.",
                       findings=[f"{c['status'].upper()} — {c['check']}: {c['detail']}" for c in checks if c["status"] != "pass"],
                       tables={"checks": checks}, data={"verdict": verdict})
