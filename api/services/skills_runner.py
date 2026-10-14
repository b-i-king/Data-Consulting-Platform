"""
Run a data skill on uploaded files (glue between the upload pipeline and data_skills).

WHAT THIS FILE DOES
-------------------
The website sends files plus the chosen skill and its options. This module
decides WHICH version of the data the skill sees, then runs it:

  * Skills marked `wants_raw` (data-cleaning, forensic-sweep, type-consistency
    sweep, discovery brief) get the files exactly as uploaded — they inspect
    the mess itself.
  * Every other skill gets the CLEANED data from the normal pipeline
    (api/services/pipeline.py): duplicates removed, files with the same
    structure stacked into one table, and the user's missing-value choices
    (dropped / filled columns, dropped rows) applied — the same data they
    preview and download.
      - one-table skills run on the group the user picked (group_id)
      - multi-table skills (er-diagram, flat-file-intake, ...) get every group

MINIMUM DATA: every skill outside Data Cleaning needs at least 30 rows, both
as uploaded and after cleaning (api/services/cleaning.py, Step 7). Smaller
tables get a friendly refusal instead of unreliable numbers.

Like the rest of the platform, nothing is stored: the files live only for the
duration of the request.
"""

from __future__ import annotations

import time

import pandas as pd

from api.services.cleaning import analysis_readiness
from api.services.file_reader import LoadedDataset
from api.services.pipeline import run_pipeline
from data_skills._core import SkillInputError
from data_skills.registry import describe, load_skill, run_skill

CLEANING = "data_cleaning"   # the one category that works on any number of rows


def prepare_input(skill_id: str, datasets: list[LoadedDataset], group_id: int,
                  missing_strategy: str = "keep",
                  column_actions: dict[int, dict[str, str]] | None = None) -> pd.DataFrame | dict[str, pd.DataFrame]:
    spec = load_skill(skill_id).SKILL
    needs_rows = describe(skill_id)["category"] != CLEANING
    if spec.wants_raw:
        tables = {d.name: d.df for d in datasets}
        if needs_rows and all(analysis_readiness(len(t), len(t)) for t in tables.values()):
            biggest = max(len(t) for t in tables.values())
            raise SkillInputError(analysis_readiness(biggest, biggest))
        if spec.input == "table":
            return list(tables.values())[min(group_id, len(tables) - 1)]
        return tables

    result = run_pipeline(datasets, missing_strategy, column_actions)
    if needs_rows:
        if spec.input == "tables":
            # Several tables (e.g. an ER diagram): a small lookup table is fine
            # as long as at least one table is big enough.
            if all(not g.report.analysis_ready for g in result.groups):
                raise SkillInputError(result.groups[0].report.analysis_note)
        else:
            chosen = next((g for g in result.groups if g.group_id == group_id), result.groups[0])
            if not chosen.report.analysis_ready:
                raise SkillInputError(chosen.report.analysis_note)
    groups = {g.label if sum(x.label == g.label for x in result.groups) == 1 else f"{g.label} #{g.group_id}": g.cleaned
              for g in result.groups}
    if spec.input == "tables":
        return groups
    chosen = next((g for g in result.groups if g.group_id == group_id), result.groups[0])
    return chosen.cleaned


def run_on_uploads(skill_id: str, datasets: list[LoadedDataset], params: dict, group_id: int = 0,
                   missing_strategy: str = "keep",
                   column_actions: dict[int, dict[str, str]] | None = None) -> dict:
    """Run the skill and return its JSON result plus the processing time."""
    started = time.perf_counter()
    data = prepare_input(skill_id, datasets, group_id, missing_strategy, column_actions)
    result = run_skill(skill_id, data, params, from_api=True).to_dict()
    result["runtime_seconds"] = round(time.perf_counter() - started, 3)
    return result
