"""
Consultations: the free first call, then paid sessions (api/services/consultations.py).

Checks the Cal.com webhook (signature, booked / rescheduled / canceled /
ended), THE RULE (only a consultant's "completed" uses up the free call), the
switch to the paid booking link, and that only a consultant can mark calls.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store

client = TestClient(app)
PW = "Correct-horse-9"
SECRET = "cal-secret"


@pytest.fixture(autouse=True)
def settings(monkeypatch):
    """
    A consultant, the access code, both booking links and the webhook secret; fresh memory store.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.

    Returns:
        Iterator[None]: yields once while the test runs.
    """
    monkeypatch.setenv("CONSULTANT_EMAILS", "coach@example.com")
    monkeypatch.setenv("CONSULTANT_ACCESS_CODE", "code-123")
    monkeypatch.setenv("BOOKING_URL", "https://cal.com/coach/free-30")
    monkeypatch.setenv("PAID_BOOKING_URL", "https://cal.com/coach/paid-60")
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", SECRET)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants,
                  account_store._consultations):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email, first="Ana", **extra):
    """Sign up an individual (asserting 201) and return the JSON reply."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": first,
                                                    "last_name": "Lee", "email": email, "password": PW, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def _h(token, acting_as=None):
    """Auth headers, optionally acting for a client."""
    h = {"Authorization": f"Bearer {token}"}
    if acting_as:
        h["X-Acting-As"] = acting_as
    return h


def _hook(trigger, payload, secret=SECRET):
    """Send a Cal.com webhook signed like Cal.com does (HMAC-SHA256 of the raw body)."""
    body = json.dumps({"triggerEvent": trigger, "payload": payload}).encode()
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return client.post("/api/consultations/calcom-webhook", content=body,
                       headers={"X-Cal-Signature-256": sig, "Content-Type": "application/json"})


def test_booking_link_carries_the_client_and_switches_to_paid():
    """New clients get the free link (prefilled); once a consultant confirms the free call, the paid link."""
    coach = _signup("coach@example.com", "Coach", access_code="code-123")["access_token"]
    ana = _signup("ana@example.com", wants_consultant=True)
    aid = ana["account"]["id"]
    info = client.get("/api/consultations", headers=_h(ana["access_token"])).json()
    assert info["free_used"] is False
    assert info["booking_url"].startswith("https://cal.com/coach/free-30?")
    assert "email=ana%40example.com" in info["booking_url"] and f"metadata%5Baccount_id%5D={aid}" in info["booking_url"]

    # A client can't mark their own call as done.
    assert client.post("/api/consultations", headers=_h(ana["access_token"]), json={"status": "completed"}).status_code == 403

    # The consultant confirms a free call that was booked before the webhook existed (manual row).
    done = client.post("/api/consultations", headers=_h(coach, aid), json={"status": "completed", "kind": "free"}).json()
    assert done["free_used"] is True
    assert done["booking_url"].startswith("https://cal.com/coach/paid-60?")
    assert done["history"][0]["source"] == "manual" and done["history"][0]["confirmed_at"]
    # The same news reaches the workspace's plan summary.
    plan = client.get("/api/accounts/services", headers=_h(ana["access_token"])).json()
    assert plan["consulting"]["free_used"] is True


def test_webhook_signature_is_required():
    """An unsigned or wrongly signed webhook is refused; with no secret set, the webhook is off."""
    assert _hook("PING", {}, secret="wrong").status_code == 401
    assert client.post("/api/consultations/calcom-webhook", json={"triggerEvent": "PING"}).status_code == 401
    assert _hook("PING", {}).json()["action"] == "ignored"


def test_webhook_off_without_secret(monkeypatch):
    """Without CALCOM_WEBHOOK_SECRET the webhook answers 503 (switched off)."""
    monkeypatch.setenv("CALCOM_WEBHOOK_SECRET", "")
    get_settings.cache_clear()
    assert _hook("PING", {}).status_code == 503


