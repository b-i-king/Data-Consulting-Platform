"""
Account endpoints.

    POST /api/accounts/signup         create an individual or business account
    POST /api/accounts/signin         get a new token for an existing email
    GET  /api/accounts/me             who am I? (requires token)
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
from api.models.accounts import AccountOut, AuthOut, SigninIn, SignupIn, TeamMemberIn
from api.services import account_store

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
    )


def _auth_response(acc: dict) -> AuthOut:
    token = create_access_token(str(acc["id"]), acc["email"], acc["account_type"])
    return AuthOut(access_token=token, account=_account_out(acc))


@router.post("/signup", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn):
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
        )
    except account_store.AccountExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))

    # Business signups can invite colleagues right away. They are created as
    # "invited" accounts attached to the owner's team.
    for member in body.team_members:
        await _add_member(owner, member)

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
