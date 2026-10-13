"""
Data skills endpoints (no sign-in required — like the guest cleaning flow).

    GET  /api/skills                 every skill: category, description, options
    POST /api/skills/{skill_id}/run  upload files + options -> the skill's result

The run endpoint takes the same multipart upload as /api/files/analyze, plus:
    params    JSON object of skill options, e.g. {"value_column": "amount"}
    group_id  which cleaned table to use when the upload has several (default 0)
    missing_strategy, column_actions
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

from api.services.pipeline import load_uploads, parse_column_actions
from api.services.skills_runner import run_on_uploads
from data_skills._core import SkillInputError
from data_skills.registry import list_skills, skill_ids

router = APIRouter(prefix="/skills")


@router.get("")
async def get_skills():
    return list_skills()


@router.post("/{skill_id}/run")
async def run_skill_endpoint(
    skill_id: str,
    files: Annotated[list[UploadFile], File(description="One or more data files")],
    params: Annotated[str, Form(description="Skill options as a JSON object")] = "{}",
    group_id: Annotated[int, Form()] = 0,
    missing_strategy: Annotated[Literal["keep", "drop_any"], Form()] = "keep",
    column_actions: Annotated[str, Form()] = "{}",
):
    if skill_id not in skill_ids():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown skill '{skill_id}'.")
    try:
        options = json.loads(params or "{}")
        if not isinstance(options, dict):
            raise ValueError
    except ValueError:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="'params' must be a JSON object.")

    actions = parse_column_actions(column_actions)
    datasets = await load_uploads(files)
    try:
        return run_on_uploads(skill_id, datasets, options, group_id, missing_strategy, actions)
    except SkillInputError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
