"""
Tests for Wave (api/services/business_tools.py Wave section, oauth.py, sync.py,
unified.py flatten_wave_invoice): sign-in with the client keys in the form,
the business picked, the live quick look (no expenses: Wave doesn't share
them), record types, the limits, and the stored sync. Wave's GraphQL API is
faked with httpx.MockTransport.
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, business_tools, entities, entitlements, oauth, unified, warehouse_store

client = TestClient(app)


async def _always_paid(_account):
    return True


@pytest.fixture(autouse=True)
def paid_plan(monkeypatch):
    """Business tools need a paid plan; these tests are about other rules, so every test account counts as paid."""
    monkeypatch.setattr(entitlements, "has_paid_plan", _always_paid)
PW = "Correct-horse-9"
GQL = business_tools.WAVE_GQL


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL", "STRIPE_SECRET_KEY"):
        monkeypatch.setenv(key, "")
    for key, value in {"WAVE_CLIENT_ID": "w-id", "WAVE_CLIENT_SECRET": "w-secret",
                       "PUBLIC_API_URL": "https://api.example.com", "SITE_URL": "https://site.example.com"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants, account_store._connections,
                  account_store._subscriptions, account_store._load_runs, account_store._consents,
                  warehouse_store._stored, entities._entities, entities._links, business_tools._minute_calls):
        store.clear()
    monkeypatch.setattr(account_store, "_api_usage", {})
    yield
    oauth.HTTP_TRANSPORT = None
    get_settings.cache_clear()


INVOICES = [
    {"id": "inv-1", "invoiceNumber": "1001", "invoiceDate": "2026-03-02", "dueDate": "2026-04-01", "status": "PAID",
     "modifiedAt": "2026-03-02T15:00:00Z", "currency": {"code": "USD"}, "total": {"value": "108.00"},
     "amountDue": {"value": "0.00"}, "amountPaid": {"value": "108.00"}, "taxTotal": {"value": "8.00"},
     "customer": {"id": "c1", "name": "Blue Door Cafe", "email": "bd@example.com"},
     "items": [{"description": "Sourdough", "quantity": "2", "unitPrice": "50.00", "subtotal": {"value": "100.00"},
                "total": {"value": "108.00"}, "product": {"id": "p1", "name": "Sourdough"}}]},
    {"id": "inv-2", "invoiceNumber": "1002", "invoiceDate": "2026-04-10", "dueDate": "2026-05-10", "status": "UNPAID",
     "modifiedAt": "2026-04-10T15:00:00Z", "currency": {"code": "USD"}, "total": {"value": "54.00"},
     "amountDue": {"value": "54.00"}, "amountPaid": {"value": "0.00"}, "taxTotal": {"value": "4.00"},
     "customer": {"id": "c2", "name": "Corner Market", "email": None}, "items": []},
    {"id": "inv-3", "invoiceNumber": "1003", "invoiceDate": "2026-04-11", "status": "DRAFT",
     "total": {"value": "999.00"}, "customer": {"id": "c2", "name": "Corner Market"}, "items": []},
]


class FakeWave:
    """Wave's token endpoint and GraphQL API; records every request."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        if url == "https://api.waveapps.com/oauth2/token/":
            form = parse_qs(request.content.decode())
            assert form["client_id"] == ["w-id"] and form["client_secret"] == ["w-secret"]   # keys in the form
            assert "authorization" not in request.headers                                      # not HTTP Basic
            return httpx.Response(200, json={"access_token": "w-a", "refresh_token": "w-r", "expires_in": 7200})
        assert url == GQL and request.headers["authorization"].startswith("Bearer w-a")
        body = json.loads(request.content)
        q, v = body["query"], body["variables"]
        self.calls.append((q, v))
        if "businesses(" in q:
            return httpx.Response(200, json={"data": {"businesses": {"edges": [
                {"node": {"id": "biz-personal", "name": "Me", "isPersonal": True}},
                {"node": {"id": "biz-1", "name": "Sam's Bakery", "isPersonal": False}}]}}})
        for field, rows in (("invoices", INVOICES), ("customers", [{"id": "c1", "name": "Blue Door Cafe"}]),
                            ("products", [])):
            if f"{field}(page:" in q:
                assert v["businessId"] == "biz-1"
                size = v.get("pageSize", 1)
                return httpx.Response(200, json={"data": {"business": {field: {
                    "pageInfo": {"currentPage": 1, "totalPages": 1},
                    "edges": [{"node": r} for r in rows[:size]]}}}})
        return httpx.Response(200, json={"errors": [{"message": "unknown query"}]})


def _signup(email="ana@example.com", engineering=False):
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Ana", "last_name": "Lee",
                                                    "email": email, "password": PW, "accept_terms": True})
    assert res.status_code == 201, res.text
    h = {"Authorization": f"Bearer {res.json()['access_token']}"}
    if engineering:
        client.post("/api/billing/checkout", headers=h, json={"plan_ids": ["data_engineering"], "interval": "month"})
    return h


def _connect(h):
    url = client.get("/api/connections/wave/authorize", headers=h).json()["url"]
    q = parse_qs(urlparse(url).query)
    assert urlparse(url).netloc == "api.waveapps.com"
    assert q["scope"] == ["business:read customer:read invoice:read product:read user:read"]   # read-only
    return client.get("/api/connections/oauth/callback", params={"state": q["state"][0], "code": "c"},
                      follow_redirects=False)


