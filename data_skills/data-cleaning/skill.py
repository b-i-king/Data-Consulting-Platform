"""
data-cleaning — the platform's cleaning standard (Data Cleaning).

Python implementation of SKILL.md in this folder. The actual pipeline lives in
api/services/cleaning.py (the website uses it on every upload); this skill
wraps it so it can also run from the command line or the skills API.

Steps: tidy text → drop empty rows → drop exact duplicates → restore whole
numbers → missing-value report. Nothing is guessed or filled in.
"""

from __future__ import annotations

import pandas as pd

from api.services.cleaning import clean_dataframe
from data_skills._core import Param, SkillResult, SkillSpec, fmt_num, records

SKILL = SkillSpec(
    id="data-cleaning",
    title="Data cleaning",
    wants_raw=True,
    description="Remove duplicates and blank rows, tidy text, and report missing values by column.",
    params={
        "missing_strategy": Param("choice", "keep", "Keep rows with gaps, or drop them", ("keep", "drop_any")),
    },
)


def run(df: pd.DataFrame, missing_strategy: str = "keep") -> SkillResult:
    cleaned, report = clean_dataframe(df, missing_strategy=missing_strategy)
    worst = sorted(report.column_reports, key=lambda c: c.missing_pct, reverse=True)[:3]
    findings = list(report.notes)
    findings += [f"'{c.column}' is {c.missing_pct:.1f}% missing." for c in worst if c.missing_pct > 0]
    return SkillResult(
        skill=SKILL.id,
        summary=(f"{fmt_num(report.rows_before)} rows in, {fmt_num(report.rows_after)} rows out; "
                 f"{report.total_missing_pct:.1f}% of cells are missing."),
        findings=findings,
        tables={
            "missing_values": [{"column": c.column, "type": c.inferred_type, "missing": c.missing_count,
                                "missing_pct": c.missing_pct, "unique": c.unique_count} for c in report.column_reports],
            "cleaned_preview": records(cleaned, 200),
        },
        data={"report": report.to_dict()},
    )
