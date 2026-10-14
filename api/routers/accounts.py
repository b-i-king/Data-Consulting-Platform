"""
Account endpoints.

    POST /api/accounts/signup         create an individual or business account
    POST /api/accounts/signin         get a new token for an existing email
    GET  /api/accounts/me             who am I? (requires token)
    GET  /api/accounts/services       services on my plan + the full catalog (requires token)
    POST /api/accounts/services       add a service to my plan (requires token; see entitlements.py)
    GET  /api/accounts/team           list my team (business accounts)
    POST /api/accounts/team/members   add a colleague (business owners only)

Guests only need an account at the DOWNLOAD step. Signup asks for:
  * individual: first name, last name, email
  * business:   first name, last name, email, company name (+ optional team members)

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

from api.core.security import create_access_token, get_current_account
from api.models.accounts import AccountOut, AddServiceIn, AuthOut, SigninIn, SignupIn, TeamMemberIn
from api.services import account_store, entitlements, locations
from api.services.pricing import catalog

router = APIRouter(prefix="/accounts")


def _account_out(acc: dict) -> AccountOut:
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
    )


def _auth_response(acc: dict) -> AuthOut:
    token = create_access_token(str(acc["id"]), acc["email"], acc["account_type"])
    return AuthOut(access_token=token, account=_account_out(acc))


@router.post("/signup", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn):
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
        )
    except account_store.AccountExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))

    # Business signups can invite colleagues right away. They are created as
    # "invited" accounts attached to the owner's team.
    for member in body.team_members:
        await _add_member(owner, member)

    # Services chosen in the questionnaire go on the plan (placeholder until payments).
    if body.service_ids:
        await entitlements.add_services(owner, body.service_ids, source="signup")

    return _auth_response(owner)


@router.post("/signin", response_model=AuthOut)
async def signin(body: SigninIn):
    acc = await account_store.get_account_by_email(body.email)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No account found for that email. Please sign up.")
    await account_store.touch_last_login(str(acc["id"]))
    return _auth_response(acc)


@router.get("/me", response_model=AccountOut)
async def me(token: Annotated[dict, Depends(get_current_account)]):
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return _account_out(acc)


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


async def _services_out(acc: dict) -> dict:
    active = await entitlements.active_services(acc)
    holder = await entitlements.plan_holder_id(acc)
    return {
        "active": active,
        # True when this person may add services (individuals and team owners).
        "can_add": holder == str(acc["id"]),
        "services": [{**s, "active": s["id"] in active} for s in catalog()],
    }


@router.get("/services")
async def my_services(token: Annotated[dict, Depends(get_current_account)]):
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found.")
    return await _services_out(acc)


@router.post("/services")
async def add_service(body: AddServiceIn, token: Annotated[dict, Depends(get_current_account)]):
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Account not found.")
    try:
        await entitlements.add_services(acc, [body.service_id])
    except entitlements.NotPlanOwnerError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return await _services_out(acc)
