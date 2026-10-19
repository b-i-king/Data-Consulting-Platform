"""
Who may run which data skill: the PAID tools stay paid (POST /api/skills/{id}/run).

WHY THIS EXISTS
---------------
Each skill belongs to one of the seven services (data_skills/catalog.json).
The website used to lock paid tools only on screen — the server ran any skill
for anyone, signed in or not. Now the SERVER decides:

  * DATA CLEANING skills: everyone, always (it's included with every account,
    and the guest flow needs it).
  * Any other skill, FULL result: the caller is signed in and the service is in
    the package of the account they're working on (their own, or — for a
    consultant — their client's; admins and testers get everything). See
    entitlements.active_services.
  * Otherwise, a PREVIEW: ONE per tool (service) per visit, a small taste with
    nothing to download:
        - the dashboard (Data Visualization): the headline KPIs only;
        - every other skill: the one-line summary and its first finding.
    Charts, tables, the data behind them and downloadable files (HTML, SQL,
    Markdown) are never in a preview; `preview_hidden` says how much the full
    result has, so the person sees what they'd get.
  * A second preview of the same tool in the same visit: 402 "Add <service>
    to your package to see the full results."

WHAT IS A "VISIT"?
------------------
The same IP address within SKILL_PREVIEW_HOURS (default 8 hours — the same as
an idle sign-in). A guest has no account, so the IP address is the only thing
the server can count by; colleagues behind one office address share a visit.
Counts live in memory, per server process, like the request limits
(api/core/rate_limit.py): nothing is stored, and a restart starts over.

  access(skill_id, ctx)           "full" or "preview"
  preview_available(ip, service)  is this visit's preview of that tool still unused?
  use_preview(ip, service)        mark it used (after the skill ran successfully)
  preview_result(result, ...)     cut a full result down to the preview
  require(ctx, service)           for paid extras with no preview (Compare over time): 403 without it
"""

from __future__ import annotations

import time

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import entitlements
from api.services.pricing import SERVICES_BY_ID
from data_skills.registry import list_skills

ALWAYS_FREE = "data_cleaning"
# Skills that a second service also unlocks: the dashboard's own tabs answer
# saved questions with ask-your-data (components/explore/DashboardTabs.tsx).
ALSO_UNLOCKED_BY: dict[str, tuple[str, ...]] = {"ask-your-data": ("data_visualization",)}
DASHBOARD_SKILL = "build-dashboard"
PREVIEW_FINDINGS = 1      # findings kept in a preview
PREVIEW_KPIS = 4          # dashboard KPI cards kept in a preview

_previews: dict[tuple[str, str], float] = {}   # (ip, service id) -> when this visit's preview ends (time.time())


def service_of(skill_id: str) -> str:
    """
    The service (category) a skill belongs to (data_skills/catalog.json).

    Args:
        skill_id: str, e.g. "build-dashboard".

    Returns:
        str: the service id, e.g. "data_visualization".

    Example:
        >>> service_of("er-diagram")
        'data_engineering'
    """
    return next(s["category"] for s in list_skills() if s["id"] == skill_id)


def service_name(service_id: str) -> str:
    """The service's name for messages: "Data Visualization"."""
    svc = SERVICES_BY_ID.get(service_id)
    return svc.name if svc else service_id


async def access(skill_id: str, ctx: dict | None) -> str:
    """
    Full result or preview for this caller.

    Args:
        skill_id: str, a known skill id.
        ctx: dict | None, the acting context (security.get_acting_context) when
            signed in; None for a guest.

    Returns:
        str: "full" or "preview".

    Example:
        >>> await access("data-cleaning", None), await access("build-dashboard", None)
        ('full', 'preview')
    """
    service = service_of(skill_id)
    if service == ALWAYS_FREE:
        return "full"
    if ctx is None:
        return "preview"
    active = await entitlements.active_services(ctx["account"], preview_all=ctx.get("admin", False))
    unlocking = (service, *ALSO_UNLOCKED_BY.get(skill_id, ()))
    return "full" if any(s in active for s in unlocking) else "preview"


