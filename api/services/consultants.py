"""
Consultants: who they are, how full they are, which clients they manage, and
who may act for whom.

WHAT THIS FILE DOES
-------------------
The platform serves two kinds of clients:
  * DO IT YOURSELF — they sign up and use the tools themselves.
  * DONE FOR YOU   — they tick "I'd like a consultant to do this for me", pick
                     a consultant by name (or let us pick), and choose a
                     one-time analysis or monthly help. The consultant signs in
                     and does the work in their workspace.

WHO IS A CONSULTANT? (no access codes)
--------------------------------------
  * ADMINS are the ADMIN_EMAILS addresses (normally just the owner). An admin
    is always a consultant; the first admin is the DEFAULT consultant.
  * Anyone else ASKS ("I'd like to work as a consultant" at sign-up, or in the
    workspace) and waits. An admin APPROVES or DENIES in the Admin panel.
    Approval creates their dim.consultants row at once, so their next page
    load shows the consultant workspace. Nobody can make themselves a consultant.

  is_admin_email(email)       is this address in ADMIN_EMAILS?
  sync_roles(account)         at sign-up / sign-in: admins get their flags and
                              consultant row; approved consultants get their row
  request(account)            "I'd like to work as a consultant"
  decide(account_id, ok, me)  an admin approves or denies
  default_consultant()        who "do it for me" clients go to when they pick nobody
  pick(consultant_id)         CAPACITY RULE: the consultant a new client gets
  directory()                 every consultant with how full they are (the picker)
  set_capacity(consultant, n) a consultant's own setting, 1 to MAX_CAPACITY
  acting_context(token, id)   THE ACCESS RULE (see below)
  active_consultant(id)       the consultant row, only while they're ACTIVE
  deactivate(id, admin)       an admin switches a consultant off; their clients
                              move to someone with room (or wait)
  reactivate(id, admin)       an admin switches them back on
  team()                      every consultant, active or not (Admin panel)

DEACTIVATED CONSULTANTS
-----------------------
dim.consultants.active = false (set by an admin; deactivated_at / _by / reason
are stamped). They keep their account and sign in as a client: no consultant
workspace, can't be picked, can't open any client. Reactivating stamps
reactivated_at / _by. Admins and the default consultant can't be deactivated.

CAPACITY
--------
Each consultant takes `capacity` clients at once (default 3, at most
MAX_CAPACITY). The picker shows a fill bar: green while under half full,
yellow from half, red when full. A full consultant can't be picked; a client
who picks nobody goes to the default consultant if they have room, else to
whoever has the most room, else waits (assigned when someone has room).

THE ACCESS RULE ("row-level security" in the API)
-------------------------------------------------
Every signed-in request names who it's for:
  * no `X-Acting-As` header -> the caller's own account.
  * `X-Acting-As: <client account id>` -> allowed ONLY when the caller is a
    consultant AND that client (or their business's team owner) is assigned to
    them (accounts.consultant_id) — or the caller is an ADMIN (any client,
    just in case). Anyone else gets 403 — a client can never act as another
    client, and a consultant can never open someone else's client.
The database repeats the safe default (RLS on, no public access); see db/schema.sql.
Before real clients, add two-factor sign-in for consultants (Supabase MFA) —
docs/ROADMAP.md.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store
from api.services.entitlements import is_admin, plan_holder_id

MAX_CAPACITY = 10   # the most clients one consultant can take (same CHECK in db/schema.sql)
ACCOUNT_GONE = ("We couldn't find your account. If this is a test server without a database, it may "
                "have restarted and forgotten accounts — please create your account again.")


def is_admin_email(email: str) -> bool:
    """
    Tell whether an email address is listed in ADMIN_EMAILS.

    The comparison ignores letter case (the settings list is already lower-cased).

    Args:
        email: str, the address someone is signing in or signing up with.

    Returns:
        bool: True if the address belongs to an admin.

    Example:
        >>> is_admin_email("Pat@Example.com")   # ADMIN_EMAILS="pat@example.com"
        True
    """
    return email.lower() in get_settings().admin_email_list


async def register(account: dict) -> dict:
    """
    Make sure a consultant's row exists (the dim.consultants table).

    A consultant is the DEFAULT when their email is the first ADMIN_EMAILS
    address (refreshed on every call). The default consultant also picks up
    clients who asked for a consultant before anyone had room — but only as
    many as their capacity leaves free.

    Args:
        account: dict, the consultant's accounts row (needs "id", "first_name",
            "email").

    Returns:
        dict: the consultants row (with "id", "is_default", "capacity", ...).

    Example:
        >>> row = await register(pat_account)   # Pat is first in ADMIN_EMAILS
        >>> row["is_default"]
        True
    """
    admins = get_settings().admin_email_list
    consultant = await account_store.ensure_consultant(account, is_default=bool(admins) and account["email"] == admins[0])
    if consultant["is_default"]:
        free = consultant.get("capacity", account_store.DEFAULT_CAPACITY) - await account_store.count_clients(str(consultant["id"]))
        await account_store.assign_waiting_clients(str(consultant["id"]), limit=free)
    return consultant


async def sync_roles(account: dict) -> dict:
    """
    Bring an account's roles up to date at sign-up and sign-in.

      * an ADMIN_EMAILS address -> is_admin and is_consultant set, consultant row made
      * an approved consultant  -> consultant row made (if it was ever missing)

    Args:
        account: dict, the accounts row that just signed up or signed in.

    Returns:
        dict: the account row, re-read when anything changed.

    Example:
        >>> (await sync_roles(my_account))["is_admin"]   # my email is in ADMIN_EMAILS
        True
    """
    if is_admin_email(account["email"]) and not account.get("is_admin"):
        await account_store.set_admin(str(account["id"]))
        account = await account_store.get_account(str(account["id"])) or account
    if account.get("is_admin") or account.get("is_consultant"):
        await register(account)
    return account


async def request(account: dict) -> dict:
    """
    "I'd like to work as a consultant": put the request on the admin's list.

    Args:
        account: dict, the signed-in person asking (their own account, never a client's).

    Returns:
        dict: the account row after the request.

    Raises:
        HTTPException: 409 if they already are a consultant or already asked.

    Example:
        >>> (await request(kim))["consultant_request"]
        'pending'
    """
    if account.get("is_consultant") or account.get("is_admin"):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="You're already a consultant.")
    if account.get("consultant_request") == "pending":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Your request is already waiting for approval.")
    await account_store.request_consultant(str(account["id"]))
    return await account_store.get_account(str(account["id"])) or account


async def decide(account_id: str, approved: bool, admin: dict) -> dict:
    """
    An admin approves or denies a consultant request.

    Approving creates the person's dim.consultants row straight away (capacity
    3), so they can take clients the next time their workspace loads.

    Args:
        account_id: str, the person who asked.
        approved: bool, True = approve, False = deny.
        admin: dict, the admin's accounts row.

    Returns:
        dict: the person's account row after the decision.

    Raises:
        HTTPException: 404 if the account doesn't exist; 400 if they never asked.

    Example:
        >>> (await decide(kim_id, True, me))["is_consultant"]
        True
    """
    person = await account_store.get_account(account_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That account was not found.")
    if not person.get("consultant_request"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This person hasn't asked to be a consultant.")
    await account_store.decide_consultant_request(account_id, approved, str(admin["id"]))
    person = await account_store.get_account(account_id) or person
    if approved:
        await register(person)
    return person


def load(consultant: dict) -> dict:
    """
    How full one consultant is, for the picker's fill bar (no email: clients see this).

    Args:
        consultant: dict, a consultants row with "client_count" (list_consultants).

    Returns:
        dict: {"id", "name", "capacity", "clients", "percent", "full", "is_default"}.
            percent = clients / capacity * 100 (0-100).

    Example:
        >>> load({"id": "c1", "display_name": "Kim", "capacity": 4, "client_count": 2})["percent"]
        50
    """
    capacity = consultant.get("capacity") or account_store.DEFAULT_CAPACITY
    clients = consultant.get("client_count", 0)
    return {
        "id": str(consultant["id"]),
        "name": consultant["display_name"],
        "capacity": capacity,
        "clients": clients,
        "percent": min(100, round(clients / capacity * 100)),
        "full": clients >= capacity,
        "is_default": bool(consultant.get("is_default")),
    }


async def directory() -> list[dict]:
    """
    Every active consultant with how full they are (default consultant first).

    Returns:
        list[dict]: `load(...)` for each active consultant.

    Example:
        >>> [(c["name"], c["clients"], c["capacity"]) for c in await directory()]
        [('Brandon', 1, 3), ('Kim', 3, 3)]
    """
    rows = [load(c) for c in await account_store.list_consultants()]
    return sorted(rows, key=lambda c: not c["is_default"])


async def directory_page(page: int = 1, page_size: int = 8, q: str = "") -> dict:
    """
    One page of the consultant directory (the "pick your consultant" panel).

    Consultants with room come first (default consultant first among them),
    then full ones; `q` filters by first name.

    Args:
        page: int, 1-based page number (out-of-range pages come back empty).
        page_size: int, consultants per page (1 to 50).
        q: str, part of a first name ("" = everyone).

    Returns:
        dict: {"consultants": [load(...), ...], "page", "page_size", "total", "pages"}.

    Example:
        >>> (await directory_page(1, 8))["total"]
        12
    """
    page_size = max(1, min(page_size, 50))
    everyone = [c for c in await directory() if q.strip().lower() in c["name"].lower()]
    everyone.sort(key=lambda c: (c["full"], not c["is_default"], c["name"].lower()))
    start = (max(page, 1) - 1) * page_size
    return {"consultants": everyone[start:start + page_size], "page": max(page, 1), "page_size": page_size,
            "total": len(everyone), "pages": max(1, -(-len(everyone) // page_size))}


async def default_consultant() -> dict | None:
    """
    Find the default consultant: the first ADMIN_EMAILS address's consultant row.

    The row exists only once that person has signed in at least once.

    Returns:
        dict | None: the consultants row, or None if ADMIN_EMAILS is empty or
            the first admin has not signed in yet.

    Example:
        >>> c = await default_consultant()
        >>> c["email"] if c else None
        'pat@example.com'
    """
    admins = get_settings().admin_email_list
    return await account_store.get_consultant_by_email(admins[0]) if admins else None


async def pick(consultant_id: str | None) -> str | None:
    """
    THE CAPACITY RULE: which consultant a new "do it for me" client gets.

      * a consultant chosen by name -> them, if active and not full (else 409)
      * nobody chosen -> the default consultant if they have room, else the
        active consultant with the most room, else None (the client waits and
        is assigned when the default consultant next has room)

    Args:
        consultant_id: str | None, the consultants row id the client picked.

    Returns:
        str | None: the consultants row id to assign, or None to wait.

    Raises:
        HTTPException: 404 for an unknown or inactive consultant; 409 when the
            chosen consultant is full.

    Example:
        >>> await pick(None)   # default consultant has room
        '<default consultant id>'
    """
    everyone = await directory()
    if consultant_id:
        chosen = next((c for c in everyone if c["id"] == consultant_id), None)
        if chosen is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That consultant isn't available.")
        if chosen["full"]:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail=f"{chosen['name']} is fully booked right now. Please pick someone else.")
        return chosen["id"]
    open_ = [c for c in everyone if not c["full"]]
    default = next((c for c in open_ if c["is_default"]), None)
    if default:
        return default["id"]
    open_.sort(key=lambda c: (c["clients"] / c["capacity"], c["name"]))
    return open_[0]["id"] if open_ else None


async def set_capacity(consultant: dict, capacity: int) -> dict:
    """
    A consultant sets how many clients they take at once.

    Lowering it below their current number of clients is allowed (nobody is
    dropped); they just can't be picked until they have room again.

    Args:
        consultant: dict, the consultant's own consultants row.
        capacity: int, 1 to MAX_CAPACITY.

    Returns:
        dict: `load(...)` with the new capacity.

    Raises:
        HTTPException: 400 outside 1..MAX_CAPACITY.

    Example:
        >>> (await set_capacity(kim, 5))["capacity"]
        5
    """
    if not 1 <= capacity <= MAX_CAPACITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"Capacity must be between 1 and {MAX_CAPACITY} clients.")
    row = await account_store.set_consultant_capacity(str(consultant["id"]), capacity) or consultant
    return load({**row, "client_count": await account_store.count_clients(str(consultant["id"]))})


def client_summary(acc: dict) -> dict:
    """
    Build what the consultant's "Working on" picker shows for one client.

    The label is the company name when there is one, else the person's full name.

    Args:
        acc: dict, the client's accounts row.

    Returns:
        dict: {"id", "label", "person", "email", "account_type", "company_name",
            "location", "naics_sector", "industry_other", "consultant_plan",
            "interested_services" (what they'd like help with — service ids, for the
            picker's "Help with" filter), "last_login_at" (ISO or None, for "recently active")}.

    Example:
        >>> client_summary({"id": 7, "first_name": "Ana", "last_name": "Diaz",
        ...                 "email": "ana@bakery.com", "account_type": "business",
        ...                 "company_name": "Sunrise Bakery"})["label"]
        'Sunrise Bakery'
    """
    name = f"{acc['first_name']} {acc['last_name']}"
    return {
        "id": str(acc["id"]),
        "label": acc.get("company_name") or name,
        "person": name,
        "email": acc["email"],
        "account_type": acc["account_type"],
        "company_name": acc.get("company_name"),
        "location": acc.get("location_label"),
        "naics_sector": acc.get("naics_sector"),
        "industry_other": acc.get("industry_other"),
        "consultant_plan": acc.get("consultant_plan"),   # "one_time" or "monthly"
        # Projects picked on "Your needs" ("Projects with a consultant"): the picker filters on them.
        "interested_services": list(acc.get("interested_services") or []),
        "last_login_at": acc["last_login_at"].isoformat() if acc.get("last_login_at") else None,
    }


async def acting_context(token: dict, acting_as: str | None) -> dict:
    """
    Decide who this request is for: {"account", "consultant", "acting", "admin"}.

      account    the account whose plan, team and billing apply
      consultant the caller's consultant row (None for clients)
      acting     True when a consultant is working on a client's account
      admin      True when the CALLER is an admin (every tool free, any client)

    THE ACCESS RULE: with no `acting_as` (or the caller's own id) the request is
    for the caller. Otherwise the caller must be a consultant AND the client's plan
    holder (the client, or their business's team owner) must be assigned to that
    consultant (accounts.consultant_id).

    Args:
        token: dict, the decoded sign-in token; "sub" is the caller's account id.
        acting_as: str | None, the client account id from the `X-Acting-As` header.

    Returns:
        dict: {"account": dict, "consultant": dict | None, "acting": bool, "admin": bool}.

    Raises:
        HTTPException: 401 if the caller's account no longer exists (ACCOUNT_GONE);
            403 if the caller is not a consultant or the client is not assigned to
            them; 404 if the client account does not exist.

    Example:
        >>> ctx = await acting_context({"sub": consultant_id}, client_id)
        >>> ctx["acting"], str(ctx["account"]["id"]) == client_id
        (True, True)
    """
    me = await account_store.get_account(token["sub"])
    if me is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    consultant = await active_consultant(str(me["id"]))   # a deactivated consultant is a client here
    admin = is_admin(me)
    if not acting_as or acting_as == str(me["id"]):
        return {"account": me, "consultant": consultant, "acting": False, "admin": admin}
    if consultant is None:
        paused = await account_store.get_consultant_by_account(str(me["id"])) is not None
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail=DEACTIVATED if paused else "Only consultants can work on another account.")
    from api.services import mfa   # (mfa imports account_store only)

    mfa.require_for_staff(token, me)   # opening a client's data needs the two-step sign-in
    client = await account_store.get_account(acting_as)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That client was not found.")
    if admin:
        return {"account": client, "consultant": consultant, "acting": True, "admin": True}
    # A business's consultant is set on its team owner; colleagues follow the owner.
    holder_id = await plan_holder_id(client)
    holder = client if holder_id == str(client["id"]) else await account_store.get_account(holder_id)
    if not holder or str(holder.get("consultant_id") or "") != str(consultant["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="That client isn't assigned to you.")
    return {"account": client, "consultant": consultant, "acting": True, "admin": False}


# ── Deactivate / reactivate (admins, Admin panel -> Consultants) ─────────────

DEACTIVATED = "Your consultant access is paused by an admin. You can still use your own account."


async def active_consultant(account_id: str) -> dict | None:
    """
    The account's consultant row if they are an ACTIVE consultant, else None.

    A deactivated consultant is treated as a normal client everywhere: no
    consultant workspace, no clients, can't act for anyone.

    Args:
        account_id: str, the account id.

    Returns:
        dict | None: the consultants row (active), or None.

    Example:
        >>> await active_consultant(kim_account_id) is None   # after deactivate(...)
        True
    """
    row = await account_store.get_consultant_by_account(account_id)
    return row if row and row.get("active", True) else None


def _iso(value) -> str | None:
    """A timestamp as ISO text (None stays None)."""
    return value.isoformat() if value else None


def team_row(consultant: dict) -> dict:
    """
    One consultant as the Admin panel lists them (with email: admins only).

    Args:
        consultant: dict, a consultants row with "client_count" (list_consultants(active_only=False)).

    Returns:
        dict: load(...) plus {"email", "active", "created_at", "updated_at",
        "deactivated_at", "deactivation_reason", "reactivated_at"} (ISO text).

    Example:
        >>> team_row(kim)["active"]
        True
    """
    return {**load(consultant), "email": consultant["email"], "active": bool(consultant.get("active", True)),
            "created_at": _iso(consultant.get("created_at")), "updated_at": _iso(consultant.get("updated_at")),
            "deactivated_at": _iso(consultant.get("deactivated_at")),
            "deactivation_reason": consultant.get("deactivation_reason"),
            "reactivated_at": _iso(consultant.get("reactivated_at"))}


async def team() -> list[dict]:
    """
    Every consultant, active or not (active first, then by name), for the Admin panel.

    Returns:
        list[dict]: team_row(...) for each consultant.
    """
    rows = [team_row(c) for c in await account_store.list_consultants(active_only=False)]
    return sorted(rows, key=lambda c: (not c["active"], not c["is_default"], c["name"].lower()))


async def deactivate(consultant_id: str, admin: dict, reason: str | None = None) -> dict:
    """
    Switch a consultant off and move their clients to someone with room.

    They keep their own account (and can sign in as a client), but can't be
    picked, can't open any client, and lose consultant mode. Each of their
    clients is reassigned by the CAPACITY RULE (pick: the default consultant if
    they have room, else whoever has the most room) or, if everyone is full,
    waits for a consultant (assigned when someone has room).

    Args:
        consultant_id: str, the consultants row id.
        admin: dict, the admin's accounts row.
        reason: str | None, an optional short note ("On leave until March").

    Returns:
        dict: {"moved": n clients reassigned, "waiting": n clients now waiting}.

    Raises:
        HTTPException: 404 unknown consultant; 400 for the default consultant or
            an admin (they can't be switched off); 409 already deactivated.

    Example:
        >>> await deactivate(kim_id, me, "Left the company")
        {'moved': 2, 'waiting': 0}
    """
    row = await account_store.get_consultant(consultant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That consultant wasn't found.")
    owner = await account_store.get_account(str(row["account_id"]))
    if row.get("is_default") or is_admin(owner):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Admins and the default consultant can't be deactivated.")
    if not row.get("active", True):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That consultant is already deactivated.")
    note = (reason or "").strip()[:300] or None
    await account_store.set_consultant_active(consultant_id, False, str(admin["id"]), note)
    moved = waiting = 0
    for client in await account_store.list_clients(consultant_id):
        new = await pick(None)   # they are inactive now, so pick() never gives them back
        await account_store.set_consultant_for(str(client["id"]), new, client.get("consultant_plan"))
        moved, waiting = (moved + 1, waiting) if new else (moved, waiting + 1)
    return {"moved": moved, "waiting": waiting}


async def reactivate(consultant_id: str, admin: dict) -> dict:
    """
    Switch a consultant back on. Clients waiting for a consultant are given to them (up to their room).

    Clients moved away when they were deactivated stay with their new consultant.

    Args:
        consultant_id: str, the consultants row id.
        admin: dict, the admin's accounts row.

    Returns:
        dict: {"assigned": n waiting clients given to them}.

    Raises:
        HTTPException: 404 unknown consultant; 409 already active.

    Example:
        >>> await reactivate(kim_id, me)
        {'assigned': 1}
    """
    row = await account_store.get_consultant(consultant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That consultant wasn't found.")
    if row.get("active", True):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That consultant is already active.")
    await account_store.set_consultant_active(consultant_id, True, str(admin["id"]))
    room = (row.get("capacity") or account_store.DEFAULT_CAPACITY) - await account_store.count_clients(consultant_id)
    return {"assigned": await account_store.assign_waiting_clients(consultant_id, room)}
