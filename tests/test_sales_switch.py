"""
Tests for the "sales open / closed" switch (api/services/sales.py).

While closed: customers can't check out (tools, bundles, consultant help) — not
even in test mode, where checkout would switch plans on for free; admins and
testers still can; the admin flips it with no redeploy.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, sales

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def closed(monkeypatch):
    """One admin (boss@example.com), Stripe off (test mode), empty stores, and sales CLOSED (never set)."""
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.delenv("SALES_OPEN", raising=False)   # not set: the default (closed)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._subscriptions):
        store.clear()
    account_store._settings.pop("sales_open", None)   # undo conftest's "open": never set = the default
    sales.forget()
    yield
    get_settings.cache_clear()


def _signup(email: str) -> dict:
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Ana",
                                                    "last_name": "Lee", "email": email, "password": PW,
                                                    "accept_terms": True})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _buy(h):
    return client.post("/api/billing/checkout", headers=h, json={"plan_ids": ["data_cleaning"], "interval": "month"})


def test_closed_by_default_customers_cannot_buy_not_even_free_in_test_mode():
    ana = _signup("ana@example.com")
    status_ = client.get("/api/billing/sales", headers=ana).json()
    assert status_ == {"open": False, "can_buy": False, "message": sales.SALES_CLOSED}
    res = _buy(ana)
    assert res.status_code == 403 and "beta" in res.json()["detail"]
    assert client.get("/api/accounts/services", headers=ana).json()["subscriptions"] == []   # nothing switched on
    consult = client.post("/api/billing/consulting/checkout", headers=ana,
                          json={"kind": "setup", "accept_terms": True})
    assert consult.status_code in (403, 400, 404, 409) and consult.status_code != 200
    assert client.get("/api/billing/sales").json()["can_buy"] is False                         # a guest


def test_testers_can_still_buy_and_the_admin_opens_sales_without_a_redeploy():
    admin = _signup("boss@example.com")
    ana, bo = _signup("ana@example.com"), _signup("bo@example.com")
    ana_id = client.get("/api/accounts/me", headers=ana).json()["id"]
    account_store._accounts[ana_id]["is_tester"] = True
    assert client.get("/api/billing/sales", headers=ana).json()["can_buy"] is True
    assert _buy(ana).status_code == 200                                    # a tester tries the flow
    assert _buy(bo).status_code == 403

    assert client.post("/api/admin/sales", headers=bo, json={"open": True}).status_code == 403
    assert client.post("/api/admin/sales", headers=admin, json={"open": "yes"}).status_code == 400
    assert client.post("/api/admin/sales", headers=admin, json={"open": True}).json() == {"open": True}
    assert client.get("/api/billing/sales").json()["open"] is True
    assert _buy(bo).status_code == 200                                     # open: anyone can buy
    client.post("/api/admin/sales", headers=admin, json={"open": False})
    assert client.get("/api/billing/sales").json()["open"] is False
