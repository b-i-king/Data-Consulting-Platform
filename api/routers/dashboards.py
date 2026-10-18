"""
Dashboard endpoints: business context, suggested questions, the client's own tabs.

    GET    /api/dashboards                the client's tabs, business context, industry,
                                          and most-asked questions
    POST   /api/dashboards/suggestions    questions worth a tab, for THIS table and business
    POST   /api/dashboards/tabs           add a tab (a name + saved questions)
    PUT    /api/dashboards/tabs/{id}      rename it / change its questions
    DELETE /api/dashboards/tabs/{id}      remove it
    POST   /api/questions                 remember a question asked of the data (text only)

Tabs belong to the plan holder (a business shares them) and accept
X-Acting-As, so a consultant can build them for their client. Adding or
changing tabs needs Data Visualization (or an admin looking). The logic is
api/services/dashboards.py; answering the questions is the ask-your-data
skill, run by the website on the current data.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status

from api.core.security import get_acting_context
from api.models.accounts import DashboardTabIn, QuestionLogIn
from api.services import account_store, dashboards, entitlements, teams

router = APIRouter()
VISUALIZATION = "data_visualization"


def _iso(v):
    """A datetime as ISO text (None stays None)."""
    return v.isoformat() if hasattr(v, "isoformat") else v


def _tab_out(t: dict) -> dict:
    """One tab for the browser."""
    return {"id": str(t["id"]), "name": t["name"], "questions": t["questions"], "position": t["position"],
            "updated_at": _iso(t.get("updated_at"))}


async def _can_build(ctx: dict) -> None:
    """403 unless the client's package has Data Visualization (admins always can)."""
    active = await entitlements.active_services(ctx["account"], preview_all=ctx.get("admin", False))
    if VISUALIZATION not in active:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Your own dashboard tabs are part of Data Visualization. Add it to your package first.")


async def _overview(holder: dict) -> dict:
    """Everything GET /api/dashboards returns."""
    hid = str(holder["id"])
    return {
        "tabs": [_tab_out(t) for t in await account_store.list_dashboard_tabs(hid)],
        "context": account_store.business_context_of(holder),
        "industry": dashboards.industry_label(holder.get("naics_sector")),
        # The sector code itself: the Overview asks build-dashboard for that industry's KPIs.
        "naics_sector": holder.get("naics_sector"),
        "company": holder.get("company_name"),
        "frequent": await account_store.frequent_questions(hid),
        "max_tabs": dashboards.MAX_TABS,
        "max_questions": dashboards.MAX_QUESTIONS,
    }


@router.get("/dashboards")
async def get_dashboards(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/dashboards — the client's tabs, business context, industry and most-asked questions.

    Returns:
        dict: {"tabs": [{"id", "name", "questions", "position", "updated_at"}],
        "context": {"about", "goals", "key_questions"}, "industry": "Retail Trade" | None,
        "company", "frequent": [{"question", "times"}], "max_tabs", "max_questions"}.
    """
    return await _overview(await entitlements.plan_holder(ctx["account"]))


@router.post("/dashboards/suggestions")
async def suggestions(ctx: Annotated[dict, Depends(get_acting_context)], columns: Annotated[list[dict], Body(embed=True)]):
    """
    POST /api/dashboards/suggestions — questions worth a dashboard tab, for this table and business.

    Args:
        columns: list[dict], the open table's columns [{"name", "type"}] (from the cleaning report).

    Returns:
        dict: {"suggestions": [{"question", "why", "source"}]}.

    Example:
        POST /api/dashboards/suggestions {"columns": [{"name": "amount", "type": "numeric"}, {"name": "store", "type": "text"}]}
        -> 200 {"suggestions": [{"question": "total amount by store", "why": "Which store earns most", "source": "industry"}]}
    """
    holder = await entitlements.plan_holder(ctx["account"])
    cols = [{"name": str(c.get("name", "")), "type": str(c.get("type", ""))} for c in columns[:300] if c.get("name")]
    return {"suggestions": dashboards.suggest(cols, holder.get("naics_sector"), account_store.business_context_of(holder),
                                              await account_store.frequent_questions(str(holder["id"])))}


@router.post("/dashboards/tabs", status_code=status.HTTP_201_CREATED)
async def add_tab(body: DashboardTabIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/dashboards/tabs — add a dashboard tab.

    Raises:
        HTTPException: 403 without Data Visualization or for a view-only member; 400 at the tab
            limit (10) or with no questions.
    """
    await _can_build(ctx)
    teams.require_editor(ctx)
    holder = await entitlements.plan_holder(ctx["account"])
    questions = dashboards.clean_questions(body.questions)
    if not questions:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Add at least one question to the tab.")
    if len(await account_store.list_dashboard_tabs(str(holder["id"]))) >= dashboards.MAX_TABS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"You can have up to {dashboards.MAX_TABS} tabs.")
    await account_store.save_dashboard_tab(account_id=str(holder["id"]), name=body.name.strip(), questions=questions,
                                           created_by=str(ctx["consultant"]["account_id"]) if ctx["acting"]
                                           else str(ctx["account"]["id"]))
    return await _overview(holder)


@router.put("/dashboards/tabs/{tab_id}")
async def change_tab(tab_id: str, body: DashboardTabIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """PUT /api/dashboards/tabs/{id} — rename a tab or change its questions (404 if it isn't this client's)."""
    await _can_build(ctx)
    teams.require_editor(ctx)
    holder = await entitlements.plan_holder(ctx["account"])
    questions = dashboards.clean_questions(body.questions)
    if not questions:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Add at least one question to the tab.")
    try:
        saved = await account_store.save_dashboard_tab(account_id=str(holder["id"]), name=body.name.strip(),
                                                       questions=questions, tab_id=tab_id)
    except ValueError:
        saved = None
    if saved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That tab wasn't found.")
    return await _overview(holder)


@router.delete("/dashboards/tabs/{tab_id}")
async def delete_tab(tab_id: str, ctx: Annotated[dict, Depends(get_acting_context)]):
    """DELETE /api/dashboards/tabs/{id} — remove a tab (404 if it isn't this client's; 403 for a view-only member)."""
    teams.require_editor(ctx)
    holder = await entitlements.plan_holder(ctx["account"])
    try:
        gone = await account_store.delete_dashboard_tab(str(holder["id"]), tab_id)
    except ValueError:
        gone = False
    if not gone:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That tab wasn't found.")
    return await _overview(holder)


@router.post("/questions")
async def log_question(body: QuestionLogIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/questions — remember a question asked of the data (only its text, never the data).

    Called by the website after "Ask your data" answers (or can't). Questions
    asked again and again are suggested as dashboard tabs.

    Returns:
        dict: {"ok": True}.
    """
    holder_id = await entitlements.plan_holder_id(ctx["account"])
    question = " ".join(body.question.split())[:dashboards.MAX_QUESTION_LENGTH]
    await account_store.log_question(holder_id, question, body.answered)
    return {"ok": True}
