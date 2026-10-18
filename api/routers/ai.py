"""
Beacon, the AI assistant ("Ask your data", with a monthly allowance).

    POST /api/ai/ask     one question about the open table (the upload is sent
                         again, like every skill run — nothing is stored)
    GET  /api/ai/usage   this month's allowance: tier, used, limit, reset date

Free for every client (AI_FREE_MONTHLY questions a month), premium with the AI
package (AI_PREMIUM_MONTHLY), no limit for admins; a consultant uses the
allowance of the client they're working on. Admins can pass
simulate=free|premium to see a tier as a client would.
The rules: api/services/ai_assistant.py. Accepts X-Acting-As.
"""

from __future__ import annotations

import time
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status

from api.core.security import get_acting_context
from api.services import ai_assistant, audit
from api.services.pipeline import load_uploads, parse_column_actions, parse_column_names, parse_transforms
from api.services.skills_runner import prepare_input
from data_skills._core import SkillInputError
from data_skills.registry import run_skill

router = APIRouter(prefix="/ai")
SKILL = "ask-your-data"


@router.get("/usage")
async def ai_usage(ctx: Annotated[dict, Depends(get_acting_context)], simulate: str | None = Query(default=None)):
    """
    GET /api/ai/usage — this month's AI allowance.

    Returns:
        dict: {"tier" ("admin" | "premium" | "free"), "used", "limit" (null = no limit),
        "left", "reset_date", "is_unlimited", "simulating", "engine", "model", "acting_for"}.

    Example:
        GET /api/ai/usage  ->  {"tier": "free", "used": 3, "limit": 20, "left": 17, "reset_date": "2026-11-01", ...}
    """
    return await ai_assistant.usage(ctx, simulate)


@router.post("/ask")
async def ask(
    ctx: Annotated[dict, Depends(get_acting_context)],
    files: Annotated[list[UploadFile], File()],
    question: Annotated[str, Form(min_length=1, max_length=500)],
    group_id: Annotated[int, Form()] = 0,
    missing_strategy: Annotated[Literal["keep", "drop_any"], Form()] = "keep",
    column_actions: Annotated[str, Form()] = "{}",
    column_names: Annotated[str, Form()] = "{}",
    transforms: Annotated[str, Form()] = "{}",
    simulate: Annotated[str, Form()] = "",
):
    """
    POST /api/ai/ask — answer one plain-English question about the open table.

    Checks the allowance first (429 when used up), then plans the question
    (a Google Gemini model when GEMINI_API_KEY is set, else the built-in parser),
    runs the plan read-only, and counts and logs the question.

    Returns:
        dict: {"answered": bool, "result": the skill result (summary, findings,
        tables) or null, "message": text when it couldn't be answered,
        "engine": "gemini" | "built-in", "usage": the allowance after this question,
        "runtime_seconds"}.

    Raises:
        HTTPException: 429 allowance used up; 400 / 413 for upload problems.

    Example:
        POST /api/ai/ask  files=@sales.csv question="total amount by store"
        -> 200 {"answered": true, "result": {"summary": "Midtown leads with ...", ...}, "usage": {"used": 4, ...}}
    """
    started = time.perf_counter()
    t = await ai_assistant.check_allowance(ctx, simulate or None)
    datasets = await load_uploads(files)
    try:
        df = prepare_input(SKILL, datasets, group_id, missing_strategy, parse_column_actions(column_actions),
                           parse_column_names(column_names), parse_transforms(transforms))
    except SkillInputError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    question = question.strip()
    plan = await ai_assistant.plan_with_model(question, df, t["model"])
    engine = "gemini" if plan is not None else "built-in"
    result, message = None, None
    attempts = ([{"plan": plan}] if plan and "cannot" not in plan else []) + [{"question": question}]
    for params in attempts:
        try:
            result = run_skill(SKILL, df, params, from_api=True).to_dict()
            break
        except SkillInputError as exc:
            message = str(exc)
    if result is None and plan and "cannot" in plan:
        message = plan["cannot"]
    await ai_assistant.record(t, question, answered=result is not None)
    await audit.record(ctx, "ai_question", {"answered": result is not None})   # never the question's answer
    return {"answered": result is not None, "result": result,
            "message": None if result else (message or "I can't answer that from this table yet."),
            "engine": engine, "usage": await ai_assistant.usage(ctx, simulate or None),
            "runtime_seconds": round(time.perf_counter() - started, 3)}

