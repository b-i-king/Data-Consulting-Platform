"""
Security: signed-in devices (3 at most, idle sign-out, revoking), two-step
sign-in for staff, per-IP request limits, the healthcare audit log, encrypted
emails / phones in stored data, and error reports with personal data stripped.
(api/services/sessions.py, mfa.py, audit.py, pii.py; api/core/rate_limit.py,
crypto.py, monitoring.py.) In-memory store.
"""

import asyncio
import time
from datetime import timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.core import crypto, monitoring, rate_limit
from api.core.config import get_settings
from api.main import app
from api.services import account_store, auth, login_guard, mfa, pii, sessions, warehouse_store

client = TestClient(app)
PW = "Correct-horse-9"
CHROME = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126.0 Safari/537.36"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """An admin (boss@example.com), no Supabase / Stripe, an empty memory store."""
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL", "STRIPE_SECRET_KEY"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants, account_store._sessions,
                  account_store._audit, account_store._subscriptions, account_store._consents, warehouse_store._stored,
                  login_guard._unknown, rate_limit._hits):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email="ana@example.com", first="Ana", **extra):
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": first,
                                                    "last_name": "Lee", "email": email, "password": PW,
                                                    "accept_terms": True, **extra})
    assert res.status_code == 201, res.text
    return res.json()["access_token"], res.json()["account"]["id"]


def _signin(email="ana@example.com", device=None, password=PW):
    return client.post("/api/accounts/signin", json={"email": email, "password": password},
                       headers={"User-Agent": CHROME, **({"X-Device-Id": device} if device else {})})


def _h(token, acting_as=None):
    return {"Authorization": f"Bearer {token}", **({"X-Acting-As": acting_as} if acting_as else {})}


# ── Signed-in devices ────────────────────────────────────────────────────────

def test_device_labels_keep_only_browser_and_system():
    assert sessions.device_label(CHROME) == "Chrome on Windows"
    assert sessions.device_label("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) AppleWebKit Safari/604.1") == "Safari on iPhone / iPad"
    assert sessions.device_label(None) == "Unknown device"


def test_three_devices_at_most_and_tabs_count_once():
    """A 4th device signs out the least recently used; the same browser (device id) reuses its session."""
    _signup()
    tokens = [_signin(device=d).json()["access_token"] for d in ("a", "b", "c")]
    again = _signin(device="b")                                          # another tab of browser b
    assert again.json()["signed_out_devices"] == []
    fourth = _signin(device="d")
    assert fourth.json()["signed_out_devices"] == ["Chrome on Windows"]
    gone = client.get("/api/accounts/me", headers=_h(tokens[0]))
    assert gone.status_code == 401 and "3 devices at most" in gone.json()["detail"]
    mine = client.get("/api/accounts/sessions", headers=_h(fourth.json()["access_token"])).json()
    assert len(mine["sessions"]) == 3 and mine["max_devices"] == 3
    assert sum(s["this_device"] for s in mine["sessions"]) == 1


def test_signing_out_one_device_others_and_after_a_password_reset(monkeypatch):
    _signup()
    a, b = _signin(device="a").json()["access_token"], _signin(device="b").json()["access_token"]
    b_sid = next(s["id"] for s in client.get("/api/accounts/sessions", headers=_h(b)).json()["sessions"] if s["this_device"])
    assert client.delete(f"/api/accounts/sessions/{b_sid}", headers=_h(a)).status_code == 200
    assert client.get("/api/accounts/me", headers=_h(b)).status_code == 401
    c = _signin(device="c").json()["access_token"]
    # Signing up opened a session too, so "everywhere else" = that one and device a.
    assert client.post("/api/accounts/sessions/sign-out-others", headers=_h(c)).json()["signed_out"] == 2
    assert client.get("/api/accounts/me", headers=_h(a)).status_code == 401 and \
        client.get("/api/accounts/me", headers=_h(c)).status_code == 200
    assert client.post("/api/accounts/signout", headers=_h(c)).status_code == 200
    assert "signed out on this device" in client.get("/api/accounts/me", headers=_h(c)).json()["detail"]

    # A password reset signs out every device.
    d = _signin(device="d").json()["access_token"]
    links = []
    monkeypatch.setattr(auth.log, "warning", lambda msg, *args: links.append(msg % args))
    client.post("/api/accounts/forgot-password", json={"email": "ana@example.com"})
    client.post("/api/accounts/reset-password", json={"token": links[-1].split("token=")[1], "password": "A-brand-new-pass"})
    assert "password was changed" in client.get("/api/accounts/me", headers=_h(d)).json()["detail"]


def test_idle_devices_are_signed_out(monkeypatch):
    _signup()
    token = _signin(device="a").json()["access_token"]
    for s in account_store._sessions.values():
        s["last_seen_at"] -= timedelta(hours=9)
    res = client.get("/api/accounts/me", headers=_h(token))
    assert res.status_code == 401 and "without activity" in res.json()["detail"]


def test_an_admin_signs_someone_out_everywhere():
    admin, _ = _signup("boss@example.com", "Boss")
    _, ana_id = _signup()
    ana = _signin(device="x").json()["access_token"]
    assert client.post(f"/api/admin/accounts/{ana_id}/sign-out", headers=_h(admin)).json()["signed_out"] >= 1
    assert "admin signed this account out" in client.get("/api/accounts/me", headers=_h(ana)).json()["detail"]


# ── Two-step sign-in ─────────────────────────────────────────────────────────

def test_totp_matches_the_rfc_6238_test_vector():
    """RFC 6238's test key "12345678901234567890" at time 59 gives 94287082 (8 digits) -> 287082 (6)."""
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"
    assert mfa.code_at(secret, 59 // 30) == "287082"
    assert mfa.verify_code(secret, "287 082", now=59) and mfa.verify_code(secret, "287082", now=59 + 30)
    assert not mfa.verify_code(secret, "287082", now=59 + 120)


def test_staff_need_two_step_sign_in(monkeypatch):
    """
    With MFA_REQUIRED_FOR_STAFF: the Admin panel is refused until it's on; after setup,
    sign-in asks for the code; a backup code works once; wrong codes count toward the lockout.
    """
    monkeypatch.setenv("MFA_REQUIRED_FOR_STAFF", "true")
    get_settings.cache_clear()
    admin, _ = _signup("boss@example.com", "Boss")
    me = client.get("/api/accounts/me", headers=_h(admin)).json()
    assert me["mfa_required"] and not me["mfa_enabled"]
    blocked = client.get("/api/admin/prices", headers=_h(admin))
    assert blocked.status_code == 403 and "two-step" in blocked.json()["detail"]

    setup = client.post("/api/accounts/mfa/setup", headers=_h(admin)).json()
    assert setup["otpauth_url"].startswith("otpauth://totp/") and len(setup["secret"]) >= 32
    stored = next(a for a in account_store._accounts.values() if a["email"] == "boss@example.com")
    assert crypto.is_encrypted(stored["mfa_secret"]) and setup["secret"] not in stored["mfa_secret"]
    assert client.post("/api/accounts/mfa/enable", headers=_h(admin), json={"code": "000000"}).status_code == 400
    on = client.post("/api/accounts/mfa/enable", headers=_h(admin),
                     json={"code": mfa.code_at(setup["secret"], int(time.time() // 30))}).json()
    assert len(on["recovery_codes"]) == 10
    assert client.get("/api/admin/prices", headers=_h(on["access_token"])).status_code == 200

    step1 = _signin("boss@example.com").json()
    assert step1["mfa_required"] and step1["access_token"] is None and step1["account"] is None
    assert client.get("/api/accounts/me", headers=_h(step1["mfa_ticket"])).status_code == 401   # a ticket isn't a login
    wrong = client.post("/api/accounts/signin/mfa", json={"ticket": step1["mfa_ticket"], "code": "111111"})
    assert wrong.status_code == 401 and "attempts left" in wrong.json()["detail"]
    ok = client.post("/api/accounts/signin/mfa", json={"ticket": step1["mfa_ticket"], "code": on["recovery_codes"][0]})
    assert ok.status_code == 200 and client.get("/api/admin/prices", headers=_h(ok.json()["access_token"])).status_code == 200
    reused = client.post("/api/accounts/signin/mfa", json={"ticket": step1["mfa_ticket"], "code": on["recovery_codes"][0]})
    assert reused.status_code == 401                                     # each backup code works once
    code = mfa.code_at(setup["secret"], int(time.time() // 30))
    assert client.post("/api/accounts/mfa/disable", headers=_h(ok.json()["access_token"]),
                       json={"code": code}).status_code == 400          # required for staff


def test_an_admin_resets_a_lost_two_step_sign_in():
    admin, _ = _signup("boss@example.com", "Boss")
    ana, ana_id = _signup()
    secret = client.post("/api/accounts/mfa/setup", headers=_h(ana)).json()["secret"]
    client.post("/api/accounts/mfa/enable", headers=_h(ana), json={"code": mfa.code_at(secret, int(time.time() // 30))})
    assert _signin().json()["mfa_required"]
    res = client.post(f"/api/admin/accounts/{ana_id}/reset-mfa", headers=_h(admin))
    assert res.status_code == 200 and not res.json()["account"]["mfa_enabled"]
    assert _signin().json()["access_token"]                              # password alone again


# ── Per-IP request limits ────────────────────────────────────────────────────

def test_sign_in_attempts_per_ip_are_limited(monkeypatch):
    monkeypatch.setenv("AUTH_RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    for _ in range(3):
        client.post("/api/accounts/signin", json={"email": "x@example.com", "password": "nope-nope"})
    res = client.post("/api/accounts/signin", json={"email": "y@example.com", "password": "nope-nope"})
    assert res.status_code == 429 and int(res.headers["Retry-After"]) >= 1
    assert client.get("/health").status_code == 200                      # never limited


def test_the_visitor_ip_comes_from_the_trusted_proxy(monkeypatch):
    from starlette.requests import Request

    def req(xff):
        return Request({"type": "http", "headers": [(b"x-forwarded-for", xff.encode())], "client": ("10.0.0.9", 1)})

    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "1")
    get_settings.cache_clear()
    assert rate_limit.client_ip(req("1.2.3.4, 203.0.113.9")) == "203.0.113.9"   # a faked first entry is ignored
    monkeypatch.setenv("TRUSTED_PROXY_HOPS", "0")
    monkeypatch.delenv("RENDER", raising=False)
    get_settings.cache_clear()
    assert rate_limit.client_ip(req("1.2.3.4")) == "10.0.0.9"


# ── Healthcare audit log ─────────────────────────────────────────────────────

def _business(email, sector):
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": email, "company_name": "Clinic",
        "password": PW, "accept_terms": True, "industry_supersector": "service_providing", "naics_sector": sector,
        "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": f"bo.{email}", "role": "developer"}]})
    assert res.status_code == 201, res.text
    return res.json()["access_token"]


CSV = b"visit_id,visit_date,patient,patient_email,patient_phone,amount\n" + b"".join(
    f"{i},2026-03-{i % 28 + 1:02d},Pat {i % 5},pat{i % 5}@clinic.org,404-555-01{i % 10}{i % 10},{i}.5\n".encode()
    for i in range(1, 41))


def _approve_hipaa(email):
    """The admin confirms a business's HIPAA request (as Admin -> HIPAA requests does)."""
    admin = _signin("boss@example.com").json()
    if not admin.get("access_token"):
        _signup("boss@example.com", "Boss")
        admin = _signin("boss@example.com").json()
    aid = next(a["id"] for a in account_store._accounts.values() if a["email"] == email)
    res = client.post(f"/api/admin/hipaa-requests/{aid}/approve", headers=_h(admin["access_token"]), json={})
    assert res.status_code == 200, res.text


def _store(token):
    client.post("/api/billing/checkout", headers=_h(token), json={"plan_ids": ["data_engineering"], "interval": "month"})
    files = [("files", ("visits.csv", CSV, "text/csv"))]
    fp = client.post("/api/warehouse/preview", headers=_h(token), files=files).json()["fingerprint"]
    res = client.post("/api/warehouse/load", headers=_h(token), files=files, data={"fingerprint": fp, "consent": "true"})
    assert res.status_code == 200, res.text


def test_healthcare_clients_get_an_audit_log_and_others_dont():
    clinic = _business("ann@clinic.org", "62")                            # NAICS 62: healthcare
    _approve_hipaa("ann@clinic.org")                                     # an admin confirmed it
    shop = _business("ann@shop.com", "44-45")
    for t in (clinic, shop):
        _store(t)
        client.get("/api/warehouse/export?source=visits.csv", headers=_h(t))
    bo = _signin("bo.ann@clinic.org").json()["access_token"]
    client.get("/api/warehouse/summary", headers=_h(bo))

    log = client.get("/api/accounts/audit", headers=_h(clinic)).json()
    assert log["enabled"]
    actions = [(e["action"], e["actor_role"]) for e in log["entries"]]
    assert ("load", "owner") in actions and ("export", "owner") in actions and ("view_stored", "developer") in actions
    assert ("sign_in", "developer") in actions
    load = next(e for e in log["entries"] if e["action"] == "load")
    assert load["detail"]["rows"] > 0 and "pat1@clinic.org" not in str(log)   # counts, never data
    assert client.get("/api/accounts/audit", headers=_h(shop)).json() == {"enabled": False, "entries": []}
    assert client.get("/api/accounts/audit", headers=_h(bo)).status_code == 403   # the owner reads it
    csv = client.get("/api/accounts/audit?format=csv", headers=_h(clinic))
    assert csv.status_code == 200 and csv.text.splitlines()[0].lstrip("﻿").startswith("when_utc,action,who")
    assert all(r["account_id"] != next(a["id"] for a in account_store._accounts.values() if a["email"] == "ann@shop.com")
               for r in account_store._audit)


def test_an_admin_switches_healthcare_on():
    admin, _ = _signup("boss@example.com", "Boss")
    _, ana_id = _signup()
    res = client.post(f"/api/admin/accounts/{ana_id}/healthcare", headers=_h(admin), json={"is_healthcare": True})
    assert res.status_code == 200 and res.json()["account"]["is_healthcare"]
    assert not account_store._audit
    _signin()
    assert [(r["action"], r["account_id"]) for r in account_store._audit] == [("sign_in", ana_id)]


# ── Encrypted emails and phone numbers ──────────────────────────────────────

def test_contact_fields_are_found_by_name():
    assert all(pii.is_contact_field(n) for n in ("customer_email", "Patient Phone", "mobile", "e-mail", "fax_number"))
    assert not any(pii.is_contact_field(n) for n in ("hotel_name", "patient", "amount", "telemetry_id"))


def test_stored_emails_and_phones_are_encrypted_and_come_back_readable():
    clinic = _business("ann@clinic.org", "44-45")
    _store(clinic)
    [stored] = list(warehouse_store._stored.values())
    attrs = stored["attributes"].iloc[0]
    assert crypto.is_encrypted(attrs["patient_email"]) and crypto.is_encrypted(attrs["patient_phone"])
    assert attrs["patient"] == "Pat 1"                                  # names stay readable for reports
    back = client.get("/api/warehouse/export?source=visits.csv", headers=_h(clinic)).text
    assert "pat1@clinic.org" in back and "enc1:" not in back
    assert pii.reveal(pii.protect({"x_email": "a@b.co"})) == {"x_email": "a@b.co"}
    assert crypto.blind_index("A@B.co ", "pii") == crypto.blind_index("a@b.co", "pii")


# ── Error reports ────────────────────────────────────────────────────────────

def test_error_reports_lose_personal_data():
    event = {"message": "No account for ana@example.com (404) 555-0123",
             "request": {"data": "file rows", "cookies": {"a": 1}, "query_string": "email=ana@example.com",
                         "headers": {"Authorization": "Bearer x", "X-Acting-As": "id", "Accept": "json"}},
             "user": {"email": "ana@example.com"},
             "exception": {"values": [{"value": "bad bo@x.com", "stacktrace": {"frames": [{"vars": {"df": "rows"}}]}}]}}
    out = monitoring.scrub(event)
    assert out["message"] == "No account for [email] [phone]"
    assert "data" not in out["request"] and "query_string" not in out["request"] and "user" not in out
    assert out["request"]["headers"] == {"Accept": "json"}
    assert out["exception"]["values"][0]["value"] == "bad [email]"
    assert "vars" not in out["exception"]["values"][0]["stacktrace"]["frames"][0]
    assert monitoring.init() is False                                    # no SENTRY_DSN: nothing is sent


# ── HIPAA: an admin confirms a healthcare business before health data is stored ──

def test_npi_check_digit():
    from api.services import hipaa

    assert hipaa.npi_valid("1234567893") and hipaa.npi_valid("123-456-7893")   # CMS's example NPI
    assert not hipaa.npi_valid("1234567890") and not hipaa.npi_valid("12345")


def test_a_healthcare_signup_waits_for_an_admin_before_anything_is_stored():
    """
    NAICS 62 opens a HIPAA request at sign-up: storing is paused (cleaning still works),
    no audit log yet; the admin approves -> storing works and every access is audited.
    """
    admin, _ = _signup("boss@example.com", "Boss")
    clinic = _business("ann@clinic.org", "62")
    me = client.get("/api/accounts/hipaa", headers=_h(clinic)).json()
    assert me["status"] == "pending" and me["storing_paused"] and me["organization"] == "Clinic"
    client.post("/api/billing/checkout", headers=_h(clinic), json={"plan_ids": ["data_engineering"], "interval": "month"})
    files = [("files", ("visits.csv", CSV, "text/csv"))]
    paused = client.post("/api/warehouse/preview", headers=_h(clinic), files=files)
    assert paused.status_code == 403 and "paused" in paused.json()["detail"]
    assert client.post("/api/files/analyze", files=files).status_code == 200    # cleaning works (memory only)
    assert client.get("/api/accounts/audit", headers=_h(clinic)).json()["enabled"] is False

    # More details for the admin; a bad NPI is refused.
    assert client.post("/api/accounts/hipaa", headers=_h(clinic),
                       json={"organization": "Ko Family Clinic LLC", "npi": "1234567890"}).status_code == 400
    bo = _signin("bo.ann@clinic.org").json()["access_token"]
    assert client.post("/api/accounts/hipaa", headers=_h(bo), json={"organization": "X Clinic"}).status_code == 403

    waiting = client.get("/api/admin/hipaa-requests", headers=_h(admin)).json()["requests"]
    assert [r["status"] for r in waiting] == ["pending"]
    aid = waiting[0]["account_id"]
    assert client.post("/api/accounts/hipaa", headers=_h(clinic), json={}).status_code == 422   # organization needed
    assert client.post(f"/api/admin/hipaa-requests/{aid}/approve", headers=_h(clinic), json={}).status_code == 403
    ok = client.post(f"/api/admin/hipaa-requests/{aid}/approve", headers=_h(admin), json={"note": "NPI checked"})
    assert ok.json()["requests"][0]["status"] == "approved"
    after = client.get("/api/accounts/hipaa", headers=_h(clinic)).json()
    assert after["status"] == "approved" and after["decision_note"] == "NPI checked" and not after["storing_paused"]
    _store(clinic)
    assert client.get("/api/accounts/audit", headers=_h(clinic)).json()["enabled"]
    assert client.post("/api/accounts/hipaa", headers=_h(clinic), json={"organization": "Another Clinic"}).status_code == 409


def test_any_business_can_ask_withdraw_or_be_turned_down():
    admin, _ = _signup("boss@example.com", "Boss")
    shop = _business("ann@shop.com", "44-45")
    assert client.get("/api/accounts/hipaa", headers=_h(shop)).json()["status"] is None   # not asked: nothing paused
    asked = client.post("/api/accounts/hipaa", headers=_h(shop),
                        json={"organization": "Ko Pharmacy", "npi": "1234567893", "note": "prescription refills"})
    assert asked.json()["status"] == "pending" and "npiregistry.cms.hhs.gov" in asked.json()["lookup_url"]
    updated = client.post("/api/accounts/hipaa", headers=_h(shop), json={"organization": "Ko Pharmacy Inc."})
    assert updated.json()["organization"] == "Ko Pharmacy Inc."           # still waiting: details updated
    assert client.delete("/api/accounts/hipaa", headers=_h(shop)).json()["status"] is None   # withdrawn
    client.post("/api/accounts/hipaa", headers=_h(shop), json={"organization": "Ko Pharmacy"})
    aid = next(a["id"] for a in account_store._accounts.values() if a["email"] == "ann@shop.com")
    client.post(f"/api/admin/hipaa-requests/{aid}/deny", headers=_h(admin), json={"note": "No NPI found"})
    denied = client.get("/api/accounts/hipaa", headers=_h(shop)).json()
    assert denied["status"] == "denied" and not denied["storing_paused"]
    _store(shop)                                                         # ordinary business data works again
    assert client.get("/api/accounts/audit", headers=_h(shop)).json()["enabled"] is False


def test_beacon_never_sees_email_or_phone_values():
    from api.services.ai_assistant import schema_summary

    df = pd.DataFrame({"store": ["A", "B"] * 10, "customer_email": ["a@x.co", "b@x.co"] * 10,
                       "phone": [4045550101, 4045550102] * 10})
    out = {c["column"]: c for c in schema_summary(df)}
    assert out["store"]["values"] == ["A", "B"]
    assert out["customer_email"] == {"column": "customer_email", "type": "text"}
    assert out["phone"] == {"column": "phone", "type": "text"}            # not even a min / max
