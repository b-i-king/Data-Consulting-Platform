"""
The Admin panel's tools: tool prices in one place (dim.plan_prices ->
Stripe -> the whole site), testers (try paid tools free even with Stripe
live), deactivating / reactivating consultants, "modified" timestamps, and the
anonymous record kept when an account is deleted.
(api/services/price_store.py, stripe_catalog.py, billing.set_tester,
consultants.deactivate / reactivate, account_store.deletion_record,
api/routers/admin.py.) Runs on the in-memory store; Stripe is faked.
"""

import re
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, billing, price_store, pricing
from tests.consultant_helpers import apply_as_consultant

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """
    One admin (boss@example.com), Stripe off, a clean memory store and the default prices.

    Args:
        monkeypatch: pytest fixture for environment variables.

    Returns:
        Iterator[None]: yields while the test runs; prices are reset afterwards.
    """
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants,
                  account_store._subscriptions, account_store._deletions, price_store._prices):
        store.clear()
    yield
    pricing.apply_prices(pricing.DEFAULT_PRICES)   # other tests expect the default prices
    get_settings.cache_clear()


def _signup(email, first="Ana", **extra):
    """Sign up an individual; returns (token, account id)."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": first,
                                                    "last_name": "Lee", "email": email, "password": PW,
                                                    "accept_terms": True, **extra})
    assert res.status_code == 201, res.text
    return res.json()["access_token"], res.json()["account"]["id"]


def _h(token, acting_as=None):
    """Authorization header (plus X-Acting-As when working on a client)."""
    return {"Authorization": f"Bearer {token}", **({"X-Acting-As": acting_as} if acting_as else {})}


def _signin(email):
    """A fresh sign-in token (carries the current role)."""
    return client.post("/api/accounts/signin", json={"email": email, "password": PW}).json()["access_token"]


# ── Prices ───────────────────────────────────────────────────────────────────

def test_the_schema_seed_matches_the_default_prices():
    """db/schema.sql seeds dim.plan_prices with exactly pricing.py's defaults (one row per plan)."""
    sql = Path("db/schema.sql").read_text(encoding="utf-8")
    block = sql[sql.index("INSERT INTO dim.plan_prices"):sql.index("ON CONFLICT (plan_id) DO NOTHING")]
    seeded = {pid: (float(m), float(a)) for pid, m, a in re.findall(r"\('(\w+)', ([\d.]+), ([\d.]+)\)", block)}
    assert seeded == pricing.DEFAULT_PRICES


def test_an_admin_changes_a_price_and_the_whole_site_follows():
    """
    PUT /api/admin/prices/{plan}: saved, shown on the public plans list, used in quotes,
    and listed with its default; the rules (annual <= 12 x monthly, Stripe minimum) hold.
    """
    admin, _ = _signup("boss@example.com", "Boss")
    ana, _ = _signup("ana@example.com")
    assert client.get("/api/admin/prices", headers=_h(ana)).status_code == 403
    res = client.put("/api/admin/prices/data_visualization", headers=_h(admin),
                     json={"monthly_usd": 24, "annual_usd": 240.004})
    assert res.status_code == 200, res.text
    row = next(p for p in res.json()["prices"] if p["id"] == "data_visualization")
    assert (row["monthly_usd"], row["annual_usd"], row["default_monthly_usd"]) == (24.0, 240.0, 19.0)
    assert row["updated_by"] == "Boss Lee" and row["updated_at"]
    assert res.json()["stripe"] == {"enabled": False, "changes": []}

    plans = {p["id"]: p for p in client.get("/api/billing/plans").json()["plans"]}
    assert plans["data_visualization"]["monthly_usd"] == 24.0
    quote = client.post("/api/billing/price", json={"plan_ids": ["data_visualization"], "interval": "month"}).json()
    assert 24.0 in [line["amount_usd"] for line in quote["lines"]]

    for bad, code in (({"monthly_usd": 10, "annual_usd": 130}, 400),     # annual > 12 months
                      ({"monthly_usd": 0.2, "annual_usd": 2}, 400),      # below Stripe's $0.50
                      ({"monthly_usd": 50000, "annual_usd": 9}, 400)):   # typo guard
        assert client.put("/api/admin/prices/data_visualization", headers=_h(admin), json=bad).status_code == code
    assert client.put("/api/admin/prices/nope", headers=_h(admin),
                      json={"monthly_usd": 5, "annual_usd": 50}).status_code == 404
    assert pricing.SUBSCRIPTIONS_BY_ID["data_visualization"].monthly_usd == 24.0   # bad tries changed nothing


