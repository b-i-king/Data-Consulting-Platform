"""
Passwords: register and check them (email + password sign-in).

WHAT THIS FILE DOES
-------------------
Sign-in used to be "type your email" only. Now every account has a password,
checked in one of two ways:

  * SUPABASE mode (SUPABASE_URL and SUPABASE_KEY set) — the recommended way.
    Supabase Auth stores the password (we never see it again after sending it)
    and can email a "confirm your address" link. This follows Supabase's
    Python guide: https://supabase.com/docs/guides/auth/passwords
        supabase.auth.sign_up({"email": ..., "password": ...})
        supabase.auth.sign_in_with_password({"email": ..., "password": ...})
    The account row keeps the Supabase user id (accounts.auth_user_id).

  * LOCAL mode (Supabase not configured) — for development and tests. The
    password is turned into a one-way scrypt hash (Python's built-in hashlib)
    and kept in accounts.password_hash. A hash can be checked but never turned
    back into the password.

After a password checks out, the API still issues its OWN login token
(api/core/security.py), exactly as before, so nothing else in the app changes.

FORGOT / RESET PASSWORD
-----------------------
  1. "Forgot password?" (sign-in form) or "Reset password" (Account page)
     -> send_reset_link(email). The reply is the SAME whether or not the email
     has an account, so nobody can use it to find out who signed up.
       * Supabase account: Supabase emails its own reset link
         (auth.reset_password_for_email), back to SITE_URL/reset-password.
         Add that address to Supabase -> Authentication -> URL Configuration
         -> Redirect URLs.
       * Local account (no Supabase): a random token; only its sha256 is
         stored (accounts.password_reset_hash, 1 hour). There is no email
         server in local mode, so the link is written to the API's log.
  2. /reset-password -> reset_password(token, new password). A reset also
     lifts a sign-in lock (3 wrong passwords; api/services/login_guard.py):
       * Supabase's link carries an access token (a JWT, it has dots): the new
         password is sent to Supabase's /auth/v1/user with that token.
       * A local token: matched by its hash, then replaced by the new hash.

WHY NOT GOOGLE / APPLE SIGN-IN YET?
-----------------------------------
Email + password keeps sign-in in-house to start. Supabase Auth supports Google
and Apple later with no change to the accounts table: they are just other ways
to get the same Supabase user id.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import httpx
from fastapi import HTTPException, status

from api.core.config import get_settings

log = logging.getLogger(__name__)

MIN_PASSWORD_LENGTH = 8
MAX_PASSWORD_LENGTH = 128

# scrypt settings (local mode). N=2**14, r=8, p=1 is the commonly recommended
# interactive-login setting: about 16 MB of memory and ~50 ms per check.
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1

WRONG_PASSWORD = "Wrong email or password."
RESET_SENT = ("If that email has an account, we've sent it a link to reset the password. "
              "The link works for 1 hour — check your spam folder too.")
RESET_BAD_LINK = "That reset link has expired or was already used. Ask for a new one."
RESET_HOURS = 1
LOCAL_PREFIX = "lr_"   # local reset tokens start with this (Supabase's are JWTs)


@dataclass
class Credentials:
    """What to save on the account after a password is registered."""

    auth_user_id: str | None = None      # Supabase user id (Supabase mode)
    password_hash: str | None = None     # scrypt hash (local mode)
    needs_confirmation: bool = False     # Supabase sent a "confirm your email" link


def mode() -> str:
    """
    Which password checker is on: "supabase" or "local".

    Returns:
        str: "supabase" when SUPABASE_URL and SUPABASE_KEY are both set, else "local".

    Example:
        >>> mode()   # with no Supabase settings
        'local'
    """
    s = get_settings()
    return "supabase" if (s.supabase_url and s.supabase_key) else "local"


def has_password(account: dict) -> bool:
    """
    Tell whether an account has a password yet.

    Accounts created FOR someone (a colleague added to a team, or a client a
    consultant added) start without one; they choose it on their first sign-in.

    Args:
        account: dict, an accounts row.

    Returns:
        bool: True when a Supabase user id or a local hash is stored.

    Example:
        >>> has_password({"auth_user_id": None, "password_hash": None})
        False
    """
    return bool(account.get("auth_user_id") or account.get("password_hash"))


def check_password_rules(password: str | None) -> str:
    """
    Make sure a new password is long enough (and not absurdly long).

    Args:
        password: str | None, the password typed.

    Returns:
        str: the password, unchanged.

    Raises:
        HTTPException: 422 with a message safe to show the person.

    Example:
        >>> check_password_rules("correct horse")
        'correct horse'
    """
    if not password or len(password) < MIN_PASSWORD_LENGTH:
        raise HTTPException(422,
                            detail=f"Choose a password of at least {MIN_PASSWORD_LENGTH} characters.")
    if len(password) > MAX_PASSWORD_LENGTH:
        raise HTTPException(422,
                            detail=f"Passwords can be at most {MAX_PASSWORD_LENGTH} characters.")
    return password


# ── Local mode: scrypt hashes ────────────────────────────────────────────────

def hash_password(password: str) -> str:
    """
    Turn a password into a salted scrypt hash (local mode).

    The text stored looks like "scrypt$16384$8$1$<salt>$<hash>", so the
    settings travel with the hash and can be raised later without breaking
    old passwords. A new random 16-byte salt is used every time, so two people
    with the same password get different hashes.

    Args:
        password: str, the plain password.

    Returns:
        str: the hash to store in accounts.password_hash.

    Example:
        >>> hash_password("correct horse").startswith("scrypt$")
        True
    """
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    b64 = lambda b: base64.b64encode(b).decode()   # noqa: E731 — tiny helper
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${b64(salt)}${b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """
    Check a password against a stored scrypt hash (local mode).

    Args:
        password: str, the password typed at sign-in.
        stored: str, the accounts.password_hash value.

    Returns:
        bool: True when it matches. A malformed stored value gives False.

    Example:
        >>> verify_password("correct horse", hash_password("correct horse"))
        True
    """
    try:
        _, n, r, p, salt, digest = stored.split("$")
        expected = base64.b64decode(digest)
        got = hashlib.scrypt(password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p))
    except (ValueError, TypeError):
        return False
    # compare_digest takes the same time however close the guess is.
    return hmac.compare_digest(got, expected)


# ── Supabase mode ────────────────────────────────────────────────────────────

def _supabase():
    """
    Create a fresh Supabase client for ONE sign-up or sign-in.

    The Python client remembers the last signed-in user. On a server that
    handles many people, sharing one client could mix them up, so each call
    gets its own client that keeps nothing (persist_session=False).

    Returns:
        supabase.Client: a client using SUPABASE_URL and SUPABASE_KEY.
    """
    from supabase import ClientOptions, create_client   # imported here: only needed in Supabase mode

    s = get_settings()
    return create_client(s.supabase_url, s.supabase_key,
                         options=ClientOptions(persist_session=False, auto_refresh_token=False))


def _supabase_error(exc: Exception) -> HTTPException:
    """
    Turn a Supabase Auth error into a friendly HTTP error.

    Supabase errors carry a `code` (for example "invalid_credentials" or
    "email_not_confirmed"); those we know get a plain-English message.

    Args:
        exc: Exception, the error raised by the Supabase client.

    Returns:
        HTTPException: the error to raise to the browser.
    """
    code = getattr(exc, "code", None) or ""
    if code == "invalid_credentials":
        return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=WRONG_PASSWORD)
    if code == "email_not_confirmed":
        return HTTPException(status.HTTP_403_FORBIDDEN,
                             detail="Please confirm your email first: open the link we emailed you, then sign in.")
    if code in ("weak_password",):
        return HTTPException(422,
                             detail="That password is too easy to guess. Try a longer one.")
    if code in ("user_already_exists", "email_exists"):
        return HTTPException(status.HTTP_409_CONFLICT,
                             detail="That email already has a sign-in. Please sign in instead.")
    if code.startswith("over_") or "rate" in code:
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                             detail="Too many attempts. Please wait a minute and try again.")
    return HTTPException(status.HTTP_502_BAD_GATEWAY,
                         detail=f"Sign-in service problem: {getattr(exc, 'message', None) or exc}")


async def register_password(email: str, password: str) -> Credentials:
    """
    Register a password for an email (sign-up, or a first sign-in that sets it).

    Supabase mode: calls supabase.auth.sign_up. If Supabase's "Confirm email"
    setting is on, no session comes back and `needs_confirmation` is True —
    the person must click the emailed link before signing in.
    Local mode: hashes the password.

    Args:
        email: str, the account's email (lowercase).
        password: str, the new password (rules already checked).

    Returns:
        Credentials: what to save on the account.

    Raises:
        HTTPException: friendly errors from Supabase (409, 422, 429, 502).

    Example:
        >>> (await register_password("ana@example.com", "correct horse")).password_hash[:7]
        'scrypt$'
    """
    if mode() == "local":
        return Credentials(password_hash=hash_password(password))

    def call():
        return _supabase().auth.sign_up({"email": email, "password": password})

    try:
        # The Supabase client is synchronous; run it in a worker thread so the
        # server keeps answering other requests meanwhile.
        res = await asyncio.to_thread(call)
    except Exception as exc:   # noqa: BLE001 — every Supabase error becomes a friendly message
        raise _supabase_error(exc)
    if res.user is None:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Sign-in service did not create the user.")
    return Credentials(auth_user_id=str(res.user.id), needs_confirmation=res.session is None)


async def check_password(account: dict, password: str) -> None:
    """
    Check the password for an account that already has one; raise if wrong.

    A local hash, when present, is checked locally (whatever the mode), so
    accounts made before Supabase was switched on keep working. Otherwise
    Supabase checks it with sign_in_with_password.

    Args:
        account: dict, the accounts row (has auth_user_id or password_hash).
        password: str, the password typed.

    Returns:
        None: returns quietly when the password is right.

    Raises:
        HTTPException: 401 "Wrong email or password."; 403 when Supabase is
            waiting for the email to be confirmed; 502 for service problems.

    Example:
        >>> await check_password(account, "correct horse")   # no error = signed in
    """
    if account.get("password_hash"):
        if not verify_password(password, account["password_hash"]):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=WRONG_PASSWORD)
        return
    if mode() == "local":
        # A Supabase account while Supabase is switched off: we can't check it.
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Sign-in is set up for Supabase, but SUPABASE_URL / SUPABASE_KEY are not set.")

    def call():
        return _supabase().auth.sign_in_with_password({"email": account["email"], "password": password})

    try:
        res = await asyncio.to_thread(call)
    except Exception as exc:   # noqa: BLE001
        raise _supabase_error(exc)
    # Belt and braces: the Supabase user must be the one linked to this account.
    if res.user is None or str(res.user.id) != str(account["auth_user_id"]):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=WRONG_PASSWORD)


# ── Forgot / reset password ─────────────────────────────────────────────────

def _reset_url() -> str:
    """Where reset links send people: SITE_URL/reset-password."""
    return get_settings().site_url.rstrip("/") + "/reset-password"


async def send_reset_link(account: dict | None, email: str) -> None:
    """
    Send a password-reset link (see FORGOT / RESET PASSWORD above).

    Quietly does nothing for an unknown email, so the caller can always answer
    RESET_SENT. A Supabase account gets Supabase's email; a local account gets a
    one-hour token whose link is written to the log (no email server locally).

    Args:
        account: dict | None, the accounts row for that email (None = unknown).
        email: str, the address typed (lowercase).

    Returns:
        None.

    Raises:
        HTTPException: 429 when Supabase says too many emails were asked for.

    Example:
        >>> await send_reset_link(account, "ana@example.com")
    """
    if account is None:
        return
    if account.get("auth_user_id") and mode() == "supabase":
        def call():
            return _supabase().auth.reset_password_for_email(email, {"redirect_to": _reset_url()})

        try:
            await asyncio.to_thread(call)
        except Exception as exc:   # noqa: BLE001
            err = _supabase_error(exc)
            if err.status_code == status.HTTP_429_TOO_MANY_REQUESTS:
                raise err
            log.warning("Supabase reset email failed for an account: %s", exc)
        return
    from api.services import account_store   # imported here: account_store doesn't need auth

    token = LOCAL_PREFIX + secrets.token_urlsafe(32)
    await account_store.set_password_reset(str(account["id"]), hashlib.sha256(token.encode()).hexdigest(),
                                           datetime.now(timezone.utc) + timedelta(hours=RESET_HOURS))
    # Local mode has no email server: the link goes to the API's log for the developer.
    log.warning("Password reset link (local mode — no email is sent): %s?token=%s", _reset_url(), token)


async def reset_password(token: str, new_password: str) -> dict:
    """
    Set a new password from a reset link.

    Args:
        token: str, Supabase's recovery access token (from the link's "#access_token=")
            or a local token ("lr_...", from "?token=").
        new_password: str, the new password (rules checked here).

    Returns:
        dict: the accounts row whose password changed.

    Raises:
        HTTPException: 422 for a password that breaks the rules; 400 RESET_BAD_LINK
            for an expired, used or unknown link; 502 when Supabase can't be reached.

    Example:
        >>> (await reset_password("lr_abc...", "a new long password"))["email"]
        'ana@example.com'
    """
    from api.services import account_store

    password = check_password_rules(new_password)
    if token.startswith(LOCAL_PREFIX):
        account = await account_store.get_account_by_reset_hash(hashlib.sha256(token.encode()).hexdigest())
        if account is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=RESET_BAD_LINK)
        await account_store.set_credentials(str(account["id"]), auth_user_id=None, password_hash=hash_password(password))
        await account_store.set_password_reset(str(account["id"]), None, None)
        await account_store.clear_failed_logins(str(account["id"]))   # a reset unlocks sign-in at once
        return account
    if mode() != "supabase" or token.count(".") != 2:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=RESET_BAD_LINK)
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            res = await client.put(f"{s.supabase_url.rstrip('/')}/auth/v1/user", json={"password": password},
                                   headers={"apikey": s.supabase_key, "Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Sign-in service problem: {exc}")
    if res.status_code in (401, 403):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=RESET_BAD_LINK)
    if res.status_code == 422:
        raise HTTPException(422, detail="That password is too easy to guess. Try a longer one.")
    if res.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Sign-in service problem: the password wasn't changed.")
    user_id = str(res.json().get("id") or "")
    account = await account_store.get_account_by_auth_user(user_id) if user_id else None
    if account is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=RESET_BAD_LINK)
    await account_store.clear_failed_logins(str(account["id"]))   # a reset unlocks sign-in at once
    return account
