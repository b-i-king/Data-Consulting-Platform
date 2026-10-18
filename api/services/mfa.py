"""
Two-step sign-in: a 6-digit code from an authenticator app, after the password.

WHAT THIS FILE DOES
-------------------
Admins and consultants can open many clients' data, so a stolen password alone
must not be enough. They add an authenticator app (Google Authenticator,
Microsoft Authenticator, 1Password, Authy...) once:

  1. setup()    a random key, saved ENCRYPTED (api/core/crypto.py), not on yet.
                The person types the key into their app (or opens the
                otpauth:// link on their phone).
  2. enable()   they type the app's current code: it's on. They get 10 one-time
                BACKUP CODES (only one-way hashes are kept) for a lost phone.
  3. Sign-in    password -> "enter your code" (a 5-minute ticket) -> the code or
                a backup code -> signed in. Wrong codes count toward the same
                3-strike lockout as wrong passwords.

The codes are standard TOTP (RFC 6238): HMAC-SHA1 of the 30-second time step,
6 digits; one step either side is accepted for a phone clock that's a little off.

REQUIRED FOR STAFF (MFA_REQUIRED_FOR_STAFF, on by default)
----------------------------------------------------------
Until an admin or consultant turns it on, their OWN workspace works (so they
can set it up), but the Admin panel, the consultant pages and every client's
workspace answer 403 "Set up two-step sign-in first". Once on, a sign-in
without the code can't reach them either. Anyone else may turn it on too.
Lost phone AND backup codes: an admin resets it (Admin -> Testers list).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import jwt
from fastapi import HTTPException, status

from api.core import crypto
from api.core.config import get_settings
from api.services import account_store

ISSUER = "Data Consulting Platform"
STEP_SECONDS = 30
DIGITS = 6
TICKET_MINUTES = 5
RECOVERY_CODES = 10

SETUP_FIRST = ("Admins and consultants need two-step sign-in. Set it up in Settings -> Account -> "
               "Two-step sign-in, then sign in again.")
WRONG_CODE = "That code didn't match."


# ── TOTP (the 6-digit codes) ────────────────────────────────────────────────

def new_secret() -> str:
    """A random 160-bit key in base32 (what authenticator apps expect), e.g. 'JBSWY3DPEHPK3PXP...'."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def code_at(secret: str, step: int) -> str:
    """
    The 6-digit code for one 30-second time step (RFC 6238 / RFC 4226).

    Args:
        secret: str, the base32 key.
        step: int, seconds since 1970 divided by 30.

    Returns:
        str: six digits, e.g. "492039".
    """
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    number = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(number % 10**DIGITS).zfill(DIGITS)


def verify_code(secret: str, code: str, now: float | None = None) -> bool:
    """
    Check a typed code against the current step, and one step either side.

    Args:
        secret: str, the base32 key.
        code: str, what the person typed (spaces ignored).
        now: float | None, seconds since 1970 (tests).

    Returns:
        bool: True when it matches.
    """
    code = "".join(ch for ch in str(code) if ch.isdigit())
    if len(code) != DIGITS:
        return False
    step = int((now if now is not None else time.time()) // STEP_SECONDS)
    return any(hmac.compare_digest(code_at(secret, step + d), code) for d in (-1, 0, 1))


def otpauth_url(secret: str, email: str) -> str:
    """The otpauth:// link authenticator apps open (also what a QR code would hold)."""
    label = quote(f"{ISSUER}:{email}")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(ISSUER)}&digits={DIGITS}&period={STEP_SECONDS}"


def _hash_code(code: str) -> str:
    return hashlib.sha256("".join(code.split()).lower().encode()).hexdigest()


# ── Who needs it ────────────────────────────────────────────────────────────

def is_on(account: dict) -> bool:
    """Two-step sign-in is switched on for this account."""
    return bool(account.get("mfa_enabled_at") and account.get("mfa_secret"))


def required(account: dict, is_staff: bool) -> bool:
    """Two-step sign-in is required here: staff (admin or consultant) while MFA_REQUIRED_FOR_STAFF is on."""
    return is_staff and get_settings().mfa_required_for_staff


