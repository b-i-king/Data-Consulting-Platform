"""
Consultants: who they are, which clients they manage, and who may act for whom.

WHAT THIS FILE DOES
-------------------
The platform serves two kinds of clients:
  * DO IT YOURSELF — they sign up and use the tools themselves.
  * DONE FOR YOU   — they tick "I'd like a consultant to do this for me" (or a
                     consultant creates their account), and a consultant signs
                     in and does the work in their workspace.

  is_consultant_email(email)   is this address in CONSULTANT_EMAILS?
  check_access_code(code)      the extra secret a consultant must enter
  default_consultant()         who new "done for you" clients are assigned to
  register(account)            create the consultant row on first sign-in, and
                               hand them any clients waiting for a consultant
  acting_context(token, id)    THE ACCESS RULE (see below)

THE ACCESS RULE ("row-level security" in the API)
-------------------------------------------------
Every signed-in request names who it's for:
  * no `X-Acting-As` header -> the caller's own account.
  * `X-Acting-As: <client account id>` -> allowed ONLY when the caller is a
    consultant AND that client (or their business's team owner) is assigned to
    them (accounts.consultant_id). Anyone else gets 403 — a client can never act
    as another client, and a consultant can never open someone else's client.
The database repeats the safe default (RLS on, no public access); see db/schema.sql.

⚠️ Sign-in is still by email only (see api/routers/accounts.py), so consultant
sign-in also needs CONSULTANT_ACCESS_CODE. Replace both with verified sign-in
(e.g. Supabase Auth) before real clients — docs/ROADMAP.md.
"""

from __future__ import annotations

import hmac

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store
from api.services.entitlements import plan_holder_id

CODE_REQUIRED = "Consultant sign-in: enter your consultant access code."
ACCOUNT_GONE = ("We couldn't find your account. If this is a test server without a database, it may "
                "have restarted and forgotten accounts — please create your account again.")


def is_consultant_email(email: str) -> bool:
    """
    Tell whether an email address is listed in CONSULTANT_EMAILS.

    The comparison ignores letter case (the settings list is already lower-cased).

    Args:
        email: str, the address someone is signing in or signing up with.

    Returns:
        bool: True if the address belongs to a consultant.

    Example:
        >>> is_consultant_email("Pat@Example.com")   # CONSULTANT_EMAILS="pat@example.com"
        True
    """
    return email.lower() in get_settings().consultant_email_list


def check_access_code(code: str | None) -> None:
    """
    Raise 403 unless consultant sign-in is switched on and the code matches.

    Consultant sign-in is "off" while CONSULTANT_ACCESS_CODE is empty. The code
    is compared with hmac.compare_digest, which takes the same time whether or not
    a guess is close, so response times do not leak the code.

    Args:
        code: str | None, the access code the person typed (None or "" if missing).

    Returns:
        None: returns quietly when the code is correct.

    Raises:
        HTTPException: 403 if CONSULTANT_ACCESS_CODE is not set, or the code is
            missing or wrong (detail CODE_REQUIRED).

    Example:
        >>> check_access_code("s3cret")   # CONSULTANT_ACCESS_CODE="s3cret" -> no error
        >>> check_access_code("guess")
        Traceback (most recent call last):
        HTTPException: 403 Consultant sign-in: enter your consultant access code.
    """
    expected = get_settings().consultant_access_code
    if not expected:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="Consultant sign-in isn't switched on yet (CONSULTANT_ACCESS_CODE is not set).")
    # compare_digest takes the same time whether or not the code is close, so
    # the response time doesn't give away how much of a guess was right.
    if not code or not hmac.compare_digest(code.encode(), expected.encode()):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=CODE_REQUIRED)


async def default_consultant() -> dict | None:
    """
    Find the default consultant: the first CONSULTANT_EMAILS address.

    New "done for you" clients are assigned to this person. The consultant row
    exists only once that person has signed in at least once (see register).

    Returns:
        dict | None: the consultants row, or None if CONSULTANT_EMAILS is empty or
            the first consultant has not signed in yet.

    Example:
        >>> c = await default_consultant()
        >>> c["email"] if c else None
        'pat@example.com'
    """
    emails = get_settings().consultant_email_list
    return await account_store.get_consultant_by_email(emails[0]) if emails else None


async def register(account: dict) -> dict:
    """
    Make sure a consultant's row exists (the "consultants" dimension table).

    The default consultant also picks up clients who asked for a consultant
    before anyone was set up to take them. A consultant is the default when their
    email is the first CONSULTANT_EMAILS address; the flag is refreshed on every
    call.

    Args:
        account: dict, the consultant's accounts row (needs "id", "first_name",
            "email").

    Returns:
        dict: the consultants row (with "id", "is_default", ...).

    Example:
        >>> row = await register(pat_account)   # Pat is first in CONSULTANT_EMAILS
        >>> row["is_default"]
        True
    """
    emails = get_settings().consultant_email_list
    consultant = await account_store.ensure_consultant(account, is_default=bool(emails) and account["email"] == emails[0])
    if consultant["is_default"]:
        await account_store.assign_waiting_clients(str(consultant["id"]))
    return consultant


def client_summary(acc: dict) -> dict:
    """
    Build what the consultant's "Working on" picker shows for one client.

    The label is the company name when there is one, else the person's full name.

    Args:
        acc: dict, the client's accounts row.

    Returns:
        dict: {"id", "label", "person", "email", "account_type", "company_name",
            "location", "naics_sector", "industry_other"}.

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
    }


async def acting_context(token: dict, acting_as: str | None) -> dict:
    """
    Decide who this request is for: {"account", "consultant", "acting"}.

      account    the account whose plan, team and billing apply
      consultant the caller's consultant row (None for clients)
      acting     True when a consultant is working on a client's account

    THE ACCESS RULE: with no `acting_as` (or the caller's own id) the request is
    for the caller. Otherwise the caller must be a consultant AND the client's plan
    holder (the client, or their business's team owner) must be assigned to that
    consultant (accounts.consultant_id).

    Args:
        token: dict, the decoded sign-in token; "sub" is the caller's account id.
        acting_as: str | None, the client account id from the `X-Acting-As` header.

    Returns:
        dict: {"account": dict, "consultant": dict | None, "acting": bool}.

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
    consultant = await account_store.get_consultant_by_account(str(me["id"]))
    if not acting_as or acting_as == str(me["id"]):
        return {"account": me, "consultant": consultant, "acting": False}
    if consultant is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only consultants can work on another account.")
    client = await account_store.get_account(acting_as)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That client was not found.")
    # A business's consultant is set on its team owner; colleagues follow the owner.
    holder_id = await plan_holder_id(client)
    holder = client if holder_id == str(client["id"]) else await account_store.get_account(holder_id)
    if not holder or str(holder.get("consultant_id") or "") != str(consultant["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="That client isn't assigned to you.")
    return {"account": client, "consultant": consultant, "acting": True}
