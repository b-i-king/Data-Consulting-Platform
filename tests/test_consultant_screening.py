"""
Consultant screening (api/services/consultants.py): invite list, the
application, the admin's decision with a reason, and the waiting period
before someone who was turned down may apply again.
"""

from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, consultants
from tests.consultant_helpers import APPLICATION, apply_as_consultant

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def screening_settings(monkeypatch):
    """One admin (boss@example.com), invite-only applications, a fresh memory store."""
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.delenv("CONSULTANT_APPLICATIONS", raising=False)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants,
                  account_store._invites, account_store._applications):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email, first="Kim"):
    """Sign up an individual; returns (token, account id)."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": first,
                                                    "last_name": "Lee", "email": email, "password": PW,
                                                    "accept_terms": True})
    assert res.status_code == 201, res.text
    return res.json()["access_token"], res.json()["account"]["id"]


def _h(token):
    """Authorization header."""
    return {"Authorization": f"Bearer {token}"}


def _status(token):
    """GET /api/accounts/consultant-application as this person."""
    return client.get("/api/accounts/consultant-application", headers=_h(token)).json()


def _apply(token, **answers):
    """POST the screening application (APPLICATION with any changes)."""
    return client.post("/api/accounts/consultant-request", headers=_h(token), json={**APPLICATION, **answers})


def test_invite_only_until_an_admin_adds_the_email():
    admin, _ = _signup("boss@example.com", "Boss")
    kim, _ = _signup("kim@example.com")
    check = _status(kim)
    assert check["can_apply"] is False and check["blocked"] == "not_invited" and check["mode"] == "invite"
    assert len(check["code_of_conduct"]) >= 3
    assert [r["id"] for r in check["experience_ranges"]] == ["lt_1", "1_3", "3_5", "5_10", "10_plus"]
    assert _apply(kim).status_code == 403
    # Only admins manage the list.
    assert client.post("/api/admin/consultant-invites", headers=_h(kim), json={"email": "kim@example.com"}).status_code == 403
    panel = client.post("/api/admin/consultant-invites", headers=_h(admin),
                        json={"email": "Kim@Example.com", "note": "Met at the meetup"}).json()
    assert panel["invites"] == [{"email": "kim@example.com", "note": "Met at the meetup",
                                 "created_at": panel["invites"][0]["created_at"], "has_account": True}]
    assert _status(kim)["can_apply"] is True and _status(kim)["invited"] is True
    applied = _apply(kim)
    assert applied.status_code == 200 and applied.json()["consultant_request"] == "pending"
    assert _status(kim)["blocked"] == "pending"
    assert _apply(kim).status_code == 409                                     # one at a time
    # Removing the invite stops new applications only.
    assert client.delete("/api/admin/consultant-invites/kim@example.com", headers=_h(admin)).status_code == 200
    assert client.delete("/api/admin/consultant-invites/kim@example.com", headers=_h(admin)).status_code == 404
    assert account_store._accounts[applied.json()["id"]]["consultant_request"] == "pending"


def test_open_mode_lets_anyone_with_an_account_apply(monkeypatch):
    monkeypatch.setenv("CONSULTANT_APPLICATIONS", "open")
    get_settings.cache_clear()
    kim, _ = _signup("kim@example.com")
    assert _status(kim)["can_apply"] is True and _status(kim)["mode"] == "open"
    assert _apply(kim).status_code == 200


def test_the_application_must_be_complete():
    admin, _ = _signup("boss@example.com", "Boss")
    kim, _ = _signup("kim@example.com")
    client.post("/api/admin/consultant-invites", headers=_h(admin), json={"email": "kim@example.com"})
    assert _apply(kim, agree_conduct=False).status_code == 422                # code of conduct
    assert _apply(kim, linkedin_url="").status_code == 422                    # LinkedIn is required
    assert _apply(kim, linkedin_url="https://github.com/kim").status_code == 422      # ...and must be LinkedIn
    assert _apply(kim, linkedin_url="https://www.linkedin.com/company/acme").status_code == 422   # a person
    assert _apply(kim, linkedin_url="https://www.linkedin.com.evil.com/in/kim").status_code == 422
    assert _apply(kim, work_sample_url="http://github.com/kim").status_code == 422     # https only
    assert _apply(kim, services=[]).status_code == 422
    assert _apply(kim, services=["astrology"]).status_code == 422
    assert _apply(kim, project_summary="I know Excel.").status_code == 422    # too short to judge
    assert _apply(kim, experience="7").status_code == 422                     # one of the ranges
    ok = _apply(kim, work_sample_url="", linkedin_url="linkedin.com/in/kim-lee")   # the sample is optional
    assert ok.status_code == 200
    stored = next(iter(account_store._applications.values()))
    assert stored["work_sample_url"] is None and stored["agreed_conduct_at"]
    assert stored["linkedin_url"] == "https://linkedin.com/in/kim-lee"       # https:// added


FEEDBACK = "Your dashboard link was empty. Share a finished dashboard next time — we'd love to see it."


def test_a_denial_needs_a_reason_and_starts_the_waiting_period():
    admin, _ = _signup("boss@example.com", "Boss")
    kim, kim_id = _signup("kim@example.com")
    assert apply_as_consultant(client, _h(admin), kim, "kim@example.com").status_code == 200
    deny = f"/api/admin/consultant-requests/{kim_id}/deny"
    assert client.post(deny, headers=_h(admin), json={"note": FEEDBACK}).status_code == 400   # no reason
    no_feedback = client.post(deny, headers=_h(admin), json={"denial_reason": "work_sample", "note": "No."})
    assert no_feedback.status_code == 400 and "personal feedback" in no_feedback.json()["detail"]
    assert client.post(deny, headers=_h(admin), json={"denial_reason": "work_sample", "note": FEEDBACK,
                                                      "wait_months": 1}).status_code == 422
    panel = client.post(deny, headers=_h(admin), json={"denial_reason": "work_sample", "wait_months": 6,
                                                       "note": FEEDBACK}).json()
    row = panel["requests"][0]
    expected = consultants.add_months(date.today(), 6)
    assert row["status"] == "denied" and row["application"]["denial_reason"] == "work_sample"
    assert row["application"]["reapply_after"] == expected.isoformat()
    assert {r["id"] for r in panel["denial_reasons"]} == set(consultants.DENIAL_REASONS)
    assert panel["reapply_choices"] == [3, 6, 12]
    # The person sees why, and when they can apply again (US date).
    check = _status(kim)
    assert check["blocked"] == "waiting" and check["reapply_after"] == expected.isoformat()
    assert check["message"] == f"You can apply again on {expected.strftime('%m/%d/%Y')}."
    assert check["latest"]["denial_reason_text"] == consultants.DENIAL_REASONS["work_sample"]
    assert check["latest"]["decision_note"] == FEEDBACK
    assert check["latest"]["human_review_note"] == consultants.HUMAN_REVIEW_NOTE   # a person decided; subjective
    assert _apply(kim).status_code == 409
    # Nothing to decide any more.
    assert client.post(f"/api/admin/consultant-requests/{kim_id}/approve", headers=_h(admin)).status_code == 400


def test_the_default_wait_is_three_months_and_reapplying_keeps_history():
    admin, _ = _signup("boss@example.com", "Boss")
    kim, kim_id = _signup("kim@example.com")
    apply_as_consultant(client, _h(admin), kim, "kim@example.com")
    client.post(f"/api/admin/consultant-requests/{kim_id}/deny", headers=_h(admin),
                json={"denial_reason": "not_enough_experience", "note": FEEDBACK})
    [first] = account_store._applications.values()
    assert first["reapply_after"] == consultants.add_months(date.today(), consultants.MIN_REAPPLY_MONTHS)
    first["reapply_after"] = date.today()                 # three months later...
    assert _status(kim)["can_apply"] is True
    assert _apply(kim, experience="5_10").status_code == 200
    panel = client.get("/api/admin/consultant-requests", headers=_h(admin)).json()
    row = next(r for r in panel["requests"] if r["account_id"] == kim_id)
    assert row["status"] == "pending" and row["application"]["times_applied"] == 2
    assert row["application"]["experience"] == "5_10" and row["application"]["experience_label"] == "5–10 years"
    assert row["application"]["denial_reason"] is None
    # Approved: a consultant, and can't apply again.
    client.post(f"/api/admin/consultant-requests/{kim_id}/approve", headers=_h(admin))
    kim = client.post("/api/accounts/signin", json={"email": "kim@example.com", "password": PW}).json()["access_token"]
    assert _status(kim)["blocked"] == "already"


def test_an_approved_consultant_is_onboarded():
    """
    After approval: a checklist (two-step sign-in, the independent-contractor
    terms, both Cal.com links, the webhook). The workspace nudges until the
    required steps are done.
    """
    admin, _ = _signup("boss@example.com", "Boss")
    kim, kim_id = _signup("kim@example.com")
    apply_as_consultant(client, _h(admin), kim, "kim@example.com")
    client.post(f"/api/admin/consultant-requests/{kim_id}/approve", headers=_h(admin))
    kim = client.post("/api/accounts/signin", json={"email": "kim@example.com", "password": PW}).json()["access_token"]
    first = client.get("/api/consultant/settings", headers=_h(kim)).json()
    steps = {st["id"]: st for st in first["onboarding"]["steps"]}
    assert first["setup_needed"] and not first["onboarding"]["done"]
    assert list(steps) == ["two_step", "contractor", "payouts", "free_link", "paid_link", "webhook", "capacity"]
    assert first["payouts"] == {"needed": True, "connected": False, "ready": False, "test_mode": True}
    assert steps["contractor"]["required"] and not steps["contractor"]["done"] and not steps["capacity"]["required"]
    assert any("taxes" in t for t in first["onboarding"]["contractor_terms"])
    agreed = client.post("/api/consultant/settings/contractor-agreement", headers=_h(kim)).json()
    assert agreed["onboarding"]["contractor_agreed_at"]
    # The admin's confirmation: Admin -> Consultants shows the date and what's left.
    row = next(c for c in client.get("/api/admin/consultant-requests", headers=_h(admin)).json()["team"]
               if c["email"] == "kim@example.com")
    assert row["contractor_agreed_at"] and row["contractor_terms_current"] and not row["onboarding_done"]
    assert not any("contractor" in step for step in row["onboarding_left"])
    cid = next(c["id"] for c in account_store._consultants.values() if c["account_id"] == kim_id)
    assert account_store._consultants[cid]["contractor_terms_version"] == consultants.CONTRACTOR_TERMS_VERSION
    # Payouts (Stripe Connect; test mode: connected at once).
    assert client.post("/api/consultant/settings/payouts", headers=_h(kim)).json() == {"url": None, "ready": True}
    client.put("/api/consultant/settings", headers=_h(kim), json={
        "free_booking_url": "https://cal.com/kim/30min", "paid_booking_url": "https://cal.com/kim/session"})
    last = client.post("/api/consultant/settings/webhook-secret", headers=_h(kim)).json()
    left = [st["id"] for st in last["onboarding"]["steps"] if st["required"] and not st["done"]]
    assert left == ["two_step"]                                   # only two-step sign-in is left
    # With two-step sign-in on, onboarding is finished and the nudge goes away.
    account_store._accounts[kim_id].update(mfa_enabled_at=date.today(), mfa_secret="encrypted")
    done = client.get("/api/consultant/settings", headers=_h(kim)).json()
    assert done["onboarding"]["done"] and not done["setup_needed"]


def test_deleting_the_account_removes_its_applications():
    admin, _ = _signup("boss@example.com", "Boss")
    kim, _ = _signup("kim@example.com")
    apply_as_consultant(client, _h(admin), kim, "kim@example.com")
    assert account_store._applications
    res = client.request("DELETE", "/api/accounts/me", headers=_h(kim), json={"confirm_email": "kim@example.com"})
    assert res.status_code == 200, res.text
    assert not account_store._applications


def test_add_months_keeps_the_day_or_uses_the_month_end():
    assert consultants.add_months(date(2026, 10, 18), 3) == date(2027, 1, 18)
    assert consultants.add_months(date(2026, 11, 30), 3) == date(2027, 2, 28)
    assert consultants.add_months(date(2027, 12, 31), 12) == date(2028, 12, 31)


def test_schema_has_the_screening_tables():
    sql = Path("db/schema.sql").read_text(encoding="utf-8")
    assert "CREATE TABLE IF NOT EXISTS dim.consultant_invites" in sql
    assert "CREATE TABLE IF NOT EXISTS dim.consultant_applications" in sql
    assert "'consultant_invites', 'consultant_applications'" in sql          # row level security on
    for reason in consultants.DENIAL_REASONS:                                 # the CHECK matches the list
        assert f"'{reason}'" in sql