@pytest.mark.anyio
async def test_load_applies_the_saved_prices():
    """price_store.load() (API startup) swaps the saved prices into pricing.SUBSCRIPTIONS."""
    price_store._prices["data_cleaning"] = {"monthly_usd": 9.0, "annual_usd": 90.0}
    assert await price_store.load() == 1
    assert pricing.SUBSCRIPTIONS_BY_ID["data_cleaning"].annual_usd == 90.0
    assert pricing.SUBSCRIPTIONS_BY_ID["ai"].monthly_usd == pricing.DEFAULT_PRICES["ai"][0]   # untouched


class CatalogStripe:
    """Stripe's Product and Price APIs in memory (enough for stripe_catalog.sync_plan)."""

    def __init__(self, fail=False):
        self.products, self.prices = {}, {}
        fake = self

        class Product:
            @staticmethod
            def search(query, limit):
                key = query.split("'")[3]
                return NS(data=[p for p in fake.products.values() if p.metadata["dcp_key"] == key])

            @staticmethod
            def retrieve(pid):
                if pid not in fake.products:
                    raise RuntimeError("No such product")
                return fake.products[pid]

            @staticmethod
            def create(name, description, metadata, id=None):  # noqa: A002 (Stripe's own name)
                p = NS(id=id or f"prod_{len(fake.products)}", name=name, description=description, metadata=metadata)
                fake.products[p.id] = p
                return p

            @staticmethod
            def modify(*a, **k):
                pass

        class Price:
            @staticmethod
            def list(lookup_keys, active, limit):
                return NS(data=[p for p in fake.prices.values() if p.lookup_key == lookup_keys[0] and p.active])

            @staticmethod
            def create(product, currency, unit_amount, lookup_key, transfer_lookup_key, recurring=None):
                if fail:
                    raise RuntimeError("Stripe is down")
                p = NS(id=f"price_{len(fake.prices)}", product=product, unit_amount=unit_amount, active=True,
                       lookup_key=lookup_key, recurring=NS(interval=recurring["interval"]) if recurring else None)
                fake.prices[p.id] = p
                return p

            @staticmethod
            def modify(pid, active):
                fake.prices[pid].active = active

        self.Product, self.Price = Product, Price


def test_with_stripe_on_a_price_change_updates_stripe_first(monkeypatch):
    """
    Stripe gets a NEW price under the same lookup key and the old one is archived;
    if Stripe refuses, nothing changes anywhere (502), so the site never shows a
    price Stripe wouldn't charge.
    """
    admin, _ = _signup("boss@example.com", "Boss")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    get_settings.cache_clear()
    fake = CatalogStripe()
    monkeypatch.setattr(billing, "_stripe", lambda: fake)
    client.put("/api/admin/prices/ai", headers=_h(admin), json={"monthly_usd": 29, "annual_usd": 290})
    res = client.put("/api/admin/prices/ai", headers=_h(admin), json={"monthly_usd": 35, "annual_usd": 350}).json()
    assert any("replace price dcp_ai_month: $35.00" in c for c in res["stripe"]["changes"])
    live = {p.lookup_key: p.unit_amount for p in fake.prices.values() if p.active}
    assert live == {"dcp_ai_month": 3500, "dcp_ai_year": 35000}
    assert sum(not p.active for p in fake.prices.values()) == 2          # the $29 / $290 prices archived

    monkeypatch.setattr(billing, "_stripe", lambda: CatalogStripe(fail=True))
    refused = client.put("/api/admin/prices/ai", headers=_h(admin), json={"monthly_usd": 40, "annual_usd": 400})
    assert refused.status_code == 502 and "nothing changed" in refused.json()["detail"]
    assert pricing.SUBSCRIPTIONS_BY_ID["ai"].monthly_usd == 35.0 and price_store._prices["ai"]["monthly_usd"] == 35.0


# ── Testers ──────────────────────────────────────────────────────────────────

def test_a_tester_gets_packages_free_even_with_stripe_live(monkeypatch):
    """
    With Stripe on, a tester's checkout switches the package on at once (no Stripe call),
    their workspace says so, downloads are free, and switching them off locks the tools again.
    """
    admin, _ = _signup("boss@example.com", "Boss")
    ana, ana_id = _signup("ana@example.com")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    get_settings.cache_clear()

    def no_stripe():
        raise AssertionError("a tester must never reach Stripe")

    monkeypatch.setattr(billing, "_stripe", no_stripe)
    found = client.get("/api/admin/accounts?q=ana", headers=_h(admin)).json()["accounts"]
    assert [a["email"] for a in found] == ["ana@example.com"] and not found[0]["is_tester"]
    on = client.post(f"/api/admin/accounts/{ana_id}/tester", headers=_h(admin), json={"is_tester": True})
    assert on.status_code == 200 and on.json()["account"]["is_tester"]

    res = client.post("/api/billing/checkout", headers=_h(ana), json={"plan_ids": ["data_visualization"], "interval": "month"})
    assert res.status_code == 200 and res.json()["activated"] and res.json()["test_mode"]
    plan = client.get("/api/accounts/services", headers=_h(ana)).json()
    assert "data_visualization" in plan["active"]
    assert plan["billing"] == {"stripe": False, "tester": True} and plan["downloads_included"]

    off = client.post(f"/api/admin/accounts/{ana_id}/tester", headers=_h(admin), json={"is_tester": False})
    assert not off.json()["account"]["is_tester"]
    after = client.get("/api/accounts/services", headers=_h(ana)).json()
    assert after["active"] == ["data_cleaning"] and after["billing"]["stripe"] is True


