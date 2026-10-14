"""
data-cleaning — the platform's cleaning standard (Data Cleaning).

Python implementation of README.md in this folder. The actual pipeline lives in
api/services/cleaning.py (the website uses it on every upload); this skill
wraps it so it can also run from the command line or the skills API.

Steps: clean column names (lowercase snake_case) → tidy text → drop empty rows → drop exact duplicates → restore whole
numbers → missing-value report with advice (20% / 90% rules, mean vs. median
by skewness; IDs: remove rows with no ID). Nothing is filled or removed unless
`column_actions` asks for it.
"""

from __future__ import annotations

import pandas as pd

from api.services.cleaning import clean_dataframe
from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, fmt_num, records

SKILL = SkillSpec(
    id="data-cleaning",
    title="Data cleaning",
    wants_raw=True,
    description="Remove duplicates and blank rows, tidy text, and report missing values by column.",
    params={
        "missing_strategy": Param("choice", "keep", "Keep rows with gaps, or drop them", ("keep", "drop_any")),
        "column_actions": Param("json", None, 'Per column (cleaned names): keep, drop, mean, median, mode, auto, '
                                              'or drop_rows (IDs), e.g. {"age_group": "mode", "order_id": "drop_rows"}'),
    },
)


def run(df: pd.DataFrame, missing_strategy: str = "keep", column_actions=None) -> SkillResult:
    try:
        cleaned, report = clean_dataframe(df, missing_strategy=missing_strategy, column_actions=column_actions)
    except ValueError as exc:   # e.g. an unknown fill method
        raise SkillInputError(str(exc)) from exc
    worst = sorted(report.column_reports, key=lambda c: c.missing_pct, reverse=True)[:3]
    findings = list(report.notes)
    findings += [f"'{c.column}' is {c.missing_pct:.1f}% missing." for c in worst if c.missing_pct > 0]
    return SkillResult(
        skill=SKILL.id,
        summary=(f"{fmt_num(report.rows_before)} rows in, {fmt_num(report.rows_after)} rows out; "
                 f"{report.columns} fields; {report.total_missing_pct:.1f}% of cells are missing."),
        findings=findings,
        tables={
            "missing_values": [{"column": c.column, "type": c.inferred_type, "missing": c.missing_count,
                                "missing_pct": c.missing_pct, "level": c.missing_level,
                                "suggested_fill": c.suggested_fill, "action": c.action,
                                "advice": c.recommendation} for c in report.column_reports],
            "cleaned_preview": records(cleaned, 200),
        },
        data={"report": report.to_dict()},
    )
