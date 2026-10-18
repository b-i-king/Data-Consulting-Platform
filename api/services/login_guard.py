"""
Sign-in lockout: a few wrong passwords, then a short lock (or reset to get back in).

WHAT THIS FILE DOES
-------------------
Stops someone guessing a password over and over:

  * Each wrong password counts (dim.accounts.failed_logins). The reply says how
    many tries are left: "Wrong email or password. 2 attempts left before
    sign-in is locked for 15 minutes."
  * After LOGIN_MAX_ATTEMPTS wrong passwords in a row (default 3), sign-in is
    LOCKED for LOGIN_LOCK_MINUTES (default 15; dim.accounts.login_locked_until).
    While locked the password isn't even checked, so guessing is pointless.
  * Two ways back in: wait for the lock to end (then the count starts over), or
    RESET THE PASSWORD ("Forgot password?") — a reset unlocks at once.
  * A successful sign-in clears the count (account_store.touch_last_login).

WHY A TIMED LOCK, NOT "LOCKED UNTIL YOU RESET"
----------------------------------------------
Anyone can type someone else's email. If three wrong tries locked an account
until a reset, a stranger could lock a client out on purpose, again and again.
A 15-minute lock stops guessing (at most 12 tries an hour) without that
problem, and the reset link still gets the real owner in right away.

UNKNOWN EMAILS
--------------
An email with no account gets the same messages and the same lock (counted in
memory here, never stored), so the replies don't show which emails have
accounts.

  ensure_unlocked(account, email)   before checking the password: 429 if locked
  failed(account, email)            after a wrong password: count it, then raise
                                    401 (tries left) or 429 (now locked)
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store

# Unknown emails (memory only, never stored): email -> {"count": int, "locked_until": datetime | None}.
_unknown: dict[str, dict] = {}
_UNKNOWN_MAX = 10_000   # forget them all past this many, so memory can't grow without end


def _now() -> datetime:
    """The current UTC time."""
    return datetime.now(timezone.utc)


def _state(account: dict | None, email: str) -> dict:
    """The current count and lock for this account (or unknown email)."""
    if account is not None:
        return {"count": account.get("failed_logins") or 0, "locked_until": account.get("login_locked_until")}
    return _unknown.get(email.lower(), {"count": 0, "locked_until": None})


def locked_message(locked_until: datetime) -> str:
    """
    The reply while sign-in is locked, with the minutes left.

    Args:
        locked_until: datetime, when the lock ends (UTC).

    Returns:
        str: e.g. "Too many wrong passwords, so sign-in is locked for 14 more minutes. ..."

    Example:
        >>> locked_message(datetime.now(timezone.utc) + timedelta(minutes=1))
        'Too many wrong passwords, so sign-in is locked for 1 more minute. Reset your password to get back in right away.'
    """
    minutes = max(1, math.ceil((locked_until - _now()).total_seconds() / 60))
    return (f"Too many wrong passwords, so sign-in is locked for {minutes} more minute{'s' if minutes != 1 else ''}. "
            "Reset your password to get back in right away.")


async def ensure_unlocked(account: dict | None, email: str) -> None:
    """
    Refuse to check a password while sign-in is locked; start the count over once a lock has ended.

    Args:
        account: dict | None, the accounts row for that email (None = no account).
        email: str, the email typed.

    Returns:
        None (sign-in may go ahead).

    Raises:
        HTTPException: 429 with locked_message while the lock lasts.

    Example:
        >>> await ensure_unlocked(ana, "ana@example.com")   # not locked: nothing happens
    """
    locked_until = _state(account, email)["locked_until"]
    if locked_until is None:
        return
    if locked_until > _now():
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=locked_message(locked_until))
    # The lock is over: a fresh set of tries.
    if account is not None:
        await account_store.clear_failed_logins(str(account["id"]))
        account.update(failed_logins=0, login_locked_until=None)
    else:
        _unknown.pop(email.lower(), None)


async def failed(account: dict | None, email: str, wrong: HTTPException) -> None:
    """
    Count a wrong password, then raise the reply: tries left (401) or locked (429).

    Args:
        account: dict | None, the accounts row (None = no account with that email).
        email: str, the email typed.
        wrong: HTTPException, the 401 from the password check (its message starts the reply).

    Returns:
        None: always raises.

    Raises:
        HTTPException: 401 "Wrong email or password. 2 attempts left before sign-in is
            locked for 15 minutes." — or 429 locked_message on the last try.

    Example:
        >>> await failed(ana, "ana@example.com", HTTPException(401, "Wrong email or password."))
        HTTPException(401, 'Wrong email or password. 2 attempts left ...')
    """
    s = get_settings()
    limit, minutes = max(1, s.login_max_attempts), max(1, s.login_lock_minutes)
    if account is not None:
        row = await account_store.record_failed_login(str(account["id"]), limit, minutes)
        count, locked_until = row["failed_logins"], row["login_locked_until"]
    else:
        if len(_unknown) > _UNKNOWN_MAX:
            _unknown.clear()
        entry = _unknown.setdefault(email.lower(), {"count": 0, "locked_until": None})
        entry["count"] += 1
        if entry["count"] >= limit:
            entry["locked_until"] = _now() + timedelta(minutes=minutes)
        count, locked_until = entry["count"], entry["locked_until"]
    if locked_until is not None:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail=locked_message(locked_until))
    left = limit - count
    detail = (f"{wrong.detail} {left} attempt{'s' if left != 1 else ''} left before sign-in is locked "
              f"for {minutes} minutes.")
    if left == 1:
        detail += " Not sure? Reset your password instead."
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail)