def require_for_staff(token: dict, account: dict) -> None:
    """
    Stop a staff request that wasn't signed in with two-step sign-in (call before staff-only work).

    Args:
        token: dict, the decoded login token ("mfa": True when the code was given).
        account: dict, the caller's accounts row.

    Raises:
        HTTPException: 403 SETUP_FIRST.
    """
    if get_settings().mfa_required_for_staff and not token.get("mfa"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=SETUP_FIRST)


# ── Setting it up ───────────────────────────────────────────────────────────

async def setup(account: dict) -> dict:
    """
    Start setting up: a new key, saved encrypted (not on until enable()).

    Args:
        account: dict, the accounts row.

    Returns:
        dict: {"secret": base32 key to type into the app, "otpauth_url": link for a phone}.

    Raises:
        HTTPException: 409 when it's already on (turn it off first).
    """
    if is_on(account):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Two-step sign-in is already on.")
    secret = new_secret()
    await account_store.set_mfa(str(account["id"]), secret=crypto.encrypt(secret, "mfa"), enabled=False,
                                recovery_hashes=[])
    return {"secret": secret, "otpauth_url": otpauth_url(secret, account["email"])}


async def enable(account: dict, code: str) -> list[str]:
    """
    Finish setting up with the app's current code; returns the 10 backup codes (shown ONCE).

    Args:
        account: dict, the accounts row (after setup()).
        code: str, the 6-digit code.

    Returns:
        list[str]: backup codes like "7f3k-9q2m" (only their hashes are stored).

    Raises:
        HTTPException: 400 before setup(); 400 WRONG_CODE.
    """
    if not account.get("mfa_secret"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Start the setup first.")
    if not verify_code(crypto.decrypt(account["mfa_secret"], "mfa"), code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{WRONG_CODE} Check the time on your phone and try again.")
    codes = [f"{secrets.token_hex(2)}-{secrets.token_hex(2)}" for _ in range(RECOVERY_CODES)]
    await account_store.set_mfa(str(account["id"]), secret=account["mfa_secret"], enabled=True,
                                recovery_hashes=[_hash_code(c) for c in codes])
    return codes


async def disable(account: dict, code: str, is_staff: bool) -> None:
    """
    Turn it off (with a current code). Not for staff while it's required.

    Raises:
        HTTPException: 400 when required for them, or the code is wrong.
    """
    if required(account, is_staff):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Two-step sign-in is required for admins and consultants.")
    if not await check(account, code):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=WRONG_CODE)
    await account_store.set_mfa(str(account["id"]), secret=None, enabled=False, recovery_hashes=[])


async def reset(account_id: str) -> None:
    """An admin clears someone's two-step sign-in (lost phone and backup codes); they set it up again."""
    await account_store.set_mfa(account_id, secret=None, enabled=False, recovery_hashes=[])


async def check(account: dict, code: str) -> bool:
    """
    A sign-in code: the app's current code, or an unused backup code (which is then used up).

    Args:
        account: dict, the accounts row (two-step sign-in on).
        code: str, what was typed.

    Returns:
        bool: True when it's right.
    """
    if not is_on(account):
        return False
    if verify_code(crypto.decrypt(account["mfa_secret"], "mfa"), code):
        return True
    return "-" in code and await account_store.use_recovery_code(str(account["id"]), _hash_code(code))


# ── The sign-in ticket (between the password and the code) ──────────────────

def ticket(account_id: str) -> str:
    """A 5-minute signed ticket that says "the password was right; now the code"."""
    s = get_settings()
    now = datetime.now(timezone.utc)
    return jwt.encode({"sub": account_id, "purpose": "mfa", "iat": now, "exp": now + timedelta(minutes=TICKET_MINUTES)},
                      s.jwt_secret_key, algorithm=s.jwt_algorithm)


def read_ticket(value: str) -> str:
    """
    The account id inside a valid ticket.

    Raises:
        HTTPException: 401 when it's expired, altered, or not a two-step ticket.
    """
    s = get_settings()
    try:
        data = jwt.decode(value, s.jwt_secret_key, algorithms=[s.jwt_algorithm])
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="That took too long. Please sign in again.") from None
    if data.get("purpose") != "mfa":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Please sign in again.")
    return str(data["sub"])
