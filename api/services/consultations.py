"""
Consultations: the FREE first call, then PAID sessions — and how the app knows.

THE PROBLEM
-----------
Booking happens on Cal.com, outside the app. On its own the app can't tell
whether a client booked, rescheduled, canceled, or actually met you, so it
can't know when to stop offering the free 30-minute call and start sending
people to the paid ($80/hour) link.

THE ANSWER: two ways in, one rule
---------------------------------
1. AUTOMATIC (Cal.com webhook). Cal.com calls POST /api/consultations/calcom-webhook
   whenever a booking is created, rescheduled, canceled, or a meeting ends.
   Each call is signed with CALCOM_WEBHOOK_SECRET so nobody can fake one.
   The app keeps one row per booking in the `consultations` table.
2. MANUAL (you, the consultant). In the workspace, working on a client, you
   press "Confirm it happened" (or "No-show" / "Canceled"). You can also add a
   consultation by hand if it was booked before the webhook was set up.

THE RULE: the free consultation is USED UP only when a consultant marks a
free consultation "completed". A webhook alone never uses it up — Cal.com
can say a meeting ENDED (the calendar slot passed), but only you know it
really happened. That keeps the final say with you.

After that, every booking link the client sees is PAID_BOOKING_URL (a second
Cal.com event type you create for $80/hour sessions; Cal.com can take the
payment itself through its Stripe app, or clients prepay hours here).

LINKING A BOOKING TO AN ACCOUNT
-------------------------------
The app adds the client's details to the booking link it shows them:
    ?name=Ana%20Lee&email=ana@example.com&metadata[account_id]=<id>&metadata[kind]=free
Cal.com fills the form from name/email and sends `metadata` back in the
webhook, so the booking is matched to the right account. Bookings made
without the app's link (no metadata) are matched by the attendee's email.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import urlencode

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store
from api.services.entitlements import plan_holder

# What each Cal.com webhook trigger means for our row.
BOOKED_TRIGGERS = {"BOOKING_CREATED", "BOOKING_REQUESTED", "BOOKING_PAID"}
CANCELED_TRIGGERS = {"BOOKING_CANCELLED", "BOOKING_REJECTED"}
RESCHEDULED = "BOOKING_RESCHEDULED"
ENDED = "MEETING_ENDED"
NO_SHOW = "BOOKING_NO_SHOW_UPDATED"


def booking_link(base_url: str, holder: dict | None, kind: str) -> str:
    """
    Add the client's name, email and account id to a Cal.com booking link.

    Cal.com pre-fills the booking form from `name` and `email`, and returns
    `metadata[...]` values in its webhook — that's how a booking finds its
    account. Without a holder (a guest) the plain link is returned.

    Args:
        base_url: str, BOOKING_URL or PAID_BOOKING_URL.
        holder: dict | None, the plan holder's accounts row.
        kind: str, "free" or "paid".

    Returns:
        str: the link to show.

    Example:
        >>> booking_link("https://cal.com/me/30min", {"id": "a1", "first_name": "Ana",
        ...              "last_name": "Lee", "email": "ana@example.com"}, "free")
        'https://cal.com/me/30min?name=Ana+Lee&email=ana%40example.com&metadata%5Baccount_id%5D=a1&metadata%5Bkind%5D=free'
    """
    if not holder or not base_url:
        return base_url
    query = urlencode({
        "name": f"{holder['first_name']} {holder['last_name']}",
        "email": holder["email"],
        "metadata[account_id]": str(holder["id"]),
        "metadata[kind]": kind,
    })
    return f"{base_url}{'&' if '?' in base_url else '?'}{query}"


def _iso(value) -> str | None:
    """Format a datetime as ISO 8601 text for the browser (None stays None)."""
    return value.isoformat() if isinstance(value, datetime) else value


def _out(row: dict) -> dict:
    """
    One consultation as sent to the browser.

    Args:
        row: dict, a consultations row.

    Returns:
        dict: {"id", "kind", "status", "source", "starts_at", "ends_at",
        "confirmed_at", "note", "attendee_email"} with times as ISO text.
    """
    return {
        "id": str(row["id"]), "kind": row["kind"], "status": row["status"], "source": row["source"],
        "starts_at": _iso(row.get("starts_at")), "ends_at": _iso(row.get("ends_at")),
        "confirmed_at": _iso(row.get("confirmed_at")), "note": row.get("note"),
        "attendee_email": row.get("attendee_email"),
    }


def free_used(rows: list[dict]) -> bool:
    """
    THE RULE: is the free consultation used up?

    Only a FREE consultation a consultant marked "completed" counts. Booked,
    ended (waiting for confirmation), canceled and no-show do not.

    Args:
        rows: list[dict], the client's consultations rows.

    Returns:
        bool: True once a free consultation is completed.

    Example:
        >>> free_used([{"kind": "free", "status": "ended"}])
        False
        >>> free_used([{"kind": "free", "status": "completed"}])
        True
    """
    return any(r["kind"] == "free" and r["status"] == "completed" for r in rows)


async def summary(account: dict) -> dict:
    """
    Everything the workspace shows about consultations for one client.

    Args:
        account: dict, the client's accounts row (a colleague uses the owner's).

    Returns:
        dict: {"free_used", "booking_url" (the one to show now), "free_booking_url",
        "paid_booking_url", "paid_link_configured", "webhook_configured",
        "history": [consultation, ...]} — mirrored in web/lib/types.ts.

    Example:
        >>> (await summary(new_client))["free_used"]
        False
    """
    s = get_settings()
    holder = await plan_holder(account)
    rows = await account_store.list_consultations(str(holder["id"]), holder["email"])
    used = free_used(rows)
    free_link = booking_link(s.booking_url, holder, "free")
    # Without a separate paid link, everyone keeps the one link you have.
    paid_link = booking_link(s.paid_booking_url or s.booking_url, holder, "paid")
    return {
        "free_used": used,
        "booking_url": paid_link if used else free_link,
        "free_booking_url": free_link,
        "paid_booking_url": paid_link,
        "paid_link_configured": bool(s.paid_booking_url),
        "webhook_configured": bool(s.calcom_webhook_secret),
        "history": [_out(r) for r in rows],
    }


# ── Cal.com webhook ──────────────────────────────────────────────────────────

def verify_signature(raw_body: bytes, signature: str | None) -> None:
    """
    Check that a webhook really came from Cal.com.

    Cal.com signs the raw request body with your webhook secret (HMAC-SHA256)
    and sends the hex result in the `X-Cal-Signature-256` header. We compute
    the same thing; a mismatch means someone else sent it.

    Args:
        raw_body: bytes, the request body exactly as received.
        signature: str | None, the X-Cal-Signature-256 header.

    Returns:
        None: returns quietly when the signature matches.

    Raises:
        HTTPException: 503 when CALCOM_WEBHOOK_SECRET isn't set; 401 for a
            missing or wrong signature.

    Example:
        >>> secret = b"s3cret"; body = b'{"triggerEvent":"PING"}'
        >>> verify_signature(body, hmac.new(secret, body, hashlib.sha256).hexdigest())   # CALCOM_WEBHOOK_SECRET=s3cret
    """
    secret = get_settings().calcom_webhook_secret
    if not secret:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The Cal.com webhook isn't switched on (CALCOM_WEBHOOK_SECRET is not set).")
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    if not signature or not hmac.compare_digest(signature.strip().lower(), expected):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Bad webhook signature.")


def _when(text) -> datetime | None:
    """Parse Cal.com's ISO time ("2026-10-20T15:00:00Z"); None if missing or unreadable."""
    if not text:
        return None
    try:
        return datetime.fromisoformat(str(text).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


async def _match_account(payload: dict, email: str | None) -> dict | None:
    """
    Find the client a booking belongs to: metadata account id first, then email.

    Args:
        payload: dict, the webhook's "payload".
        email: str | None, the attendee's email (lowercase).

    Returns:
        dict | None: the PLAN HOLDER's accounts row, or None for an unknown person.
    """
    account = None
    account_id = (payload.get("metadata") or {}).get("account_id")
    if account_id:
        try:
            account = await account_store.get_account(str(account_id))
        except ValueError:      # not a valid id (someone edited the link)
            account = None
    if account is None and email:
        account = await account_store.get_account_by_email(email)
    return await plan_holder(account) if account else None


async def handle_webhook(event: dict) -> dict:
    """
    Apply one Cal.com webhook to the consultations table.

    Triggers handled: BOOKING_CREATED / REQUESTED / PAID -> "booked";
    BOOKING_RESCHEDULED -> same row, new time and booking uid, "booked";
    BOOKING_CANCELLED / REJECTED -> "canceled"; MEETING_ENDED -> "ended"
    (waiting for your confirmation); BOOKING_NO_SHOW_UPDATED -> "no_show"
    when an attendee is marked absent. PING (Cal.com's "test" button) and
    anything else are acknowledged and ignored.

    A row YOU confirmed (completed / no-show) is never changed by a webhook,
    except that its time is kept up to date.

    Args:
        event: dict, the webhook JSON ({"triggerEvent": ..., "payload": {...}}).

    Returns:
        dict: {"ok": True, "action": what happened, "consultation_id": ... or None}.

    Example:
        >>> await handle_webhook({"triggerEvent": "BOOKING_CREATED", "payload": {"uid": "bk1",
        ...     "startTime": "2026-10-20T15:00:00Z", "attendees": [{"email": "ana@example.com"}]}})
        {'ok': True, 'action': 'booked', 'consultation_id': '...'}
    """
    trigger = str(event.get("triggerEvent") or "").upper()
    payload = event.get("payload") or {}
    uid = payload.get("uid") or payload.get("bookingUid")
    if trigger == "PING" or not uid:
        return {"ok": True, "action": "ignored", "consultation_id": None}

    attendees = payload.get("attendees") or []
    first = attendees[0] if attendees and isinstance(attendees[0], dict) else {}
    email = (first.get("email") or "").strip().lower() or None
    holder = await _match_account(payload, email)

    # Which row: this booking, or (on a reschedule) the booking it replaced.
    old_uid = payload.get("rescheduleUid") or payload.get("fromReschedule") or payload.get("rescheduledFromUid")
    row = await account_store.get_consultation_by_booking(uid)
    if row is None and trigger == RESCHEDULED and old_uid:
        row = await account_store.get_consultation_by_booking(str(old_uid))

    if trigger in BOOKED_TRIGGERS or trigger == RESCHEDULED:
        new_status = "booked"
    elif trigger in CANCELED_TRIGGERS:
        new_status = "canceled"
    elif trigger == ENDED:
        new_status = "ended"
    elif trigger == NO_SHOW:
        absent = any(isinstance(a, dict) and a.get("noShow") for a in attendees) or payload.get("noShowHost")
        if not absent:
            return {"ok": True, "action": "ignored", "consultation_id": str(row["id"]) if row else None}
        new_status = "no_show"
    else:
        return {"ok": True, "action": "ignored", "consultation_id": str(row["id"]) if row else None}

    times = {"starts_at": _when(payload.get("startTime")), "ends_at": _when(payload.get("endTime"))}
    times = {k: v for k, v in times.items() if v is not None}

    if row is None:
        # First time we hear of this booking. Free or paid? The link says so
        # (metadata[kind]); otherwise: paid once the free call is used up.
        kind = (payload.get("metadata") or {}).get("kind")
        if kind not in ("free", "paid"):
            used = holder is not None and free_used(
                await account_store.list_consultations(str(holder["id"]), holder["email"]))
            kind = "paid" if used else "free"
        row = await account_store.save_consultation({
            "account_id": str(holder["id"]) if holder else None,
            "attendee_email": email, "attendee_name": first.get("name"),
            "kind": kind, "status": new_status, "source": "calcom", "calcom_booking_uid": uid, **times,
        })
        return {"ok": True, "action": new_status, "consultation_id": str(row["id"])}

    change: dict = {"id": row["id"], "calcom_booking_uid": uid, **times}
    if holder and not row.get("account_id"):
        change["account_id"] = str(holder["id"])
    if row.get("confirmed_at") is None:      # never overrule the consultant's own confirmation
        if not (new_status == "ended" and row["status"] in ("canceled", "no_show")):
            change["status"] = new_status
    row = await account_store.save_consultation(change)
    return {"ok": True, "action": row["status"], "consultation_id": str(row["id"])}


# ── The consultant's own confirmation ────────────────────────────────────────

async def consultant_update(account: dict, consultant: dict, *, status_: str, consultation_id: str | None,
                            kind: str, note: str | None) -> dict:
    """
    A consultant marks a client's consultation: completed, no-show, canceled or booked.

    With `consultation_id`, that consultation (it must belong to this client)
    gets the new status. Without it, a new consultation is added by hand —
    for calls booked before the Cal.com webhook was set up. Marking
    "completed" or "no_show" records who confirmed it and when.

    Args:
        account: dict, the client's accounts row (from get_acting_context).
        consultant: dict, the consultant's consultants row.
        status_: str, "completed", "no_show", "canceled" or "booked".
        consultation_id: str | None, which consultation to change.
        kind: str, "free" or "paid" (only used for a new, hand-entered row).
        note: str | None, an optional note.

    Returns:
        dict: the updated `summary(account)`.

    Raises:
        HTTPException: 404 when the consultation isn't this client's.

    Example:
        >>> s = await consultant_update(client, me, status_="completed", consultation_id=None,
        ...                             kind="free", note="Great first call")
        >>> s["free_used"]
        True
    """
    holder = await plan_holder(account)
    confirmed = {"confirmed_by": str(consultant["id"]), "confirmed_at": datetime.now(timezone.utc)} \
        if status_ in ("completed", "no_show") else {"confirmed_by": None, "confirmed_at": None}
    if consultation_id:
        row = await account_store.get_consultation(consultation_id)
        mine = row is not None and (
            str(row.get("account_id") or "") == str(holder["id"])
            or (not row.get("account_id") and row.get("attendee_email") == holder["email"]))
        if not mine:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That consultation wasn't found for this client.")
        change = {"id": row["id"], "account_id": str(holder["id"]), "status": status_, **confirmed}
        if note is not None:
            change["note"] = note
        await account_store.save_consultation(change)
    else:
        await account_store.save_consultation({
            "account_id": str(holder["id"]), "attendee_email": holder["email"],
            "attendee_name": f"{holder['first_name']} {holder['last_name']}",
            "kind": kind, "status": status_, "source": "manual", "note": note, **confirmed,
        })
    return await summary(account)