def test_wave_connect_quick_look_and_records():
    fake = FakeWave()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()
    listed = {c["connector"]: c for c in client.get("/api/connections", headers=h).json()["connectors"]}
    assert listed["wave"]["available"] and listed["wave"]["kind"] == "business_tool"
    assert _connect(h).headers["location"].endswith("integration=wave&connected=1")
    row = next(c for c in account_store._connections.values() if c["connector"] == "wave")
    assert row["display_name"] == "Sam's Bakery"                                  # not the personal business

    look = client.get("/api/connections/wave/quick-look?start=2026-01-01&end=2026-06-30", headers=h).json()
    assert look["totals"]["income"] == 150.0 and look["totals"]["owed_to_you"] == 54.0   # before tax; drafts left out
    assert look["totals"]["expenses"] is None and look["totals"]["you_owe"] is None       # Wave doesn't share them
    assert look["top_customers"][0] == {"name": "Blue Door Cafe", "amount": 100.0} and look["estimate"]
    period = next(v for q, v in fake.calls if "invoiceDateStart" in q)
    assert period["invoiceDateStart"] == "2026-01-01" and period["invoiceDateEnd"] == "2026-06-30"

    sets = {d["id"]: d["has_rows"] for d in client.get("/api/connections/wave/datasets", headers=h).json()["datasets"]}
    assert sets == {"invoices": True, "customers": True, "products": False}
    csv = client.get("/api/connections/wave/dataset?id=invoices", headers=h)
    assert csv.status_code == 200 and csv.text.splitlines()[0].startswith("invoice_id,invoice_number,invoice_date")
    assert client.get("/api/connections/wave/dataset?id=products", headers=h).status_code == 422


def test_wave_limits(monkeypatch):
    monkeypatch.setenv("WAVE_MAX_CONNECTIONS", "1")
    monkeypatch.setenv("WAVE_DAILY_CALL_LIMIT", "3")
    get_settings.cache_clear()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(FakeWave())
    ana, bo = _signup("ana@example.com"), _signup("bo@example.com")
    _connect(ana)
    full = client.get("/api/connections/wave/authorize", headers=bo)
    assert full.status_code == 409 and "Wave connections are full" in full.json()["detail"]
    assert client.get("/api/connections/wave/datasets", headers=ana).status_code == 200     # 3 calls
    over = client.get("/api/connections/wave/datasets", headers=ana)
    assert over.status_code == 429 and "Today's Wave allowance" in over.json()["detail"]
    assert business_tools.LIMITS["wave"]["minute"] < 60                            # Wave: ~60 a minute


def test_default_wave_limits_fit_its_daily_app_limit():
    s = get_settings()
    assert s.wave_max_connections * business_tools.daily_budget("wave") <= 0.9 * 5000


def test_wave_invoices_flatten_into_the_unified_model():
    rows = unified.wave_invoices_to_unified(INVOICES)
    assert set(rows["record_id"]) == {"Invoice:inv-1", "Invoice:inv-2"}            # the draft isn't stored
    line = rows[(rows["record_id"] == "Invoice:inv-1") & (rows["line_number"] == 1)]
    assert set(line["field_name"]) >= {"line_amount", "quantity", "unit_price"}
    header = rows[(rows["record_id"] == "Invoice:inv-1") & (rows["line_number"] == 0)]
    assert header["attributes"].iloc[0]["customer_name"] == "Blue Door Cafe"
    assert set(header["field_name"]) >= {"total_amount", "balance_due", "tax_amount"}


def test_wave_stored_sync():
    fake = FakeWave()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup(engineering=True)
    _connect(h)
    res = client.post("/api/connections/wave/sync", headers=h, json={"consent": True})
    assert res.status_code == 200, res.text
    r = res.json()["sync"]
    assert r["complete"] and r["records"] == 2 and r["entities_added"] >= 2
    aid = next(c for c in account_store._connections.values() if c["connector"] == "wave")["account_id"]
    stored = warehouse_store._stored[(aid, "wave", "Wave")]
    assert "customer_link" in stored["attributes"].iloc[0]
    row = next(c for c in account_store._connections.values() if c["connector"] == "wave")
    assert row["sync_state"]["Invoices"].startswith("2026-04-10T15:00:00")


def test_admins_can_connect_their_own_wave_with_a_personal_token(monkeypatch):
    """Wave's free plan: no OAuth (needs Pro), but an admin can paste a personal full access token."""
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("WAVE_CLIENT_ID", "")                     # no Wave app keys needed for this path
    get_settings.cache_clear()
    fake = FakeWave()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    client_h = _signup("ana@example.com")
    refused = client.post("/api/connections/wave/token", headers=client_h, json={"token": "w-a-personal"})
    assert refused.status_code == 403                             # clients use Connect Wave (Wave's rules)

    admin_h = _signup("boss@example.com")
    res = client.post("/api/connections/wave/token", headers=admin_h, json={"token": "w-a-personal-123"})
    assert res.status_code == 200, res.text
    wave = next(c for c in res.json()["connectors"] if c["connector"] == "wave")
    assert wave["status"] == "connected" and wave["display_name"] == "Sam's Bakery"
    row = next(c for c in account_store._connections.values() if c["connector"] == "wave")
    assert "w-a-personal-123" not in row["credentials_encrypted"]                 # saved encrypted
    look = client.get("/api/connections/wave/quick-look?start=2026-01-01&end=2026-06-30", headers=admin_h)
    assert look.status_code == 200 and look.json()["totals"]["income"] == 150.0   # no refresh needed
