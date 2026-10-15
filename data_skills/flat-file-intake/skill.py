"""
flat-file-intake — how several files relate (Data Cleaning).

Python implementation of README.md in this folder. Wraps the categorizer in
api/services/categorizer.py: group tables by shared columns, detect year
partitions, guess the business entity, find join keys (or fuzzy links).
Takes SEVERAL tables: {name: DataFrame}.
"""

from __future__ import annotations

import pandas as pd

from api.services.categorizer import categorize
from api.services.file_reader import LoadedDataset
from data_skills._core import SkillResult, SkillSpec

SKILL = SkillSpec(
    id="flat-file-intake",
    title="Multi-file sorting",
    input="tables",
    description="Group files by structure (same columns, split by year, different entities) and find links.",
)


def run(tables: dict[str, pd.DataFrame]) -> SkillResult:
    """
    Sort several uploaded tables into groups and find the links between them.

    A thin wrapper around api.services.categorizer.categorize: each DataFrame is wrapped
    in a LoadedDataset (size 0, type "table"), then the categorizer groups files by shared
    columns, spots year-partitioned files, guesses the business entity, and finds exact
    join keys or fuzzy links between groups.

    Args:
        tables: dict[str, pd.DataFrame], file/table name -> its data.

    Returns:
        SkillResult: summary is the categorizer's suggested next step; findings describe each
        group (with its recommendation) and each relationship; tables "groups" (label,
        structure, files, years) and "links" (which groups, on which columns, by what method).

    Example:
        >>> res = run({"sales_2023.csv": df23, "sales_2024.csv": df24, "customers.csv": cust})
        >>> [g["group"] for g in res.tables["groups"]]   # e.g. a sales group and a customers group
    """
    datasets = [LoadedDataset(name=n, source_file=n, file_type="table", df=df, size_bytes=0) for n, df in tables.items()]
    result = categorize(datasets)
    findings = [f"{g.label}: {', '.join(g.members)} — {g.recommendation}" for g in result.groups]
    findings += [r.description for r in result.relationships]
    return SkillResult(
        skill=SKILL.id,
        summary=result.next_step,
        findings=findings,
        tables={
            "groups": [{"group": g.label, "structure": g.structure, "files": "; ".join(g.members),
                        "years": ", ".join(map(str, g.years))} for g in result.groups],
            "links": [{"between": f"{result.groups[r.left_group].label} ↔ {result.groups[r.right_group].label}",
                       "on": ", ".join(r.key_columns), "method": r.method} for r in result.relationships],
        },
    )
