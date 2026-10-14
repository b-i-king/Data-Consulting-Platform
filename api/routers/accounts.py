"""
Account endpoints.

    POST /api/accounts/signup         create an individual or business account
    POST /api/accounts/signin         get a new token for an existing email
    GET  /api/accounts/me             who am I? (requires token)
    GET  /api/accounts/services       services on my plan + the full catalog (requires token)
    POST /api/accounts/services       record interest in a project with a consultant (doesn't unlock tools)
    GET  /api/accounts/team           list my team (business accounts)
    POST /api/accounts/team/members   add a colleague (business owners only)

Guests only need an account at the DOWNLOAD step. Signup asks for:
  * individual: first name, last name, email
  * business:   first name, last name, email, company name (+ optional team members)
  * optional:   "I'd like a consultant to do this for me"
(Subscriptions are bought after signup: api/routers/billing.py.)

CONSULTANTS sign up and sign in here too: their email must be in
CONSULTANT_EMAILS and they must send CONSULTANT_ACCESS_CODE. Their token says
role "consultant". The services endpoints accept `X-Acting-As: <client id>`
from a consultant to work on an assigned client (api/services/consultants.py).

⚠️ SECURITY NOTE — READ BEFORE LAUNCH
-------------------------------------
For this first version, sign-in is by email only (no password, no email
verification). That keeps the guest-to-account step frictionless while we
iterate, but it means anyone who knows an email could get a token for it.
Before real clients use the platform, switch to Supabase Auth "magic link"
emails (or another verified sign-in). Steps are in docs/ROADMAP.md.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.security import create_access_token, get_acting_context, get_current_account
from api.models.accounts import (
    AccountOut,
    AddServiceIn,
    AuthOut,
    SigninIn,
    SignupIn,
    TeamMemberIn,
)
from api.services import account_store, billing, consultants, entitlements, locations, pricing
from api.services.pricing import catalog, subscription_catalog

router = APIRouter(prefix="/accounts")

# A valid sign-in token whose account no longer exists. Without a database
# (DATABASE_URL empty, e.g. a free test deploy) accounts live in memory and
# vanish whenever the server restarts or sleeps, so say that plainly. 401 tells
# the website to sign the person out.
ACCOUNT_GONE = ("We couldn't find your account. If this is a test server without a database, it may "
                "have restarted and forgotten accounts — please create your account again.")


def _account_out(acc: dict, role: str = "client", consultant_name: str | None = None) -> AccountOut:
    """Convert a stored account (dict with UUIDs/dates) to the API shape."""
    return AccountOut(
        id=str(acc["id"]),
        account_type=acc["account_type"],
        first_name=acc["first_name"],
        last_name=acc["last_name"],
        email=acc["email"],
        company_name=acc.get("company_name"),
        team_id=str(acc["team_id"]) if acc.get("team_id") else None,
        team_role=acc.get("team_role"),
        status=acc.get("status", "active"),
        industry_supersector=acc.get("industry_supersector"),
        naics_sector=acc.get("naics_sector"),
        naics_subsector=acc.get("naics_subsector"),
        company_size=acc.get("company_size"),
        location_id=acc.get("location_geoname_id"),
        location=acc.get("location_label"),
        industry_other=acc.get("industry_other"),
        role=role,
        wants_consultant=bool(acc.get("wants_consultant")),
        consultant_name=consultant_name,
    )


async def _full_account_out(acc: dict) -> AccountOut:
    """_account_out plus the role and the assigned consultant's name (looked up)."""
    role = "consultant" if await account_store.get_consultant_by_account(str(acc["id"])) else "client"
    holder_id = await entitlements.plan_holder_id(acc)
    holder = acc if holder_id == str(acc["id"]) else await account_store.get_account(holder_id)
    consultant = await account_store.get_consultant(str(holder["consultant_id"])) if holder and holder.get("consultant_id") else None
    return _account_out(acc, role, consultant["display_name"] if consultant else None)


async def _auth_response(acc: dict) -> AuthOut:
    out = await _full_account_out(acc)
    token = create_access_token(str(acc["id"]), acc["email"], acc["account_type"], out.role)
    return AuthOut(access_token=token, account=out)


@router.post("/signup", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn):
    is_consultant = consultants.is_consultant_email(body.email)
    if is_consultant:
        consultants.check_access_code(body.access_code)   # 403 without the right code
    owner = await create_client_account(body, consultant_id=None if is_consultant else "default")
    if is_consultant:
        await consultants.register(owner)
    return await _auth_response(owner)


async def create_client_account(body: SignupIn, consultant_id: str | None) -> dict:
    """
    Create the account (+ team members, + services on the plan). Shared by
    signup and by a consultant adding a client (api/routers/consultants.py).
    `consultant_id`: "default" = the default consultant if they asked for one;
    a consultant's id = assign to them; None = no consultant.
    """
    unknown = [s for s in body.service_ids if s not in {svc["id"] for svc in catalog()}]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown service id(s): {', '.join(unknown)}")
    # Check EVERY email before creating anything, so a taken address never
    # leaves a half-created signup behind (one email = one account).
    for email in [body.email, *(m.email for m in body.team_members)]:
        if await account_store.get_account_by_email(email):
            who = "You already have" if email == body.email else f"{email} already has"
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"{who} an account. Each email address can only be used once"
                       + (" — please sign in instead." if email == body.email else "."),
            )
    place = locations.get(body.location_id) if body.location_id else None
    wants = body.wants_consultant or (consultant_id not in (None, "default"))
    if consultant_id == "default":
        default = await consultants.default_consultant() if body.wants_consultant else None
        consultant_id = str(default["id"]) if default else None
    try:
        owner = await account_store.create_account(
            account_type=body.account_type,
            first_name=body.first_name.strip(),
            last_name=body.last_name.strip(),
            email=body.email,
            company_name=(body.company_name or "").strip() or None,
            industry_supersector=body.industry_supersector,
            naics_sector=body.naics_sector,
            naics_subsector=body.naics_subsector,
            company_size=body.company_size,
            location=place,
            industry_other=body.industry_other,
            wants_consultant=wants,
            consultant_id=consultant_id,
        )
    except account_store.AccountExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))

    # Business signups can invite colleagues right away. They are created as
    # "invited" accounts attached to the owner's team.
    for member in body.team_members:
        await _add_member(owner, member)

    # Projects picked on "Your needs": recorded as interest for the consultant
    # (tools unlock only with a package — api/services/entitlements.py).
    if body.service_ids:
        await entitlements.add_services(owner, body.service_ids, source="signup")
    return owner