def test_tester_rules():
    """Not for admins, not for business colleagues, not while paying through Stripe; admins only."""
    admin, admin_id = _signup("boss@example.com", "Boss")
    ana, ana_id = _signup("ana@example.com")
    assert client.post(f"/api/admin/accounts/{ana_id}/tester", headers=_h(ana), json={"is_tester": True}).status_code == 403
    assert client.post(f"/api/admin/accounts/{admin_id}/tester", headers=_h(admin),
                       json={"is_tester": True}).status_code == 400
    owner = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": "ann@example.com",
        "company_name": "Ko Bakery", "password": PW, "accept_terms": True,
        "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": "bo@example.com"}]}).json()
    bo = next(a for a in account_store._accounts.values() if a["email"] == "bo@example.com")
    assert client.post(f"/api/admin/accounts/{bo['id']}/tester", headers=_h(admin),
                       json={"is_tester": True}).status_code == 400
    assert all(a["email"] != "bo@example.com" for a in
               client.get("/api/admin/accounts", headers=_h(admin)).json()["accounts"])   # owners only
    # Paying through Stripe right now -> cancel first.
    sub = {"id": "s1", "account_id": owner["account"]["id"], "billing_interval": "month", "status": "active",
           "items": [{"plan_id": "data_cleaning"}], "stripe_subscription_id": "sub_123",
           "cancel_at_period_end": False, "current_period_end": None, "created_at": account_store._now()}
    account_store._subscriptions["s1"] = sub
    paying = client.post(f"/api/admin/accounts/{owner['account']['id']}/tester", headers=_h(admin),
                         json={"is_tester": True})
    assert paying.status_code == 409


# ── Consultants: deactivate / reactivate ─────────────────────────────────────

def _kim_with_a_client():
    """The admin (default consultant), Kim (approved) and Sam (Kim's client). Returns tokens and ids."""
    admin, _ = _signup("boss@example.com", "Boss")
    kim_tok, kim_acc = _signup("kim@example.com", "Kim")
    assert apply_as_consultant(client, _h(admin), kim_tok, "kim@example.com").status_code == 200
    client.post(f"/api/admin/consultant-requests/{kim_acc}/approve", headers=_h(admin))
    kim = _signin("kim@example.com")
    kim_id = next(c["id"] for c in client.get("/api/accounts/consultants").json()["consultants"] if c["name"] == "Kim")
    sam, sam_id = _signup("sam@example.com", "Sam", wants_consultant=True, consultant_id=kim_id)
    return admin, kim, kim_id, sam, sam_id


