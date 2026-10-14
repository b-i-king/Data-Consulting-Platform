"""
setup-data-workspace — a local "talk to your data" database (AI).

Python implementation of README.md in this folder (adapted from Daniel
Rosehill's skill, MIT). The original loads files into DuckDB; this version
uses SQLite, which is built into Python (no extra install), and the same steps:

  1. One table per file, with snake_case table and column names
  2. A schema summary (tables, columns, types, row counts) and sample queries
  3. A CLAUDE.md block telling future Claude sessions how to query it

From the WEB API it only RETURNS the schema, sample SQL and CLAUDE.md block —
the server never writes client data to disk. From the COMMAND LINE, pass
`db_path` to actually create the database file on your computer:

    python -m data_skills run setup-data-workspace samples/*.csv --param db_path=outputs/workspace.db
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pandas as pd

from data_skills._core import Param, SkillResult, SkillSpec, date_columns, numeric_columns

SKILL = SkillSpec(
    id="setup-data-workspace",
    title='"Talk to your data" workspace',
    input="tables",
    description="Plan (or, from the command line, create) a SQLite workspace with one table per file and a CLAUDE.md guide.",
    params={"db_path": Param("str", None, "Where to write the SQLite file (command line only)", api_allowed=False)},
)


def snake(name: str) -> str:
    base = re.sub(r"\.[a-z0-9]+$", "", name.split("›")[-1].strip(), flags=re.I)
    s = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_") or "table"
    return f"t_{s}" if s[0].isdigit() else s


def run(tables: dict[str, pd.DataFrame], db_path=None) -> SkillResult:
    schema, samples = [], []
    renamed = {}
    for name, df in tables.items():
        t = snake(name)
        d = df.rename(columns={c: snake(str(c)) for c in df.columns})
        renamed[t] = d
        for c in d.columns:
            schema.append({"table": t, "column": c, "type": str(d[c].dtype), "rows": len(d)})
        nums, dates = numeric_columns(d), date_columns(d)
        samples.append(f"SELECT * FROM {t} LIMIT 10;")
        if nums and dates:
            samples.append(f"SELECT strftime('%Y-%m', {dates[0]}) AS month, SUM({nums[0]}) AS total "
                           f"FROM {t} GROUP BY 1 ORDER BY 1;")

    claude_md = ["## Data workspace", "",
                 f"The data lives in a SQLite database{' at `' + db_path + '`' if db_path else ''}. Query it with:",
                 "", "```bash", f"sqlite3 {db_path or 'workspace.db'} \"SELECT name FROM sqlite_master WHERE type='table';\"", "```", "",
                 "Tables:", ""]
    for t, d in renamed.items():
        claude_md.append(f"- `{t}` ({len(d):,} rows): " + ", ".join(f"`{c}`" for c in d.columns))
    claude_md += ["", "Always show the SQL you ran next to the answer.", ""]

    findings = [f"{len(renamed)} table(s) planned."]
    if db_path:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path) as conn:
            for t, d in renamed.items():
                d.to_sql(t, conn, if_exists="replace", index=False)
        findings.append(f"Database written to {db_path}.")
    else:
        findings.append("Nothing written to disk (pass db_path from the command line to create the database).")
    return SkillResult(skill=SKILL.id, summary=f"Workspace with {len(renamed)} table(s): {', '.join(renamed)}.",
                       findings=findings, tables={"schema": schema},
                       artifacts={"sql_samples": "\n\n".join(samples) + "\n", "markdown_claude": "\n".join(claude_md)})
