"""
data-context-extractor — draft a client-specific skill (AI).

Python implementation of SKILL.md in this folder (Anthropic data plugin,
Apache-2.0), BOOTSTRAP mode. It discovers what it can from the client's
tables and writes a draft SKILL.md that teaches Claude (and the future AI
service) the client's data:

  * tables, their grain ("one row = one order line"), keys and joins
  * candidate metrics with a default definition (SUM of amount = revenue)
  * dimensions with their values (stores: Midtown, Decatur)
  * date coverage
  * a glossary section with TODOs for the terms only the client can define

The consultant then fills the TODOs with the client (ITERATION mode is a
conversation, so it stays in SKILL.md).
"""

from __future__ import annotations

import re

import pandas as pd

from data_skills._core import (
    Param, SkillResult, SkillSpec, as_datetime, categorical_columns, date_columns, numeric_columns,
)

SKILL = SkillSpec(
    id="data-context-extractor",
    title="Learns your business vocabulary",
    input="tables",
    description="Draft a client-specific SKILL.md: tables, grain, keys, metrics, dimensions, and glossary TODOs.",
    params={"client_name": Param("str", "client", "Client or company name")},
)


def run(tables: dict[str, pd.DataFrame], client_name="client") -> SkillResult:
    slug = re.sub(r"[^a-z0-9]+", "-", client_name.lower()).strip("-") or "client"
    md = ["---", f"name: {slug}-data", f"description: How to analyze {client_name}'s data — tables, metrics, "
          "definitions, and gotchas. Use for any question about their sales, customers, or operations.", "---", "",
          f"# {client_name} data guide", ""]
    metrics, todos = [], []
    for name, df in tables.items():
        key = next((c for c in df.columns if df[c].notna().all() and df[c].is_unique), None)
        dates, nums, cats = date_columns(df), numeric_columns(df), categorical_columns(df, max_unique=20)
        md += [f"## Table: `{name}`", "", f"- Rows: {len(df):,}"]
        md.append(f"- Grain: one row = one {'record per ' + str(key) if key else 'TODO: what is one row? (order line? day? customer?)'}")
        if not key:
            todos.append(f"What is one row of {name}?")
        if dates:
            d = as_datetime(df[dates[0]]).dropna()
            md.append(f"- Dates: `{dates[0]}` from {d.min().date()} to {d.max().date()}")
        for n in nums:
            label = "revenue" if re.search(r"amount|revenue|total|sales", n, re.I) else n.replace("_", " ")
            metrics.append(f"- **{label}** = SUM(`{n}`) in `{name}` — TODO: confirm (before/after tax? refunds?)")
        for c in cats:
            vals = ", ".join(map(str, df[c].dropna().unique()[:8]))
            md.append(f"- Dimension `{c}`: {vals}")
        md.append("")
    md += ["## Metrics", ""] + (metrics or ["- TODO: which numbers matter most?"]) + [""]
    md += ["## Glossary (fill in with the client)", "", "- TODO: what counts as an 'active customer'?",
           "- TODO: fiscal year start (January?)", "- TODO: anything excluded from reports (tests, staff orders)?"]
    md += [f"- TODO: {t}" for t in todos] + ["", "## Gotchas", "", "- TODO: known data quirks (e.g. walk-in sales have no customer_id)", ""]
    return SkillResult(skill=SKILL.id, summary=f"Draft skill '{slug}-data' with {len(tables)} table(s) and {len(metrics)} candidate metric(s).",
                       findings=["Review the TODOs with the client — they define what the numbers mean.",
                                 f"Save as data_skills/{slug}-data/SKILL.md (and add it to catalog.json) once confirmed."],
                       artifacts={"markdown_skill": "\n".join(md)})