def _visit_hours() -> float:
    """How long one visit's preview lasts (SKILL_PREVIEW_HOURS)."""
    return float(get_settings().skill_preview_hours)


def preview_available(ip: str, service: str, now: float | None = None) -> bool:
    """
    Is this visit's preview of `service` still unused?

    Args:
        ip: str, the visitor's IP address (rate_limit.client_ip).
        service: str, the service id.
        now: float | None, time.time() (tests pass their own).

    Returns:
        bool: True when no preview of this tool was used from this address in
        the last SKILL_PREVIEW_HOURS.
    """
    now = time.time() if now is None else now
    ends = _previews.get((ip, service))
    return ends is None or ends <= now


def use_preview(ip: str, service: str, now: float | None = None) -> None:
    """
    Mark this visit's preview of `service` as used (called only after the skill ran).

    Also drops expired entries, so the memory never grows without bound.

    Args:
        ip: str, the visitor's IP address.
        service: str, the service id.
        now: float | None, time.time() (tests pass their own).
    """
    now = time.time() if now is None else now
    for key in [k for k, ends in _previews.items() if ends <= now]:
        _previews.pop(key)
    _previews[(ip, service)] = now + _visit_hours() * 3600


def used_message(service: str) -> str:
    """The 402 message once this visit's preview of a tool is used."""
    name = service_name(service)
    return (f"You've seen this visit's preview of {name}. Add {name} to your package to see the full "
            "results and download them.")


def preview_result(result: dict, skill_id: str) -> dict:
    """
    Cut a full skill result down to the preview.

    Kept: the skill id, the summary and the first PREVIEW_FINDINGS findings —
    and, for the dashboard, its first PREVIEW_KPIS headline KPIs (the
    `kpis_<range>` table). Dropped: every chart, the other tables, the data
    behind them and all downloadable files.

    Args:
        result: dict, the full result (SkillResult.to_dict() + runtime_seconds).
        skill_id: str, the skill that ran.

    Returns:
        dict: the preview, with "preview": True, "preview_note" (what to do to see
        everything) and "preview_hidden" ({"findings", "tables", "charts", "files"}:
        how much the full result has that the preview doesn't show).

    Example:
        >>> p = preview_result(full, "explore-data")
        >>> p["charts"], p["artifacts"], p["preview"]
        ([], {}, True)
    """
    service = service_of(skill_id)
    findings = list(result.get("findings") or [])
    tables = dict(result.get("tables") or {})
    kept_tables: dict[str, list] = {}
    if skill_id == DASHBOARD_SKILL:
        kpi_key = next((k for k in tables if k.startswith("kpis_")), None)
        if kpi_key:
            kept_tables[kpi_key] = tables[kpi_key][:PREVIEW_KPIS]
    name = service_name(service)
    return {
        "skill": result.get("skill", skill_id),
        "summary": result.get("summary", ""),
        "findings": findings[:PREVIEW_FINDINGS],
        "tables": kept_tables,
        "charts": [],
        "artifacts": {},
        "data": {},
        "runtime_seconds": result.get("runtime_seconds"),
        "preview": True,
        "preview_service": service,
        "preview_note": (f"This is a one-time preview of {name} for this visit. Add {name} to your package "
                         "to see everything — the charts, tables and downloads."),
        "preview_hidden": {
            "findings": max(0, len(findings) - PREVIEW_FINDINGS),
            "tables": len(tables) - len(kept_tables),
            "charts": len(result.get("charts") or []),
            "files": len(result.get("artifacts") or {}),
        },
    }


async def require(ctx: dict, service: str) -> None:
    """
    Refuse (403) unless `service` is in the package of the account this request is for.

    For paid extras that have no preview, e.g. "Compare over time" on the
    Dashboard page (api/routers/compare.py).

    Args:
        ctx: dict, the acting context (security.get_acting_context).
        service: str, the service id, e.g. "data_visualization".

    Raises:
        HTTPException: 403 "Compare over time needs Data Visualization in your package."
    """
    if service not in await entitlements.active_services(ctx["account"], preview_all=ctx.get("admin", False)):
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail=f"This needs {service_name(service)} in your package.")
