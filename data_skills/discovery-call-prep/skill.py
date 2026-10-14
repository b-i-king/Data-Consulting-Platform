"""
discovery-call-prep — one-page brief before a discovery call (Data Strategy & Discovery).

Python implementation of README.md in this folder. Given the client's tables
(and, optionally, their intake answers), it writes a Markdown brief:

  1. Data-source inventory — every table: rows, columns, date span, entity guess
  2. Readiness snapshot     — missing values, duplicates, sample-size verdicts
  3. Draft objectives       — from the services they selected
  4. The three questions    — the ones that most change the scope
  5. Roadmap draft          — phases matched to the services and the data

Output: artifacts["markdown_brief"] — paste it into your notes before the call.
"""

from __future__ import annotations

import pandas as pd

from api.services.categorizer import classify_entity
from api.services.cleaning import clean_dataframe
from api.services.sample_size import sample_size_snapshot
from data_skills._core import Param, SkillResult, SkillSpec, as_datetime, date_columns, fmt_num

OBJECTIVES = {
    "data_strategy_discovery": "Agree on the data sources that matter and a 90-day roadmap.",
    "data_exploration_analysis": "Answer the top 3–5 business questions with clear numbers.",
    "data_visualization": "A dashboard the team checks weekly without building a spreadsheet.",
    "data_science_ml": "A forecast or prediction that changes a decision (staffing, ordering, outreach).",
    "data_engineering": "One reliable, documented home for the data, refreshed automatically.",
    "ai": "Ask plain-English questions about the data and trust the answers.",
}
PHASES = [
    ("Foundation", "1–2", "Sources connected, cleaned, documented", ("data_engineering", None)),
    ("Answers", "2–4", "Key questions answered, trends identified", ("data_exploration_analysis",)),
    ("Visibility", "4–6", "Dashboard the team checks weekly", ("data_visualization",)),
    ("Foresight", "6+", "Forecast / prediction where the data supports it", ("data_science_ml",)),
]

SKILL = SkillSpec(
    id="discovery-call-prep",
    title="Discovery call brief",
    input="tables",
    wants_raw=True,   # it runs the cleaning itself, to report what the client's raw data looked like
    description="Turn the client's files and intake answers into a one-page call brief and roadmap draft.",
    params={
        "client_name": Param("str", "Client", "Business name for the brief"),
        "services": Param("columns", None, "Selected service ids (e.g. data_visualization,data_science_ml)"),
        "data_location": Param("str", None, "Where they said their data lives (e.g. quickbooks)"),
        "industry": Param("str", None, "NAICS sector or subsector, if known"),
    },
)


def run(tables: dict[str, pd.DataFrame], client_name="Client", services=None, data_location=None, industry=None) -> SkillResult:
    services = services or []
    inventory, readiness, findings = [], [], []
    for name, raw in tables.items():
        df, rep = clean_dataframe(raw, name)
        entity, _ = classify_entity(list(df.columns))
        dcols = date_columns(df)
        span = ""
        if dcols:
            d = as_datetime(df[dcols[0]]).dropna()
            if len(d):
                span = f"{d.min().date()} → {d.max().date()}"
        inventory.append({"source": name, "entity_guess": entity, "rows": rep.rows_after,
                          "columns": rep.columns, "date_span": span})
        snap = sample_size_snapshot(df)
        green = sum(c["verdict"] == "green" for c in snap["checks"])
        readiness.append({"source": name, "missing_pct": rep.total_missing_pct,
                          "duplicates_removed": rep.duplicate_rows_removed,
                          "adequacy": f"{green}/{len(snap['checks'])} standard checks pass"})
        if rep.total_missing_pct > 10:
            findings.append(f"{name}: {rep.total_missing_pct:.0f}% missing — ask how the data is entered.")

    md = [f"# Discovery brief — {client_name}", ""]
    if industry or data_location:
        md += [f"**Industry:** {industry or 'not given'} · **Data lives in:** {data_location or 'files'}", ""]
    md += ["## 1. Data-source inventory", "", "| Source | Looks like | Rows | Columns | Dates |", "|---|---|---|---|---|"]
    md += [f"| {i['source']} | {i['entity_guess']} | {fmt_num(i['rows'])} | {i['columns']} | {i['date_span'] or '—'} |" for i in inventory]
    md += ["", "_Ask about anything mentioned but not uploaded (payroll? accounting? marketing?)._", "",
           "## 2. Readiness", "", "| Source | Missing | Duplicates removed | Sample size |", "|---|---|---|---|"]
    md += [f"| {r['source']} | {r['missing_pct']:.1f}% | {r['duplicates_removed']} | {r['adequacy']} |" for r in readiness]
    md += ["", "## 3. Draft objectives (confirm on the call)", ""]
    md += [f"- {OBJECTIVES[s]}" for s in services if s in OBJECTIVES] or ["- _No services selected yet — ask what decision the data should support._"]
    md += ["", "## 4. The three questions", "",
           "1. What decision would you make differently if you had this answer today?",
           "2. Where does the data that isn't in these files live, and who can export it?",
           "3. Who will look at the results, and how often?", "",
           "## 5. Roadmap draft", "", "| Phase | Weeks | Outcome |", "|---|---|---|"]
    for phase, weeks, outcome, needs in PHASES:
        if any(n in services for n in needs if n) or (phase == "Foundation"):
            md.append(f"| {phase} | {weeks} | {outcome} |")
    md += ["", "_Small businesses often stop at phase 2 or 3 — that's a success, not a shortfall._"]

    return SkillResult(
        skill=SKILL.id,
        summary=f"Brief ready for {client_name}: {len(inventory)} source(s), {sum(i['rows'] for i in inventory):,} rows.",
        findings=findings or ["Data looks ready for a productive first call."],
        tables={"inventory": inventory, "readiness": readiness},
        artifacts={"markdown_brief": "\n".join(md) + "\n"},
    )
