"""
Tests for "Connect Square" (api/services/business_tools.py, Square section):
read-only sign-in (JSON token request, the sandbox by default), the quick look
from completed orders, orders opened one row per item sold, stock on hand, and
the paid-plan rule. Square is faked (httpx.MockTransport).
"""

from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from api.core.config import get_settings
from api.services import account_store, business_tools, entitlements, oauth
from tests.test_oauth_integrations import FakeProviders, _connect, _signup, client

SB = "https://connect.squareupsandbox.com"


async def _paid(_account):
    return True


@pytest.fixture(autouse=True)
def square_keys(monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(k, "")
    monkeypatch.delenv("SQUARE_ENVIRONMENT", raising=False)          # not set: Square's sandbox
    for k, v in {"SQUARE_CLIENT_ID": "sq-id", "SQUARE_CLIENT_SECRET": "sq-secret",
                 "PUBLIC_API_URL": "https://api.example.com", "SITE_URL": "https://site.example.com"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    monkeypatch.setattr(entitlements, "has_paid_plan", _paid)
    for store in (account_store._accounts, account_store._connections, business_tools._minute_calls):
        store.clear()
    yield
    oauth.HTTP_TRANSPORT = None
    get_settings.cache_clear()


ORDERS = [
    {"id": "o1", "created_at": "2026-09-03T15:00:00Z", "location_id": "L1", "customer_id": "c1",
     "total_money": {"amount": 2350, "currency": "USD"}, "total_tax_money": {"amount": 150, "currency": "USD"},
     "line_items": [{"name": "Sourdough Loaf", "variation_name": "Large", "quantity": "2",
                     "base_price_money": {"amount": 700}, "gross_sales_money": {"amount": 1400},
                     "total_money": {"amount": 1400}},
                    {"name": "Latte", "variation_name": "12 oz", "quantity": "2", "base_price_money": {"amount": 400},
                     "gross_sales_money": {"amount": 800}, "total_money": {"amount": 800}}]},
    {"id": "o2", "created_at": "2026-10-01T09:00:00Z", "location_id": "L1",
     "total_money": {"amount": 500, "currency": "USD"}, "total_tax_money": {"amount": 0, "currency": "USD"},
     "line_items": [{"name": "Croissant", "quantity": "1", "base_price_money": {"amount": 500},
                     "total_money": {"amount": 500}}]},
]


def _square() -> FakeProviders:
    fake = FakeProviders({})

    def token(request: httpx.Request):
        body = json.loads(request.content)                     # Square wants JSON, with the secret in it
        assert body["client_id"] == "sq-id" and body["client_secret"] == "sq-secret"
        return {"access_token": "sq-access", "refresh_token": "sq-refresh", "expires_at": "2026-11-20T00:00:00Z",
                "merchant_id": "M1"}

    def search(request: httpx.Request):
        body = json.loads(request.content)
        assert request.headers["square-version"] and body["location_ids"] == ["L1"]
        return {"orders": ORDERS[:body.get("limit", 500)]}

    fake.routes.update({
        ("POST", f"{SB}/oauth2/token"): token,
        ("GET", f"{SB}/v2/merchants/me"): {"merchant": {"id": "M1", "business_name": "Sunrise Bakery", "currency": "USD"}},
        ("GET", f"{SB}/v2/locations"): {"locations": [{"id": "L1", "status": "ACTIVE"}, {"id": "L2", "status": "INACTIVE"}]},
        ("POST", f"{SB}/v2/orders/search"): search,
        ("POST", f"{SB}/v2/customers/bulk-retrieve"): {"responses": {"c1": {"customer": {"given_name": "Ana", "family_name": "Lee"}}}},
        ("GET", f"{SB}/v2/catalog/list"): {"objects": [{"id": "I1", "item_data": {"name": "Sourdough Loaf", "variations": [
            {"id": "V1", "item_variation_data": {"name": "Large", "sku": "SD-L", "price_money": {"amount": 700}}}]}}]},
        ("POST", f"{SB}/v2/inventory/counts/batch-retrieve"): {"counts": [
            {"catalog_object_id": "V1", "location_id": "L1", "quantity": "14", "calculated_at": "2026-10-20T00:00:00Z"}]},
        ("GET", f"{SB}/v2/payments"): {"payments": []},
        ("GET", f"{SB}/v2/customers"): {"customers": [{"id": "c1", "given_name": "Ana"}]},
    })
    return fake


def test_connect_square_quick_look_and_open_orders_and_stock():
    fake = _square()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()
    url = client.get("/api/connections/square/authorize", headers=h).json()["url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith(f"{SB}/oauth2/authorize") and q["session"] == ["false"]          # sandbox by default
    assert "ORDERS_READ" in q["scope"][0] and "WRITE" not in q["scope"][0]                 # read-only
    assert _connect(h, "square").headers["location"].endswith("connected=1")
    row = next(v for k, v in account_store._connections.items() if k[1] == "square")
    creds = oauth.unpack(row["credentials_encrypted"])
    assert row["display_name"] == "Sunrise Bakery" and creds["extra"]["merchant_id"] == "M1"
    assert creds["tokens"]["expires_at"] > 1_790_000_000                                  # Square's ISO date read

    look = client.get("/api/connections/square/quick-look?start=2026-09-01&end=2026-10-16", headers=h).json()
    assert look["totals"]["income"] == 27.0 and look["totals"]["owed_to_you"] == 0.0     # (23.50 - 1.50) + 5.00
    assert look["top_customers"] == [{"name": "Ana Lee", "amount": 22.0}]
    assert [m["month"] for m in look["monthly"]] == ["2026-09", "2026-10"]

    sets = {d["id"]: d["has_rows"] for d in client.get("/api/connections/square/datasets", headers=h).json()["datasets"]}
    assert sets["orders"] and sets["inventory"] and not sets["payments"]
    orders = client.get("/api/connections/square/dataset?id=orders", headers=h)
    lines = orders.text.splitlines()
    assert orders.status_code == 200 and orders.headers["X-Rows"] == "3"                   # one row per item sold
    assert "product" in lines[0] and "quantity" in lines[0] and "Sourdough Loaf" in lines[1]
    stock = client.get("/api/connections/square/dataset?id=inventory", headers=h).text.splitlines()
    assert "on_hand" in stock[0] and "Sourdough Loaf" in stock[1] and ",14.0," in stock[1]


def test_square_needs_a_paid_plan(monkeypatch):
    async def free(_account):
        return False

    monkeypatch.setattr(entitlements, "has_paid_plan", free)
    res = client.get("/api/connections/square/authorize", headers=_signup())
    assert res.status_code == 402 and "Square" in res.json()["detail"]
