"""
data-cleaning — the platform's cleaning standard (Data Cleaning).

Python implementation of README.md in this folder. The actual pipeline lives in
api/services/cleaning.py (the website uses it on every upload); this skill
wraps it so it can also run from the command line or the skills API.

Steps: your transform steps → clean column names (lowercase snake_case) → tidy text → drop empty rows → drop exact duplicates → restore whole
numbers → missing-value report with advice (20% / 90% rules, mean vs. median
by skewness; IDs: remove rows with no ID) → wrong-data and wrong-format checks. Nothing is filled or removed unless
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
        "column_names": Param("json", None, 'Your own column names, by cleaned name, '
                                            'e.g. {"unnamed_column_4": "store_code", "amount_2": "refund_amount"}'),
        "transforms": Param("json", None, 'Transform steps in order (like Power Query), e.g. '
                                          '[{"op": "change_type", "column": "order_date", "to": "date"}]'),
    },
)


def run(df: pd.DataFrame, missing_strategy: str = "keep", column_actions=None, column_names=None,
        transforms=None) -> SkillResult:
    """
    Run the platform's cleaning pipeline (api/services/cleaning.py) and report the results.

    Order: transform steps -> clean column names -> tidy text -> drop empty rows -> drop
    exact duplicates -> restore whole numbers -> missing-value report with advice ->
    wrong-data and wrong-format checks. Nothing is filled or removed unless
    `column_actions` (or missing_strategy="drop_any") asks for it.

    Args:
        df: the raw table (this skill sets wants_raw=True, so it gets the upload as is).
        missing_strategy: "keep" (default) keeps rows with gaps; "drop_any" drops them.
        column_actions: per cleaned column name: keep, drop, mean, median, mode, auto,
            or drop_rows (IDs), e.g. {"age_group": "mode", "order_id": "drop_rows"}.
        column_names: your own names by cleaned name, e.g. {"unnamed_column_4": "store_code"}.
        transforms: transform steps in order (like Power Query), e.g.
            [{"op": "change_type", "column": "order_date", "to": "date"}].

    Returns:
        SkillResult: summary (rows in/out, fields, % missing); findings (pipeline notes
        plus the 3 most-missing columns); tables "missing_values" (per-column report
        with suggested fill and advice) and "cleaned_preview" (first 200 rows);
        data["report"] with the full cleaning report.

    Raises:
        SkillInputError: the pipeline rejected an option (e.g. an unknown fill method,
            or two columns given the same name).

    Example:
        >>> run(raw_df, column_actions={"age_group": "mode"}).summary
        # 'N rows in, M rows out; K fields; x.x% of cells are missing.'
    """
    try:
        cleaned, report = clean_dataframe(df, missing_strategy=missing_strategy, column_actions=column_actions,
                                          column_names=column_names, transforms=transforms)
    except ValueError as exc:   # e.g. an unknown fill method, or two columns given one name
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
