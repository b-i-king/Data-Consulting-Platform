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
                  column_actions: dict[int, dict[str, str]] | None = None,
                  column_names: dict[int, dict[str, str]] | None = None,
                  transforms: dict[int, list[dict]] | None = None) -> pd.DataFrame | dict[str, pd.DataFrame]:
    """
    Decide which version of the uploaded data a skill sees, and check its size.

    Rules:
    - `wants_raw` skills get the files exactly as uploaded (no cleaning):
      a one-table skill gets the table at position `group_id` (or the last one
      if `group_id` is too big); a multi-table skill gets every table by name.
    - Every other skill gets the CLEANED data from run_pipeline(), with the
      user's missing-value choices, renames and transform steps applied:
      a one-table skill gets the chosen group (or the first group if
      `group_id` is not found); a multi-table skill gets every group, keyed by
      its label ("<label> #<group id>" when two groups share a label).
    - Skills outside Data Cleaning need at least 30 rows (cleaning.py, Step 7).
      For one-table skills the chosen table must be big enough; for
      multi-table skills at least ONE table must be (a small lookup table is fine).

    Args:
        skill_id: the skill folder name, e.g. "build-dashboard".
        datasets: the tables from load_uploads().
        group_id: which table/group a one-table skill should use.
        missing_strategy: the default missing-value handling for the pipeline ("keep").
        column_actions: group id -> {column -> action}, passed to run_pipeline().
        column_names: group id -> {column key -> new name}, passed to run_pipeline().
        transforms: group id -> list of transform steps, passed to run_pipeline().

    Returns:
        pd.DataFrame | dict[str, pd.DataFrame]: one table for skills whose
        input is "table", or {name: table} for skills whose input is "tables".

    Raises:
        SkillInputError: if the skill id is unknown, or the data is too small to
            analyze (the message is shown to the user).
        HTTPException: 400 from run_pipeline() when the user's cleaning choices are invalid.

    Example:
        df = prepare_input("build-dashboard", datasets, group_id=0)   # one cleaned DataFrame
        tables = prepare_input("er-diagram", datasets, group_id=0)    # {group label: cleaned DataFrame}
    """
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

    result = run_pipeline(datasets, missing_strategy, column_actions, column_names, transforms)
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
                   column_actions: dict[int, dict[str, str]] | None = None,
                   column_names: dict[int, dict[str, str]] | None = None,
                   transforms: dict[int, list[dict]] | None = None) -> dict:
    """
    Run the skill and return its JSON result plus the processing time.

    Prepares the input with prepare_input() (raw or cleaned data, size checks),
    runs the skill through the registry with from_api=True, and adds the total
    time taken (preparing + running) so runtime billing is accurate.

    Args:
        skill_id: the skill folder name, e.g. "build-dashboard".
        datasets: the tables from load_uploads().
        params: the skill's options from the web form (validated by run_skill).
        group_id: which table/group a one-table skill should use (default 0).
        missing_strategy: the default missing-value handling for the pipeline ("keep").
        column_actions: group id -> {column -> action}.
        column_names: group id -> {column key -> new name}.
        transforms: group id -> list of transform steps.

    Returns:
        dict: the skill's result (SkillResult.to_dict()) plus "runtime_seconds"
        (float, rounded to 3 decimals).

    Raises:
        SkillInputError: for an unknown skill, bad options, or too little data.
        HTTPException: 400 when the user's cleaning choices are invalid.

    Example:
        out = run_on_uploads("build-dashboard", datasets, params={})
        out["runtime_seconds"]   # e.g. 0.412 — seconds spent preparing and running
    """
    started = time.perf_counter()
    data = prepare_input(skill_id, datasets, group_id, missing_strategy, column_actions, column_names, transforms)
    result = run_skill(skill_id, data, params, from_api=True).to_dict()
    result["runtime_seconds"] = round(time.perf_counter() - started, 3)
    return result
