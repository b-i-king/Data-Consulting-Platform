"""
Partner endpoints: the home page's "Partners" carousel (api/services/partners.py).

    GET    /api/partners                       the carousel (public)
    GET    /api/partners/{partner_id}/logo     one partner's logo, WebP (public while shown;
                                               the business itself and admins see it before approval)
    GET    /api/partners/me                    my requirements, eligibility and application
    POST   /api/partners/me                    apply / resubmit (multipart: website_url, linkedin_url, logo, agreed)
    DELETE /api/partners/me                    take my logo off the home page
    GET    /api/partners/admin/list            admins: applications (?status=pending)
    POST   /api/partners/admin/{partner_id}    admins: {"approve": true} or {"approve": false, "reason": "..."}
    POST   /api/partners/admin/add             admins: add a company they work with directly (no account;
                                               multipart: business_name, website_url, logo) — live at once
    DELETE /api/partners/admin/{partner_id}    admins: take a partner off the list

Routers stay thin: every rule is in the service.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from pydantic import BaseModel, Field

from api.core.security import get_acting_context, get_optional_acting_context
from api.services import entitlements, partners, teams

router = APIRouter(prefix="/partners")


class PartnerDecisionIn(BaseModel):
    approve: bool
    reason: str | None = Field(default=None, max_length=500)


def _require_admin(ctx: dict) -> None:
    if not ctx.get("admin"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This is for admins only.")


@router.get("")
async def carousel():
    """GET /api/partners — approved partners on a paid plan (at most 30, a new order each day), and the rules."""
    return await partners.public_list()


@router.get("/me")
async def my_partner_status(ctx: Annotated[dict, Depends(get_acting_context)]):
    """GET /api/partners/me — the requirements, whether I can apply now, and my application."""
    return await partners.status_for(ctx["account"])


@router.post("/me")
async def apply_as_partner(
    ctx: Annotated[dict, Depends(get_acting_context)],
    website_url: Annotated[str, Form(max_length=300)],
    logo: Annotated[UploadFile, File(description="PNG, JPEG or WebP, up to 2 MB")],
    agreed: Annotated[bool, Form()] = False,
    linkedin_url: Annotated[str, Form(max_length=300)] = "",
):
    """
    POST /api/partners/me — submit (or resubmit) the logo and website; an admin reviews it.

    Raises:
        HTTPException: 400 / 403 / 413 from partners.apply (requirements not met, bad logo, ...);
            403 for a view-only team member.
    """
    teams.require_editor(ctx)
    raw = await logo.read(partners.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    await logo.close()
    return await partners.apply(ctx["account"], website_url, linkedin_url, raw, agreed)


@router.delete("/me")
async def withdraw_partner(ctx: Annotated[dict, Depends(get_acting_context)]):
    """DELETE /api/partners/me — take my logo off the home page (apply again any time)."""
    teams.require_editor(ctx)
    return await partners.withdraw(ctx["account"])


@router.get("/admin/list")
async def admin_partner_list(ctx: Annotated[dict, Depends(get_acting_context)], status_filter: str | None = None):
    """GET /api/partners/admin/list?status_filter=pending — applications to review (admins only)."""
    _require_admin(ctx)
    return {"partners": await partners.admin_list(status_filter)}


def _admin_id(ctx: dict) -> str | None:
    return None if ctx["acting"] else str(ctx["account"]["id"])   # who decided (not saved while acting)


@router.post("/admin/add")
async def admin_add_partner(
    ctx: Annotated[dict, Depends(get_acting_context)],
    business_name: Annotated[str, Form(max_length=120)],
    website_url: Annotated[str, Form(max_length=300)],
    logo: Annotated[UploadFile, File(description="PNG, JPEG or WebP, up to 2 MB")],
):
    """POST /api/partners/admin/add — add a company you work with directly (no account); live at once (admins only)."""
    _require_admin(ctx)
    raw = await logo.read(partners.MAX_UPLOAD_MB * 1024 * 1024 + 1)
    await logo.close()
    return await partners.add_by_admin(_admin_id(ctx), business_name, website_url, raw)


@router.post("/admin/{partner_id}")
async def admin_partner_decision(partner_id: str, body: PartnerDecisionIn,
                                 ctx: Annotated[dict, Depends(get_acting_context)]):
    """POST /api/partners/admin/{partner_id} — approve, or turn down with a reason (admins only)."""
    _require_admin(ctx)
    return await partners.decide(_admin_id(ctx), partner_id, body.approve, body.reason)


@router.delete("/admin/{partner_id}")
async def admin_remove_partner(partner_id: str, ctx: Annotated[dict, Depends(get_acting_context)]):
    """DELETE /api/partners/admin/{partner_id} — take a partner off the list for good (admins only)."""
    _require_admin(ctx)
    await partners.remove(partner_id)
    return {"removed": partner_id}


@router.get("/{partner_id}/logo")
async def partner_logo(partner_id: str, ctx: Annotated[dict | None, Depends(get_optional_acting_context)]):
    """
    GET /api/partners/{account_id}/logo?v=... — the logo as WebP.

    Cached by browsers for a day (the ?v= changes with each new logo).
    """
    admin = bool(ctx) and bool(ctx.get("admin"))
    viewer = str(await entitlements.plan_holder_id(ctx["account"])) if ctx else None
    body = await partners.logo(partner_id, viewer_account_id=viewer, admin=admin)
    # A signed-in request may be for a logo that isn't public yet: keep it out of shared caches.
    mine = ctx is not None
    return Response(body, media_type="image/webp",
                    headers={"Cache-Control": "private, max-age=300" if mine else "public, max-age=86400"})
