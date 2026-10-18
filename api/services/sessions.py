"""
Signed-in devices: at most 3 per person, signed out after 8 idle hours, and revocable.

WHAT THIS FILE DOES
-------------------
Every sign-in now creates (or reuses) a SESSION — one row in dim.sessions per
person per device — and the login token carries its id ("sid"). Each request
checks that session (check), so a token is only good while its session is:

  * at most SESSION_MAX_DEVICES (3) devices signed in at once per person.
    Signing in on a 4th signs out the one used longest ago, and the reply says
    which ("We signed you out on Chrome on Windows"). So one login can't be
    shared by a whole office to dodge the 5-person team limit.
  * signed out after SESSION_IDLE_HOURS (8) without use (healthcare clients
    expect an automatic log-off).
  * revocable: "Sign out" (this device), "Sign out everywhere else", a
    password reset (every device), or an admin (every device).

WHAT COUNTS AS A DEVICE
-----------------------
The website keeps the login per browser TAB, so a tab alone can't be a device.
Each browser gets a random device id (kept in localStorage, sent as the
X-Device-Id header at sign-in; not a secret). Signing in again from the same
browser reuses that device's session, so several tabs count once. A phone app
sends its own id. Only a short label is stored ("Chrome on Windows") — never
the full browser string or the IP address.

  start(account_id, device_id, user_agent) -> (sid, signed-out labels)
  check(token)                             -> 401 unless the session is live
  list_for(account_id, current_sid)        -> "Your devices"
  sign_out(account_id, sid)                -> one device
  sign_out_all(account_id, reason, keep)   -> every device (but `keep`)
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store

ACCOUNT_GONE = ("We couldn't find your account. If this is a test server without a database, it may "
                "have restarted and forgotten accounts — please create your account again.")
SIGNED_OUT = {
    "signed_out": "You signed out on this device. Please sign in again.",
    "device_limit": "You were signed out here because your account signed in on another device "
                    "(3 devices at most). Please sign in again.",
    "password_reset": "Your password was changed, so every device was signed out. Please sign in with the new password.",
    "everywhere": "You signed out on every other device. Please sign in again.",
    "admin": "An admin signed this account out. Please sign in again.",
    "idle": "You were signed out after a while without activity. Please sign in again.",
}
TOUCH_EVERY = timedelta(minutes=5)   # write last_seen_at at most this often (fewer database writes)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def max_devices() -> int:
    return max(1, get_settings().session_max_devices)


def idle_limit() -> timedelta:
    return timedelta(hours=max(1, get_settings().session_idle_hours))


def device_label(user_agent: str | None) -> str:
    """
    A short, readable device name from the browser's User-Agent ("Chrome on Windows").

    Only this label is stored, never the full User-Agent text.

    Args:
        user_agent: str | None, the User-Agent header.

    Returns:
        str: "<browser> on <system>", or "Unknown device".

    Example:
        >>> device_label("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36")
        'Chrome on Windows'
    """
    ua = user_agent or ""
    system = next((name for pattern, name in (
        (r"iPhone|iPad|iPod", "iPhone / iPad"), (r"Android", "Android"), (r"Windows", "Windows"),
        (r"Mac OS X|Macintosh", "Mac"), (r"CrOS", "Chromebook"), (r"Linux", "Linux"),
    ) if re.search(pattern, ua)), None)
    browser = next((name for pattern, name in (
        (r"Edg/", "Edge"), (r"OPR/|Opera", "Opera"), (r"Firefox/", "Firefox"), (r"Chrome/|CriOS", "Chrome"),
        (r"Safari/", "Safari"), (r"python|httpx|curl|testclient", "Script"),
    ) if re.search(pattern, ua, re.IGNORECASE)), None)
    if not system and not browser:
        return "Unknown device"
    return f"{browser or 'Browser'} on {system or 'another system'}"


def _live(row: dict, now: datetime) -> bool:
    """A session still counts: not signed out and used within the idle limit."""
    return row.get("revoked_at") is None and row["last_seen_at"] > now - idle_limit()


async def start(account_id: str, device_id: str | None, user_agent: str | None) -> tuple[str, list[str]]:
    """
    Sign a person in on a device: reuse that device's session, or open a new one (within the limit).

    Args:
        account_id: str, the account signing in.
        device_id: str | None, the browser's random device id (X-Device-Id); None = a new device each time.
        user_agent: str | None, for the "Chrome on Windows" label.

    Returns:
        tuple[str, list[str]]: the session id for the token, and the labels of
        devices signed out to make room (usually []).

    Example:
        >>> sid, bumped = await start(ana_id, "b7c1...", "Mozilla/5.0 (Macintosh) ... Safari/605.1")
        >>> bumped
        []
    """
    now = _now()
    device = (device_id or "").strip()[:64] or None
    label = device_label(user_agent)
    rows = await account_store.list_sessions(account_id)
    same = next((r for r in rows if device and r.get("device_id") == device and _live(r, now)), None)
    if same:
        await account_store.touch_session(str(same["id"]), label=label)
        return str(same["id"]), []
    live = sorted((r for r in rows if _live(r, now)), key=lambda r: r["last_seen_at"])
    bumped = []
    while len(live) >= max_devices():
        oldest = live.pop(0)
        await account_store.revoke_session(str(oldest["id"]), "device_limit")
        bumped.append(oldest["device"])
    sid = await account_store.create_session(account_id, device, label)
    return sid, bumped


async def check(token: dict) -> None:
    """
    Make sure the token's session is still live; refresh its "last used" time now and then.

    Args:
        token: dict, the decoded login token (needs "sub" and "sid").

    Returns:
        None.

    Raises:
        HTTPException: 401 with why (signed out, idle, device limit, password changed,
            account gone).

    Example:
        >>> await check({"sub": ana_id, "sid": sid})   # live: nothing happens
    """
    row = await account_store.get_session(token.get("sid") or "") if token.get("sid") else None
    if row is None or str(row["account_id"]) != str(token.get("sub")):
        gone = await account_store.get_account(str(token.get("sub"))) is None
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail=ACCOUNT_GONE if gone else "Please sign in again.")
    if row.get("revoked_at") is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail=SIGNED_OUT.get(row.get("revoked_reason") or "", SIGNED_OUT["signed_out"]))
    now = _now()
    if row["last_seen_at"] <= now - idle_limit():
        await account_store.revoke_session(str(row["id"]), "idle")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=SIGNED_OUT["idle"])
    if now - row["last_seen_at"] > TOUCH_EVERY:
        await account_store.touch_session(str(row["id"]))


async def list_for(account_id: str, current_sid: str | None) -> list[dict]:
    """
    "Your devices": every device signed in now, most recently used first.

    Args:
        account_id: str, the account.
        current_sid: str | None, the session of the device asking (marked "this_device").

    Returns:
        list[dict]: {"id", "device", "signed_in_at", "last_used_at", "this_device"} (ISO times).
    """
    now = _now()
    live = sorted((r for r in await account_store.list_sessions(account_id) if _live(r, now)),
                  key=lambda r: r["last_seen_at"], reverse=True)
    return [{"id": str(r["id"]), "device": r["device"], "signed_in_at": r["created_at"].isoformat(),
             "last_used_at": r["last_seen_at"].isoformat(), "this_device": str(r["id"]) == current_sid}
            for r in live]


async def sign_out(account_id: str, sid: str) -> bool:
    """
    Sign out one device of this account.

    Args:
        account_id: str, the account (a session of someone else is never touched).
        sid: str, the session id.

    Returns:
        bool: True if a live session was signed out.
    """
    row = await account_store.get_session(sid)
    if row is None or str(row["account_id"]) != account_id or row.get("revoked_at") is not None:
        return False
    await account_store.revoke_session(sid, "signed_out")
    return True


async def sign_out_all(account_id: str, reason: str, keep: str | None = None) -> int:
    """
    Sign out every device of an account (except `keep`): "everywhere", "password_reset" or "admin".

    Args:
        account_id: str, the account.
        reason: str, a SIGNED_OUT key (the message those devices will see).
        keep: str | None, a session id to leave signed in (the device asking).

    Returns:
        int: how many devices were signed out.
    """
    count = 0
    for r in await account_store.list_sessions(account_id):
        if r.get("revoked_at") is None and str(r["id"]) != (keep or ""):
            await account_store.revoke_session(str(r["id"]), reason)
            count += 1
    return count

