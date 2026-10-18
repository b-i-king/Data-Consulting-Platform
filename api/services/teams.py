"""
Business teams: who may do what, and how many people a team holds.

THE THREE ROLES (dim.accounts.team_role)
----------------------------------------
  owner      The team's ADMIN. Holds the plan (the package, billing, stored
             data); adds and removes people and changes their roles; the only
             one who can delete the stored data or the business account.
  developer  Does the work: uploads files, connects QuickBooks / Xero / a
             database, loads data into storage, syncs, edits dashboard tabs,
             confirms "same customer?" matches — and sees everything the
             package pays for. No billing, no people.
  member     VIEW ONLY: sees and downloads everything (dashboards, stored data,
             reports) but changes nothing that's stored or connected.

A consultant working on the client counts as the owner's helper: they can edit.
The rule is enforced on the server (require_editor), not only by hiding buttons,
so a member can't change anything even by calling the API directly.

TEAM SIZE
---------
A team holds at most TEAM_MAX_PEOPLE people, the owner included (default 5).
Past that, "Need more people? Talk to us about Enterprise" (TEAM_FULL).

ONE TEAM PER PERSON
-------------------
An email signs up once, and an account has one team_id, so nobody can join
several teams (people who work with many businesses are consultants).

  is_colleague(account)        a developer or member (not the plan holder)
  can_edit(ctx)                False only for a member (view-only) acting as themselves
  require_editor(ctx)          403 VIEW_ONLY for a member
  seats(team_id)               {"used", "limit"}
  check_room(team_id, n)       409 TEAM_FULL if n more people wouldn't fit
  set_role(owner, id, role)    the owner changes a colleague's role
  remove(owner, id)            the owner removes a colleague (their account is deleted)
"""

from __future__ import annotations

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store

ROLES = ("owner", "developer", "member")
COLLEAGUE_ROLES = ("developer", "member")         # everyone on a team but the owner
DEFAULT_COLLEAGUE_ROLE = "developer"              # what a new colleague gets unless the owner picks "member"
ROLE_LABELS = {"owner": "Owner (admin)", "developer": "Developer", "member": "Member (view only)"}

VIEW_ONLY = ("You have view-only access (Member), so you can look at and download everything but not change it. "
             "Ask your team's owner for Developer access.")
OWNER_ONLY = "Only your team's owner can do that."


def team_full_message(limit: int) -> str:
    """The reply when a team is full: the limit, and where to go for more."""
    return (f"Your team already has {limit} of {limit} people, the most a package includes. "
            "Need more people? Talk to us about an Enterprise plan.")


def is_colleague(account: dict) -> bool:
    """
    Tell whether an account is a business colleague (developer or member), not a plan holder.

    Args:
        account: dict, an accounts row.

    Returns:
        bool: True for team_role "developer" or "member".

    Example:
        >>> is_colleague({"team_role": "member"}), is_colleague({"team_role": "owner"})
        (True, False)
    """
    return account.get("team_role") in COLLEAGUE_ROLES


def can_edit(ctx: dict) -> bool:
    """
    Tell whether this request may change stored or connected data.

    Everyone may, except a MEMBER (view-only) working in their own team's
    workspace. A consultant working on the client may.

    Args:
        ctx: dict, the acting context from get_acting_context ({"account", "acting", ...}).

    Returns:
        bool: False only for a view-only member.

    Example:
        >>> can_edit({"account": {"team_role": "member"}, "acting": False})
        False
    """
    if ctx.get("acting"):
        return True
    return ctx["account"].get("team_role") != "member"


def require_editor(ctx: dict) -> None:
    """
    Stop a view-only member from changing anything (call at the top of every endpoint that changes data).

    Args:
        ctx: dict, the acting context.

    Returns:
        None.

    Raises:
        HTTPException: 403 VIEW_ONLY for a member.

    Example:
        >>> require_editor(ctx)   # a developer: nothing happens
    """
    if not can_edit(ctx):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=VIEW_ONLY)


def max_people() -> int:
    """The most people a team holds, owner included (TEAM_MAX_PEOPLE, at least 1)."""
    return max(1, get_settings().team_max_people)


async def seats(team_id: str) -> dict:
    """
    How full a team is.

    Args:
        team_id: str, the team's id.

    Returns:
        dict: {"used": people on the team (owner included), "limit": max_people()}.

    Example:
        >>> await seats(team_id)
        {'used': 3, 'limit': 5}
    """
    return {"used": len(await account_store.list_team_members(team_id)), "limit": max_people()}


async def check_room(team_id: str | None, adding: int, already: int | None = None) -> None:
    """
    Refuse to add people past the team limit.

    Args:
        team_id: str | None, the team (None at sign-up, before the team exists).
        adding: int, how many people are about to be added.
        already: int | None, people already on the team (looked up when None; 1 at sign-up = the owner).

    Returns:
        None.

    Raises:
        HTTPException: 409 team_full_message when they wouldn't fit.

    Example:
        >>> await check_room(None, adding=4, already=1)   # sign-up with 4 colleagues: fits in 5
    """
    used = already if already is not None else (await seats(team_id))["used"]
    if used + adding > max_people():
        raise HTTPException(status.HTTP_409_CONFLICT, detail=team_full_message(max_people()))


async def _colleague_of(owner: dict, member_id: str) -> dict:
    """The colleague's row, after checking the caller owns that team; 403 / 404 otherwise."""
    if owner.get("team_role") != "owner" or not owner.get("team_id"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=OWNER_ONLY)
    member = await account_store.get_account(member_id)
    if member is None or str(member.get("team_id")) != str(owner["team_id"]) or not is_colleague(member):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That person isn't on your team.")
    return member


async def set_role(owner: dict, member_id: str, role: str) -> dict:
    """
    The owner changes a colleague's role: "developer" or "member" (view only).

    Ownership itself doesn't move here (the owner holds the plan and billing).

    Args:
        owner: dict, the team owner's accounts row (the caller).
        member_id: str, the colleague's account id.
        role: str, "developer" or "member".

    Returns:
        dict: the colleague's updated accounts row.

    Raises:
        HTTPException: 400 for any other role; 403 for a non-owner; 404 not on the team.

    Example:
        >>> (await set_role(owner, bo_id, "member"))["team_role"]
        'member'
    """
    if role not in COLLEAGUE_ROLES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Pick Developer or Member (view only).")
    member = await _colleague_of(owner, member_id)
    return await account_store.set_team_role(str(member["id"]), role) or member


async def remove(owner: dict, member_id: str) -> None:
    """
    The owner removes a colleague: their account is deleted (it only existed for this team).

    Like "Delete my account", only an anonymous row is kept (removed_by "team_owner").

    Args:
        owner: dict, the team owner's accounts row (the caller).
        member_id: str, the colleague's account id.

    Returns:
        None.

    Raises:
        HTTPException: 403 for a non-owner; 404 not on the team.

    Example:
        >>> await remove(owner, bo_id)
    """
    member = await _colleague_of(owner, member_id)
    await account_store.delete_account(str(member["id"]), by_team_owner=True)