def test_webhook_lifecycle_and_the_rule():
    """Booked -> rescheduled -> ended keeps one row; "ended" does NOT use up the free call; confirming does."""
    coach = _signup("coach@example.com", "Coach", access_code="code-123")["access_token"]
    ana = _signup("ana@example.com", wants_consultant=True)
    aid = ana["account"]["id"]
    meta = {"account_id": aid, "kind": "free"}

    booked = _hook("BOOKING_CREATED", {"uid": "bk1", "startTime": "2026-10-20T15:00:00Z",
                                       "endTime": "2026-10-20T15:30:00Z", "metadata": meta,
                                       "attendees": [{"email": "ana@example.com", "name": "Ana Lee"}]}).json()
    assert booked["action"] == "booked"
    moved = _hook("BOOKING_RESCHEDULED", {"uid": "bk2", "rescheduleUid": "bk1", "startTime": "2026-10-22T15:00:00Z",
                                          "attendees": [{"email": "ana@example.com"}], "metadata": meta}).json()
    assert moved["consultation_id"] == booked["consultation_id"]     # same consultation, new time
    ended = _hook("MEETING_ENDED", {"uid": "bk2", "attendees": [{"email": "ana@example.com"}]}).json()
    assert ended["action"] == "ended"

    info = client.get("/api/consultations", headers=_h(coach, aid)).json()
    assert len(info["history"]) == 1
    row = info["history"][0]
    assert row["status"] == "ended" and row["starts_at"].startswith("2026-10-22")
    assert info["free_used"] is False                # ended is not enough: the consultant decides

    confirmed = client.post("/api/consultations", headers=_h(coach, aid),
                            json={"status": "completed", "consultation_id": row["id"]}).json()
    assert confirmed["free_used"] is True
    # A late webhook can't undo the consultant's confirmation.
    _hook("BOOKING_CANCELLED", {"uid": "bk2", "attendees": [{"email": "ana@example.com"}]})
    assert client.get("/api/consultations", headers=_h(coach, aid)).json()["history"][0]["status"] == "completed"


def test_cancel_and_no_show_keep_the_free_call():
    """A canceled booking or a no-show leaves the free consultation unused."""
    coach = _signup("coach@example.com", "Coach", access_code="code-123")["access_token"]
    ana = _signup("ana@example.com", wants_consultant=True)
    aid = ana["account"]["id"]
    _hook("BOOKING_CREATED", {"uid": "bk9", "attendees": [{"email": "ana@example.com"}]})   # matched by email
    _hook("BOOKING_CANCELLED", {"uid": "bk9", "attendees": [{"email": "ana@example.com"}]})
    info = client.get("/api/consultations", headers=_h(coach, aid)).json()
    assert info["history"][0]["status"] == "canceled" and info["free_used"] is False
    no_show = client.post("/api/consultations", headers=_h(coach, aid),
                          json={"status": "no_show", "consultation_id": info["history"][0]["id"]}).json()
    assert no_show["free_used"] is False


def test_guest_booking_is_matched_when_the_account_appears():
    """Someone books before signing up; the booking shows up on their account by email."""
    _hook("BOOKING_CREATED", {"uid": "early", "attendees": [{"email": "new@example.com"}]})
    later = _signup("new@example.com", "New")
    info = client.get("/api/consultations", headers=_h(later["access_token"])).json()
    assert [h["status"] for h in info["history"]] == ["booked"]


def test_cannot_mark_another_clients_consultation():
    """A consultation id from another client is 404 for this client."""
    coach = _signup("coach@example.com", "Coach", access_code="code-123")["access_token"]
    ana = _signup("ana@example.com", wants_consultant=True)["account"]["id"]
    bo = _signup("bo@example.com", "Bo", wants_consultant=True)["account"]["id"]
    bos = client.post("/api/consultations", headers=_h(coach, bo), json={"status": "booked"}).json()["history"][0]["id"]
    res = client.post("/api/consultations", headers=_h(coach, ana), json={"status": "completed", "consultation_id": bos})
    assert res.status_code == 404
