"""
data-reporting — a shareable report about a dataset (Data Visualization).

Python implementation of SKILL.md in this folder (adapted from Daniel
Rosehill's skill, MIT). The original compiles a PDF with Typst; this version
writes the same sections as Markdown AND a print-ready HTML page (open it in a
browser and choose "Save as PDF"), so no extra software is needed.

Sections — built by running other skills and collecting their findings:
  1. Overview          (explore-data)
  2. Data quality      (data-cleaning on the given table, anomaly-analysis)
  3. Trends            (trend-analysis, if there is a date column)
  4. Relationships     (correlation-analysis, if 2+ number columns)
  5. Privacy           (pii-flag)
"""

from __future__ import annotations

import html

import pandas as pd

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, date_columns, numeric_columns

SKILL = SkillSpec(
    id="data-reporting",
    title="Shareable report",
    description="A Markdown + print-ready HTML report combining profiling, quality, trends, correlations, and privacy.",
    params={"title": Param("str", "Data report", "Report title")},
)


def run(df: pd.DataFrame, title: str = "Data report") -> SkillResult:
    from data_skills.registry import run_skill   # imported here to avoid a circular import

    sections = [("Overview", "explore-data", {}), ("Data quality", "data-cleaning", {}),
                ("Unusual values", "anomaly-analysis", {})]
    if date_columns(df) and numeric_columns(df):
        sections.append(("Trends", "trend-analysis", {}))
    if len(numeric_columns(df)) >= 2:
        sections.append(("Relationships", "correlation-analysis", {}))
    sections.append(("Privacy", "pii-flag", {}))

    md = [f"# {title}", ""]
    body = [f"<h1>{html.escape(title)}</h1>"]
    for heading, skill, params in sections:
        try:
            r = run_skill(skill, df, params)
        except SkillInputError as exc:
            r = None
            note = str(exc)
        md += [f"## {heading}", ""]
        body.append(f"<h2>{html.escape(heading)}</h2>")
        if r is None:
            md += [f"_Skipped: {note}_", ""]
            body.append(f"<p><em>Skipped: {html.escape(note)}</em></p>")
            continue
        md += [r.summary, ""] + [f"- {f}" for f in r.findings] + [""]
        body.append(f"<p><strong>{html.escape(r.summary)}</strong></p><ul>"
                    + "".join(f"<li>{html.escape(f)}</li>" for f in r.findings) + "</ul>")

    page = ("<!doctype html><html lang='en-US'><head><meta charset='utf-8'><title>" + html.escape(title) + "</title>"
            "<style>body{font:15px/1.6 system-ui,sans-serif;max-width:760px;margin:40px auto;padding:0 16px;color:#111827}"
            "h1{font-family:ui-monospace,'Fira Code',monospace}h2{border-bottom:1px solid #d9dde3;padding-bottom:4px;margin-top:32px}"
            "@media print{body{margin:0}}</style></head><body>" + "".join(body) + "</body></html>")
    return SkillResult(skill=SKILL.id, summary=f"Report '{title}' with {len(sections)} sections.",
                       findings=[s[0] for s in sections],
                       artifacts={"markdown_report": "\n".join(md), "html_report": page})
