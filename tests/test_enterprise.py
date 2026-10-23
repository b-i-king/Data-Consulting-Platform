"""
Tests for enterprise contracts (api/services/enterprise.py, api/routers/enterprise.py):
columns on the client's account set by an admin; an active contract unlocks its
services without Stripe, counts as paid and raises limits (upload size, team
seats); it stops when the dates say so; the owner accepts the agreement in the app.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, enterprise, entitlements, teams

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")              # small, so the test file can be "too big"
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._subscriptions):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email, **extra):
    body = {"account_type": "business", "first_name": "Ana", "last_name": "Lee", "email": email,
            "company_name": "Big Co", "password": PW, "accept_terms": True, **extra}
    res = client.post("/api/accounts/signup", json=body)
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}, res.json()["account"]["id"]


def _admin():
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Boss", "last_name": "Lee",
                                                    "email": "boss@example.com", "password": PW, "accept_terms": True})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _big_csv(mb: float) -> bytes:
    row = b"2026-10-01,Cake,12.50,Midtown\n"
    return b"date,item,amount,store\n" + row * int(mb * 1024 * 1024 / len(row))


def test_admin_sets_a_contract_that_unlocks_services_and_raises_limits():
    h, aid = _signup("ana@bigco.com")
    admin = _admin()
    url = f"/api/enterprise/admin/{aid}"
    assert client.put(url, headers=h, json={"is_enterprise": True}).status_code == 403          # admins only

    # Before: no package, 1 MB uploads.
    big = [("files", ("sales.csv", _big_csv(1.5), "text/csv"))]
    assert client.post("/api/files/analyze", headers=h, files=big).status_code == 413
    assert "data_visualization" not in client.get("/api/accounts/services", headers=h).json()["active"]

    for bad, why in [({"enterprise_price_usd": -5}, "negative"), ({"enterprise_billing": "weekly"}, "Billing"),
                     ({"enterprise_contract_start": "2026-12-01", "enterprise_contract_end": "2026-11-01"}, "ends before"),
                     ({"enterprise_services": ["nope"]}, "Unknown service"),
                     ({"enterprise_limits": {"max_upload_mb": 999}}, "between 1 and 200")]:
        res = client.put(url, headers=admin, json=bad)
        assert res.status_code == 400 and why in res.json()["detail"], bad

    today = date.today()
    saved = client.put(url, headers=admin, json={
        "is_enterprise": True, "enterprise_price_usd": "24000", "enterprise_billing": "yearly",
        "enterprise_setup_fee_usd": 5000, "enterprise_contract_start": today.isoformat(),
        "enterprise_contract_end": (today + timedelta(days=365)).isoformat(),
        "enterprise_services": ["data_visualization", "data_engineering"],
        "enterprise_limits": {"max_upload_mb": 3, "team_seats": 40}, "enterprise_notes": "Wave invoice INV-1042"}).json()
    assert saved["active"] and saved["price_usd"] == 24000.0 and saved["billing"] == "yearly"
    assert saved["limits"]["max_upload_mb"] == 3 and saved["limits"]["team_seats"] == 40 and saved["notes"] == "Wave invoice INV-1042"
    listed = client.get("/api/enterprise/admin/list", headers=admin).json()["clients"]
    assert [c["name"] for c in listed] == ["Big Co"] and "notes" not in listed[0]                # notes stay private

    # After: the services are on (no Stripe), it counts as paid, the bigger file goes through.
    active = client.get("/api/accounts/services", headers=h).json()["active"]
    assert "data_visualization" in active and "data_engineering" in active
    acc = account_store._accounts[aid]
    assert asyncio.run(entitlements.has_paid_plan(acc)) and asyncio.run(entitlements.downloads_included(acc))
    assert client.post("/api/files/analyze", headers=h, files=big).status_code == 200
    assert enterprise.limits(acc)["team_seats"] == 40 and asyncio.run(teams.seat_limit(None)) == get_settings().team_max_people

    # The contract ended: back to the defaults.
    client.put(url, headers=admin, json={"enterprise_contract_end": (today - timedelta(days=1)).isoformat(),
                                         "enterprise_contract_start": (today - timedelta(days=30)).isoformat()})
    assert not enterprise.is_active(acc)
    assert "data_visualization" not in client.get("/api/accounts/services", headers=h).json()["active"]
    assert client.post("/api/files/analyze", headers=h, files=big).status_code == 413


def test_the_owner_accepts_the_enterprise_agreement_in_the_app():
    h, aid = _signup("ana@bigco.com")
    admin = _admin()
    assert client.post("/api/enterprise/me/accept", headers=h, json={"signer_name": "Ana Lee", "agreed": True}).status_code == 400
    client.put(f"/api/enterprise/admin/{aid}", headers=admin, json={"is_enterprise": True})
    me = client.get("/api/enterprise/me", headers=h).json()
    assert me["is_enterprise"] and me["agreement"]["needs_acceptance"] and "notes" not in me
    assert client.post("/api/enterprise/me/accept", headers=h, json={"signer_name": "Ana Lee", "agreed": False}).status_code == 400
    done = client.post("/api/enterprise/me/accept", headers=h, json={"signer_name": "  Ana   Lee ", "agreed": True}).json()
    assert done["agreement"]["accepted_by"] == "Ana Lee" and not done["agreement"]["needs_acceptance"]
    assert done["agreement"]["accepted_version"] == enterprise.AGREEMENT_VERSION