@router.post("/signin", response_model=AuthOut)
async def signin(body: SigninIn):
    if consultants.is_consultant_email(body.email):
        consultants.check_access_code(body.access_code)   # 403 without the right code
    acc = await account_store.get_account_by_email(body.email)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No account found for that email. Please sign up.")
    await account_store.touch_last_login(str(acc["id"]))
    if consultants.is_consultant_email(body.email):
        await consultants.register(acc)
    return await _auth_response(acc)


@router.get("/me", response_model=AccountOut)
async def me(token: Annotated[dict, Depends(get_current_account)]):
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    return await _full_account_out(acc)


@router.get("/team")
async def my_team(token: Annotated[dict, Depends(get_current_account)]):
    acc = await account_store.get_account(token["sub"])
    if acc is None or not acc.get("team_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This account is not part of a team.")
    team_id = str(acc["team_id"])
    team = await account_store.get_team(team_id)
    members = await account_store.list_team_members(team_id)
    return {
        "team_id": team_id,
        "name": team["name"] if team else None,
        "members": [_account_out(m) for m in members],
    }


@router.post("/team/members", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def add_team_member(body: TeamMemberIn, token: Annotated[dict, Depends(get_current_account)]):
    owner = await account_store.get_account(token["sub"])
    if owner is None or owner.get("team_role") != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can add members.")
    return _account_out(await _add_member(owner, body))


async def _add_member(owner: dict, member: TeamMemberIn) -> dict:
    """Create an 'invited' business account on the owner's team."""
    try:
        return await account_store.create_account(
            account_type="business",
            first_name=member.first_name.strip(),
            last_name=member.last_name.strip(),
            email=member.email,
            company_name=owner.get("company_name"),
            team_id=str(owner["team_id"]),
            team_role="member",
            status="invited",
            # Colleagues share the business's industry.
            industry_supersector=owner.get("industry_supersector"),
            naics_sector=owner.get("naics_sector"),
            naics_subsector=owner.get("naics_subsector"),
            company_size=owner.get("company_size"),
        )
    except account_store.AccountExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"{member.email} already has an account. Team transfers are not supported yet.",
        )


async def _services_out(ctx: dict) -> dict:
    acc = ctx["account"]
    active = await entitlements.active_services(acc)
    holder_id = await entitlements.plan_holder_id(acc)
    subs = await account_store.list_subscriptions(holder_id)
    return {
        "active": active,
        # True when this person may add services (individuals and team owners).
        "can_add": holder_id == str(acc["id"]),
        "services": [{**s, "active": s["id"] in active} for s in catalog()],
        # Projects they'd like a consultant's help with (interest only).
        "interests": await entitlements.interests(acc),
        # Whose workspace this is (a consultant may be working on a client's).
        "account": (await _full_account_out(acc)).model_dump(),
        "acting": ctx["acting"],
        # Live subscriptions (kept in sync by Stripe) and the plans on sale.
        "subscriptions": [_subscription_out(s) for s in subs],
        "plans": subscription_catalog(),
        "billing": {"stripe": billing.stripe_enabled()},   # False = test mode, nothing is charged
        "downloads_included": await entitlements.downloads_included(acc),
        "consulting": {
            "rate_per_hour_usd": pricing.consulting_rate(),
            "free_minutes": pricing.FREE_CONSULTATION_MINUTES,
            "hours_bought": await account_store.consulting_hours(holder_id),
        },
    }


def _subscription_out(s: dict) -> dict:
    """One subscription: its tools, interval, status and what it costs per period."""
    end = s.get("current_period_end")
    plan_ids = [i["plan_id"] for i in s["items"] if i["plan_id"] in pricing.SUBSCRIPTIONS_BY_ID]
    price = pricing.selection_price(plan_ids, s["billing_interval"]) if plan_ids else {"total_usd": 0}
    return {
        "id": str(s["id"]),
        "plan_ids": plan_ids,
        "names": [pricing.SUBSCRIPTIONS_BY_ID[p].name for p in plan_ids],
        "interval": s["billing_interval"], "status": s["status"],
        "total_usd": price["total_usd"],
        "cancel_at_period_end": bool(s.get("cancel_at_period_end")),
        "current_period_end": end.date().isoformat() if end else None,
    }


@router.get("/services")
async def my_services(ctx: Annotated[dict, Depends(get_acting_context)]):
    return await _services_out(ctx)


@router.post("/services")
async def add_service(body: AddServiceIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    acc = ctx["account"]
    try:
        await entitlements.add_services(acc, [body.service_id])
    except entitlements.NotPlanOwnerError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return await _services_out(ctx)