def test_deactivating_a_consultant_moves_their_clients_and_locks_them_out():
    """
    Kim is switched off: her client moves to the default consultant, she can't be picked,
    can't open a client, signs in as a client; reactivating gives her waiting clients.
    """
    admin, kim, kim_id, sam, sam_id = _kim_with_a_client()
    assert client.get("/api/consultant/clients", headers=_h(kim)).status_code == 200
    assert client.post(f"/api/admin/consultants/{kim_id}/deactivate", headers=_h(kim), json={}).status_code == 403

    res = client.post(f"/api/admin/consultants/{kim_id}/deactivate", headers=_h(admin), json={"reason": "On leave"})
    assert res.status_code == 200, res.text
    assert "1 client moved" in res.json()["notice"]
    row = next(c for c in res.json()["team"] if c["id"] == kim_id)
    assert not row["active"] and row["deactivated_at"] and row["deactivation_reason"] == "On leave"
    assert client.get("/api/accounts/me", headers=_h(sam)).json()["consultant_name"] == "Boss"

    assert all(c["name"] != "Kim" for c in client.get("/api/accounts/consultants").json()["consultants"])
    assert client.get("/api/consultant/clients", headers=_h(kim)).status_code == 403
    blocked = client.get("/api/accounts/services", headers=_h(kim, sam_id))
    assert blocked.status_code == 403 and "paused" in blocked.json()["detail"]
    assert client.get("/api/accounts/me", headers=_h(kim)).json()["role"] == "client"
    as_consultant = client.post("/api/accounts/signin", json={"email": "kim@example.com", "password": PW,
                                                              "as_consultant": True})
    assert as_consultant.status_code == 403 and "paused" in as_consultant.json()["detail"]
    assert client.post(f"/api/admin/consultants/{kim_id}/deactivate", headers=_h(admin), json={}).status_code == 409

    # Someone waiting for a consultant goes to Kim when she's back.
    # The admin has room for 3 (Sam is one): fill it, so the next client waits.
    for i in range(2):
        _signup(f"c{i}@example.com", "Cy", wants_consultant=True)
    _, wait_id = _signup("wait@example.com", "Wy", wants_consultant=True)
    assert account_store._accounts[wait_id]["consultant_id"] is None
    back = client.post(f"/api/admin/consultants/{kim_id}/reactivate", headers=_h(admin)).json()
    assert "1 waiting client assigned" in back["notice"]
    kim_row = next(c for c in back["team"] if c["id"] == kim_id)
    assert kim_row["active"] and kim_row["reactivated_at"]
    assert account_store._accounts[wait_id]["consultant_id"] == kim_id


def test_admins_and_the_default_consultant_cant_be_deactivated():
    """The admin's own consultant row (the default consultant) can't be switched off."""
    admin, _ = _signup("boss@example.com", "Boss")
    boss_id = next(c["id"] for c in client.get("/api/accounts/consultants").json()["consultants"])
    res = client.post(f"/api/admin/consultants/{boss_id}/deactivate", headers=_h(admin), json={})
    assert res.status_code == 400
    assert client.post("/api/admin/consultants/nope/deactivate", headers=_h(admin), json={}).status_code == 404


# ── Account deletions: an anonymous count, never who ─────────────────────────

def test_deleting_an_account_keeps_only_an_anonymous_record():
    """
    "Delete my account" keeps one row per account removed (type, role, age, paid, reason)
    with nothing that identifies the person; the Admin panel shows the counts.
    """
    admin, _ = _signup("boss@example.com", "Boss")
    owner = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": "ann@example.com",
        "company_name": "Ko Bakery", "password": PW, "accept_terms": True,
        "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": "bo@example.com"}]}).json()
    t = owner["access_token"]
    client.post("/api/billing/checkout", headers=_h(t), json={"plan_ids": ["data_visualization"], "interval": "month"})
    bad = client.request("DELETE", "/api/accounts/me", headers=_h(t),
                         json={"confirm_email": "ann@example.com", "reason": "my secret reason"})
    assert bad.status_code == 422                                     # only the fixed list
    gone = client.request("DELETE", "/api/accounts/me", headers=_h(t),
                          json={"confirm_email": "ann@example.com", "reason": "too_expensive"})
    assert gone.json() == {"deleted": 2}

    rows = account_store._deletions
    assert sorted((r["role"], r["removed_by"], r["reason"]) for r in rows) == [
        ("team_member", "team_owner", None), ("team_owner", "self", "too_expensive")]
    assert all(r["had_paid_plan"] and r["account_age_days"] == 0 and r["account_type"] == "business" for r in rows)
    text = repr(rows)
    for secret in ("ann@example.com", "Ann", "Ko Bakery", owner["account"]["id"]):
        assert secret not in text                                      # nothing points back to them

    summary = client.get("/api/admin/consultant-requests", headers=_h(admin)).json()["deletions"]
    assert summary["total"] == 2 and summary["recent"] == 2
    assert summary["by_reason"] == {"too_expensive": 1, "none": 1} and summary["by_month"][0]["count"] == 2


def test_accounts_and_consultants_carry_modified_timestamps():
    """New rows start with updated_at; the tester switch and deactivation move it (the database uses a trigger)."""
    admin, _ = _signup("boss@example.com", "Boss")
    _, ana_id = _signup("ana@example.com")
    before = account_store._accounts[ana_id]["updated_at"]
    client.post(f"/api/admin/accounts/{ana_id}/tester", headers=_h(admin), json={"is_tester": True})
    assert account_store._accounts[ana_id]["updated_at"] >= before
    person = client.get("/api/admin/accounts?q=ana", headers=_h(admin)).json()["accounts"][0]
    assert person["updated_at"] and person["created_at"]
    sql = Path("db/schema.sql").read_text(encoding="utf-8")
    assert "CREATE TRIGGER accounts_touch_updated_at" in sql and "CREATE TRIGGER consultants_touch_updated_at" in sql
