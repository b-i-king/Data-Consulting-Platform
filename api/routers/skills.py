"""
Data skills endpoints (no sign-in required — like the guest cleaning flow).

    GET  /api/skills                 every skill: category, description, options
    POST /api/skills/{skill_id}/run  upload files + options -> the skill's result

The run endpoint takes the same multipart upload as /api/files/analyze, plus:
    params    JSON object of skill options, e.g. {"value_column": "amount"}
    group_id  which cleaned table to use when the upload has several (default 0)
    missing_strategy, column_actions, column_names, transforms
              the user's missing-value choices, exactly as sent to
              /api/files/analyze, so skills see the same cleaned data

Results come back as JSON: summary, findings, tables, charts, artifacts
(Markdown, SQL, Mermaid, HTML) and data. See data_skills/_core.py.

Try it in the browser at http://localhost:8000/docs.
"""

from __future__ import annotations

import json
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status

from api.services.pipeline import load_uploads, parse_column_actions, parse_column_names, parse_transforms
from api.services.skills_runner import run_on_uploads
from data_skills._core import SkillInputError
from data_skills.registry import list_skills, skill_ids

router = APIRouter(prefix="/skills")


@router.get("")
async def get_skills():
    """
    GET /api/skills — every data skill with its category, description and options.

    No sign-in. Skills are ordered as in data_skills/catalog.json (Cleaning
    first, AI last).

    Returns:
        list[dict]: one description per skill ("id", "title", "description",
        "input", "wants_raw", "category", "category_name", "label", "primary",
        "params" — the skill's options).

    Example:
        GET /api/skills
        -> 200 [{"id": "...", "category": "data_cleaning", "title": "...", ...}, ...]
    """
    return list_skills()


@router.post("/{skill_id}/run")
async def run_skill_endpoint(
    skill_id: str,
    files: Annotated[list[UploadFile], File(description="One or more data files")],
    params: Annotated[str, Form(description="Skill options as a JSON object")] = "{}",
    group_id: Annotated[int, Form()] = 0,
    missing_strategy: Annotated[Literal["keep", "drop_any"], Form()] = "keep",
    column_actions: Annotated[str, Form()] = "{}",
    column_names: Annotated[str, Form()] = "{}",
    transforms: Annotated[str, Form()] = "{}",
):
    """
    POST /api/skills/{skill_id}/run — upload files plus options and get the skill's result.

    No sign-in. Takes the same multipart upload and cleaning choices as
    /api/files/analyze, so the skill sees the same cleaned data the user
    previewed. Order of checks: skill id, then `params` JSON, then the
    cleaning choices, then the upload, then the skill itself.

    Args:
        skill_id: str, the skill folder name from the URL, e.g. "build-dashboard".
        files: list[UploadFile], one or more data files.
        params: str, the skill's options as a JSON object, e.g. {"value_column": "amount"};
            default "{}".
        group_id: int, which cleaned table to use when the upload has several (default 0).
        missing_strategy: str, "keep" (default) or "drop_any".
        column_actions: str, JSON per-column missing-value choices (as for /analyze).
        column_names: str, JSON column renames (as for /analyze).
        transforms: str, JSON transform steps (as for /analyze).

    Returns:
        dict: the skill's result (summary, findings, tables, charts, artifacts,
        data) plus "runtime_seconds".

    Raises:
        HTTPException: 404 "Unknown skill '<id>'."; 400 "'params' must be a JSON
            object."; 400 for bad skill options or too little data
            (SkillInputError); 400/413 for upload or cleaning-choice problems.

    Example:
        POST /api/skills/build-dashboard/run   (multipart/form-data)
        files=@samples/sales.csv  params={}
        -> 200 {"summary": "...", "findings": [...], "charts": [...], "runtime_seconds": 0.412, ...}
    """
    if skill_id not in skill_ids():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown skill '{skill_id}'.")
    try:
        options = json.loads(params or "{}")
        if not isinstance(options, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="'params' must be a JSON object.")

    actions = parse_column_actions(column_actions)
    names = parse_column_names(column_names)
    steps = parse_transforms(transforms)
    datasets = await load_uploads(files)
    try:
        return run_on_uploads(skill_id, datasets, options, group_id, missing_strategy, actions, names, steps)
    except SkillInputError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
