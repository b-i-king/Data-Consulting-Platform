"""
Beacon, the AI assistant: "Ask your data" with a monthly allowance (free and premium).

WHY THIS EXISTS
---------------
The AI page lets anyone with data open ask questions in plain English and get
an answer with the calculation shown (the ask-your-data skill, which only
reads the data and never runs generated code). This module adds what a SaaS
needs around it — the same structure as Bolt (the BOL Group's gaming analyst):

    tier       who                                   questions / month   Gemini model (with a key)
    admin      ADMIN_EMAILS                           no limit            the premium model
    premium    a client with the AI package           AI_PREMIUM_MONTHLY  AI_MODEL_PREMIUM
                                                      (200)
    free       everyone else                          AI_FREE_MONTHLY     AI_MODEL_FREE
                                                      (20)

  * The allowance belongs to the CLIENT (the plan holder): a business's team
    shares it, and a consultant working on a client uses — and counts toward —
    THAT client's allowance and tier. Admins never have a limit.
  * Counted in dim.ai_usage (one row per client per day); the text goes to
    dim.question_log as before (it feeds "questions you ask most").
  * An admin can SIMULATE free or premium to see what clients see (like
    Bolt's owner view); simulating also applies the limit, without counting.

HOW A QUESTION IS ANSWERED (ask())
----------------------------------
  1. the allowance is checked (429 when this month's is used up);
  2. the question becomes a PLAN — {"metric", "agg", "group_by", "filters",
     "year", "limit"} — by a Google Gemini model when GEMINI_API_KEY is set
     (Flash-Lite for free, Flash for premium; "thinking" off, a short JSON
     answer: about $0.0001-0.0004 a question), else by the skill's built-in
     parser (no cost). The model sees only the
     column names and types, number/date ranges, and the values of columns
     with few distinct values (store names) — NEVER rows, and never values of
     columns with many distinct values (names, emails);
  3. the plan runs on the open table with a fixed set of pandas operations;
     if the model's plan is unusable, the built-in parser tries instead;
  4. the question is counted and logged.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

import httpx
import pandas as pd
from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store, entitlements

AI_SERVICE = "ai"                 # pricing.py: the AI package
NAME = "Beacon"                   # the assistant's name, in messages
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
MAX_CATEGORY_VALUES = 50          # a column with more distinct values sends none of them
MAX_DISTINCT_SHARE = 0.5          # ...nor one where most rows are different (names, emails, IDs)
HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None   # tests plug in a fake
TIERS = ("free", "premium")


def _month_start(today: date | None = None) -> date:
    today = today or datetime.now(timezone.utc).date()
    return today.replace(day=1)


def _reset_date(today: date | None = None) -> date:
    """The 1st of next month (UTC): when the allowance starts over."""
    first = _month_start(today)
    return date(first.year + 1, 1, 1) if first.month == 12 else date(first.year, first.month + 1, 1)


async def tier_for(ctx: dict, simulate: str | None = None) -> dict:
    """
    Which tier applies to this request, and its allowance.

    Args:
        ctx: dict, the acting context (api/core/security.py): the client's account,
            the consultant if one is acting, and whether the caller is an admin.
        simulate: "free" / "premium" — admins only: see a tier as a client would.

    Returns:
        dict: {"tier" ("admin" | "premium" | "free"), "limit" (int | None = no limit),
        "model", "holder_id", "simulating" (str | None), "counts" (bool)}.

    Example:
        >>> (await tier_for(ctx))["limit"]
        20
    """
    s = get_settings()
    holder = await entitlements.plan_holder_id(ctx["account"])
    if ctx.get("admin"):
        if simulate in TIERS:
            return {"tier": simulate, "limit": s.ai_premium_monthly if simulate == "premium" else s.ai_free_monthly,
                    "model": s.ai_model_premium if simulate == "premium" else s.ai_model_free,
                    "holder_id": holder, "simulating": simulate, "counts": False}
        return {"tier": "admin", "limit": None, "model": s.ai_model_premium, "holder_id": holder,
                "simulating": None, "counts": False}
    premium = AI_SERVICE in await entitlements.active_services(ctx["account"])
    return {"tier": "premium" if premium else "free",
            "limit": s.ai_premium_monthly if premium else s.ai_free_monthly,
            "model": s.ai_model_premium if premium else s.ai_model_free,
            "holder_id": holder, "simulating": None, "counts": True}


async def usage(ctx: dict, simulate: str | None = None) -> dict:
    """
    This month's allowance, for the assistant's usage bar.

    Returns:
        dict: {"tier", "used", "limit" (None = no limit), "left", "reset_date" (ISO),
        "is_unlimited", "simulating", "engine" ("gemini" | "built-in"), "model",
        "acting_for" (the client's name when a consultant is working on them)}.
    """
    t = await tier_for(ctx, simulate)
    used = await account_store.ai_questions_since(t["holder_id"], _month_start())
    acc = ctx["account"]
    return {
        "tier": t["tier"], "used": int(used), "limit": t["limit"],
        "left": None if t["limit"] is None else max(t["limit"] - int(used), 0),
        "reset_date": _reset_date().isoformat(), "is_unlimited": t["limit"] is None,
        "simulating": t["simulating"],
        "engine": "gemini" if get_settings().gemini_api_key else "built-in",
        "model": t["model"] if get_settings().gemini_api_key else None,
        "acting_for": (acc.get("company_name") or f"{acc['first_name']} {acc['last_name']}") if ctx.get("acting") else None,
    }


# ── The plan from a model (optional) ────────────────────────────────────────

def schema_summary(df: pd.DataFrame) -> list[dict]:
    """
    What the model may see about the table: column names and types, ranges for
    numbers and dates, and the values of CATEGORY columns — ones whose values
    repeat (at most 50 distinct, and no more than half the rows), like store
    names. Never rows; never values of columns that tell people apart
    (customer names, emails, IDs), even in a small table.

    Example:
        >>> schema_summary(pd.DataFrame({"store": ["A", "B", "A", "B"], "amount": [1.0, 2.5, 3.0, 4.0]}))
        [{'column': 'store', 'type': 'text', 'values': ['A', 'B']}, {'column': 'amount', 'type': 'number', 'min': 1.0, 'max': 4.0}]
    """
    out = []
    for col in df.columns:
        s = df[col]
        info: dict = {"column": str(col)}
        if pd.api.types.is_numeric_dtype(s):
            info.update(type="number", min=float(s.min()) if s.notna().any() else None,
                        max=float(s.max()) if s.notna().any() else None)
        elif pd.api.types.is_datetime64_any_dtype(s):
            info.update(type="date", min=str(s.min())[:10], max=str(s.max())[:10])
        else:
            info["type"] = "text"
            filled = s.dropna().astype(str)
            distinct = filled.unique()
            if len(distinct) <= MAX_CATEGORY_VALUES and len(distinct) <= MAX_DISTINCT_SHARE * len(filled):
                info["values"] = sorted(distinct.tolist())[:MAX_CATEGORY_VALUES]
        out.append(info)
    return out


_SYSTEM = """You turn a business owner's question about ONE table into a calculation plan, as JSON only.
Plan keys (all optional except agg): "metric" (a numeric column), "agg" (one of sum, mean, median, max, min, count),
"group_by" (a column), "filters" (list of {"column", "op" (=, !=, >, >=, <, <=, in), "value"}), "year" (integer),
"limit" (integer, for top N; results are sorted biggest first).
Use only columns listed in the schema. For "how many", use agg "count" with no metric.
If the table cannot answer the question, reply {"cannot": "<one short sentence why>"}.
Reply with the JSON object and nothing else."""


async def plan_with_model(question: str, df: pd.DataFrame, model: str) -> dict | None:
    """
    Ask a Google Gemini model for the plan (generateContent, JSON out, no
    "thinking" — the cheapest way: a short plan, not prose). Returns None when
    it fails or answers with something unusable (the built-in parser then tries).

    Returns:
        dict | None: a plan, {"cannot": "..."}, or None.
    """
    key = get_settings().gemini_api_key
    if not key:
        return None
    body = {
        "systemInstruction": {"parts": [{"text": _SYSTEM}]},
        "contents": [{"role": "user", "parts": [{"text": json.dumps(
            {"question": question[:500], "schema": schema_summary(df)}, default=str)}]}],
        "generationConfig": {"responseMimeType": "application/json", "temperature": 0, "maxOutputTokens": 300,
                             "thinkingConfig": {"thinkingBudget": 0}},
    }
    try:
        async with httpx.AsyncClient(timeout=30, transport=HTTP_TRANSPORT) as http:
            res = await http.post(GEMINI_URL.format(model=model), json=body,
                                  headers={"x-goog-api-key": key, "content-type": "application/json"})
        if res.status_code >= 400:
            return None
        parts = ((res.json().get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts)
        start, end = text.find("{"), text.rfind("}")
        plan = json.loads(text[start:end + 1]) if start >= 0 and end > start else None
    except (httpx.HTTPError, ValueError):
        return None
    if not isinstance(plan, dict):
        return None
    if "cannot" in plan:
        return {"cannot": str(plan["cannot"])[:300]}
    allowed = {"metric", "agg", "group_by", "filters", "year", "limit", "date_column"}
    plan = {k: v for k, v in plan.items() if k in allowed}
    known = {str(c) for c in df.columns}
    cols = [plan.get("metric"), plan.get("group_by"), *[f.get("column") for f in plan.get("filters") or []
                                                          if isinstance(f, dict)]]
    if any(c is not None and c not in known for c in cols) or plan.get("agg") not in (
            "sum", "mean", "median", "max", "min", "count"):
        return None
    plan.setdefault("filters", [])
    return plan


# ── One question ─────────────────────────────────────────────────────────────

async def check_allowance(ctx: dict, simulate: str | None = None) -> dict:
    """
    The tier, after making sure a question is still allowed this month.

    Raises:
        HTTPException: 429 when this month's allowance is used up.
    """
    t = await tier_for(ctx, simulate)
    if t["limit"] is not None:
        used = await account_store.ai_questions_since(t["holder_id"], _month_start())
        if used >= t["limit"]:
            more = ("Add the AI package for more." if t["tier"] == "free"
                    else "It starts over on the 1st.")
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail=f"This month's {t['limit']} {NAME} questions are used up. {more}")
    return t


async def record(t: dict, question: str, answered: bool) -> None:
    """Count the question (unless an admin asked or is simulating) and log its text."""
    today = datetime.now(timezone.utc).date()
    if t["counts"]:
        await account_store.add_ai_question(t["holder_id"], today)
    await account_store.log_question(t["holder_id"], question, answered)
