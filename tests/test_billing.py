"""
Payments: plans with monthly + annual prices, Stripe Checkout, the signed
webhook, the billing portal, prepaid consulting hours, and test (placeholder)
mode. Stripe itself is never called: a small fake stands in for its library,
and webhook signatures are made the same way Stripe makes them.
"""

import hashlib
import hmac
import io
import json
import time
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, billing, pricing

client = TestClient(app)
WHSEC = "whsec_test_secret"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._subscriptions,
                  account_store._services, account_store._plans, account_store._consultants):
        store.clear()
    account_store._consulting.clear()
    yield
    get_settings.cache_clear()


def _signup(email="pat@example.com", **extra):
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Pat",
                                                    "last_name": "Lee", "email": email, **extra})
    body = res.json()
    return body["access_token"], body["account"]["id"]


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ── Catalog and consulting prices ────────────────────────────────────────────

def test_every_tool_has_a_monthly_and_a_cheaper_annual_price():
    plans = client.get("/api/billing/plans").json()["plans"]
    tools = {p["id"] for p in plans if p["kind"] == "tool"}
    assert tools == {"data_cleaning", "data_exploration_analysis", "data_visualization", "data_engineering",
                     "data_science_ml", "ai"}
    for p in plans:
        assert 0 < p["annual_usd"] < p["monthly_usd"] * 12
    bundle = next(p for p in plans if p["kind"] == "bundle")
    assert bundle["bundle_savings_month_usd"] > 0          # cheaper than picking every tool
    assert pricing.lookup_key("all_tools", "year") == "dcp_all_tools_year"
    assert pricing.plan_from_lookup_key("dcp_data_science_ml_month") == ("data_science_ml", "month")


def test_build_your_own_package_adds_the_prices():
    res = client.post("/api/billing/price", json={"plan_ids": ["data_visualization", "data_exploration_analysis"],
                                                  "interval": "month"}).json()
    assert res["plan_ids"] == ["data_cleaning", "data_exploration_analysis", "data_visualization"]   # base added
    assert res["total_usd"] == sum(l["amount_usd"] for l in res["lines"]) and not res["bundle_cheaper"]
    # Every tool picked separately costs more than the bundle.
    everything = [p.id for p in pricing.SUBSCRIPTIONS if p.kind == "tool"]
    assert client.post("/api/billing/price", json={"plan_ids": everything, "interval": "month"}).json()["bundle_cheaper"]
    # AI needs Data Engineering, and is free until it launches.
    bad = client.post("/api/billing/price", json={"plan_ids": ["ai"], "interval": "month"})
    assert bad.status_code == 400 and "needs Data Engineering" in bad.json()["detail"]
    ok = client.post("/api/billing/price", json={"plan_ids": ["ai", "data_engineering"], "interval": "year"}).json()
    assert next(l for l in ok["lines"] if l["id"] == "ai")["amount_usd"] == 0
    # The bundle replaces any other picks.
    assert client.post("/api/billing/price", json={"plan_ids": ["all_tools", "ai"]}).json()["plan_ids"] == ["all_tools"]


def test_consultations_are_free_for_30_minutes_except_data_strategy():
    terms = {s["id"]: s["consultation"] for s in client.get("/api/services").json()}
    assert terms["data_visualization"]["free_minutes"] == 30 and terms["data_visualization"]["rate_per_hour_usd"] == 80
    assert terms["data_strategy_discovery"]["free_minutes"] == 0
    assert "Paid consulting" in terms["data_strategy_discovery"]["text"]
    quote = client.post("/api/quote", json={"service_ids": ["data_strategy_discovery"]}).json()
    line = next(l for l in quote["lines"] if l["service_id"] == "data_strategy_discovery")
    assert line["amount_usd"] == 80


# ── Test (placeholder) mode: no Stripe key ───────────────────────────────────

def test_test_mode_turns_a_plan_on_and_off_without_charging():
    token, _ = _signup()
    res = client.post("/api/billing/checkout", headers=_h(token), json={"plan_ids": ["all_tools"], "interval": "year"})
    assert res.json() == {"url": None, "activated": True, "updated": False, "test_mode": True}
    plan = client.get("/api/accounts/services", headers=_h(token)).json()
    assert plan["billing"]["stripe"] is False and plan["downloads_included"]
    assert [s["plan_ids"] for s in plan["subscriptions"]] == [["all_tools"]]
    assert len(plan["active"]) > 1
    # The same package again is refused.
    again = client.post("/api/billing/checkout", headers=_h(token), json={"plan_ids": ["all_tools"], "interval": "year"})
    assert again.status_code == 409
    # Downloads are included in the plan.
    csv = b"customer,amount\n" + b"".join(f"C{i},{i}\n".encode() for i in range(40))
    dl = client.post("/api/files/download", headers=_h(token), files=[("files", ("s.csv", io.BytesIO(csv), "text/csv"))])
    assert dl.headers["X-Amount-USD"] == "0.00"
    # Cancel anytime.
    assert client.post("/api/billing/cancel", headers=_h(token)).status_code == 200
    plan = client.get("/api/accounts/services", headers=_h(token)).json()
    assert plan["subscriptions"] == [] and plan["active"] == ["data_cleaning"] and not plan["downloads_included"]


def test_a_package_unlocks_only_its_tools_and_can_change():
    token, _ = _signup()
    client.post("/api/billing/checkout", headers=_h(token), json={"plan_ids": ["data_visualization"], "interval": "month"})
    plan = client.get("/api/accounts/services", headers=_h(token)).json()
    assert plan["active"] == ["data_cleaning", "data_visualization"]
    assert plan["subscriptions"][0]["total_usd"] == 38.0
    # Add a tool: the same subscription changes (no second subscription).
    res = client.post("/api/billing/checkout", headers=_h(token),
                      json={"plan_ids": ["data_visualization", "data_science_ml"], "interval": "month"}).json()
    assert res["updated"]
    plan = client.get("/api/accounts/services", headers=_h(token)).json()
    assert len(plan["subscriptions"]) == 1 and "data_science_ml" in plan["active"]


def test_consulting_hours_in_test_mode_and_limits():
    token, _ = _signup()
    res = client.post("/api/billing/consulting-hours", headers=_h(token), json={"hours": 2, "service_id": "data_engineering"})
    assert res.json()["activated"]
    consulting = client.get("/api/accounts/services", headers=_h(token)).json()["consulting"]
    assert consulting == {"rate_per_hour_usd": 80.0, "free_minutes": 30, "hours_bought": 2.0}
    assert client.post("/api/billing/consulting-hours", headers=_h(token), json={"hours": 0}).status_code == 422
    assert client.post("/api/billing/portal", headers=_h(token)).status_code == 400   # no portal in test mode


def test_team_members_cant_change_billing():
    owner = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": "ann@example.com",
        "company_name": "Ko Co", "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": "bo@example.com"}],
    }).json()["access_token"]
    member = client.post("/api/accounts/signin", json={"email": "bo@example.com"}).json()["access_token"]
    assert client.post("/api/billing/checkout", headers=_h(member), json={"plan_ids": ["all_tools"], "interval": "month"}).status_code == 403
    assert client.post("/api/billing/checkout", headers=_h(owner), json={"plan_ids": ["all_tools"], "interval": "month"}).status_code == 200
    # ...but they share the owner's plan.
    assert len(client.get("/api/accounts/services", headers=_h(member)).json()["active"]) > 1


# ── With Stripe (a fake library) ─────────────────────────────────────────────

class FakeStripe:
    """Just enough of the stripe library for billing.py, recording what was asked."""

    def __init__(self):
        self.calls = []
        fake = self

        class Price:
            @staticmethod
            def list(lookup_keys, active, limit):
                return NS(data=[NS(id=f"price_{lookup_keys[0]}")])

        class Customer:
            @staticmethod
            def create(**kw):
                fake.calls.append(("customer", kw))
                return NS(id="cus_123")

        class Session:
            @staticmethod
            def create(**kw):
                fake.calls.append(("checkout", kw))
                return NS(url="https://checkout.stripe.com/c/pay/cs_test_1")

        class Portal:
            @staticmethod
            def create(**kw):
                fake.calls.append(("portal", kw))
                return NS(url="https://billing.stripe.com/p/session/1")

        class Subscription:
            @staticmethod
            def modify(sub_id, **kw):
                fake.calls.append(("modify", kw))
                return {"id": sub_id, "customer": "cus_123", "status": "active", "metadata": kw["metadata"],
                        "items": {"data": [{"id": "si_new", "price": {"lookup_key": "dcp_data_science_ml_month"}},
                                           {"id": "si_1", "price": {"lookup_key": "dcp_data_cleaning_month"}}]}}

        import stripe as real

        self.Price, self.Customer, self.Subscription = Price, Customer, Subscription
        self.checkout = NS(Session=Session)
        self.billing_portal = NS(Session=Portal)
        self.Webhook = real.Webhook                       # real signature checking
        self.SignatureVerificationError = real.SignatureVerificationError


@pytest.fixture
def stripe_on(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    get_settings.cache_clear()
    fake = FakeStripe()
    monkeypatch.setattr(billing, "_stripe", lambda: fake)
    return fake


def test_checkout_sends_the_client_to_stripe(stripe_on):
    token, account_id = _signup()
    res = client.post("/api/billing/checkout", headers=_h(token),
                      json={"plan_ids": ["data_visualization", "data_engineering", "ai"], "interval": "year"})
    assert res.json()["url"].startswith("https://checkout.stripe.com/")
    kind, kw = stripe_on.calls[-1]
    assert kind == "checkout" and kw["mode"] == "subscription" and kw["customer"] == "cus_123"
    # One line per billable tool (AI is free until it launches, so it isn't charged).
    assert kw["line_items"] == [{"price": f"price_dcp_{t}_year", "quantity": 1}
                                for t in ("data_cleaning", "data_visualization", "data_engineering")]
    assert kw["subscription_data"]["metadata"] == {"account_id": account_id}
    # Nothing is unlocked until Stripe's webhook says it's paid.
    assert client.get("/api/accounts/services", headers=_h(token)).json()["subscriptions"] == []
    assert client.post("/api/billing/portal", headers=_h(token)).json()["url"].startswith("https://billing.stripe.com/")


def _signed(event: dict, secret=WHSEC):
    payload = json.dumps(event)
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return payload, {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


def _sub_event(kind, account_id, status, tools=("data_cleaning", "data_visualization"), interval="month"):
    return {"id": "evt_1", "object": "event", "type": kind, "data": {"object": {
        "id": "sub_123", "object": "subscription", "customer": "cus_123", "status": status,
        "cancel_at_period_end": False, "metadata": {"account_id": account_id},
        "items": {"data": [{"id": f"si_{t}", "current_period_end": 1893456000,
                            "price": {"lookup_key": f"dcp_{t}_{interval}", "recurring": {"interval": interval}}}
                           for t in tools]},
    }}}


def test_the_webhook_keeps_subscriptions_in_sync(stripe_on):
    token, account_id = _signup()
    payload, headers = _signed(_sub_event("customer.subscription.created", account_id, "active"))
    assert client.post("/api/billing/webhook", content=payload, headers=headers).status_code == 200
    plan = client.get("/api/accounts/services", headers=_h(token)).json()
    [sub] = plan["subscriptions"]
    assert sub["plan_ids"] == ["data_cleaning", "data_visualization"] and sub["current_period_end"] == "2030-01-01"
    assert "data_visualization" in plan["active"]
    # Canceled in Stripe's portal -> switched off here.
    payload, headers = _signed(_sub_event("customer.subscription.deleted", account_id, "canceled"))
    client.post("/api/billing/webhook", content=payload, headers=headers)
    assert client.get("/api/accounts/services", headers=_h(token)).json()["active"] == ["data_cleaning"]


def test_changing_tools_modifies_the_stripe_subscription(stripe_on):
    token, account_id = _signup()
    payload, headers = _signed(_sub_event("customer.subscription.created", account_id, "active"))
    client.post("/api/billing/webhook", content=payload, headers=headers)
    # Swap Dashboards for Data Science & ML, same interval.
    res = client.post("/api/billing/checkout", headers=_h(token),
                      json={"plan_ids": ["data_science_ml"], "interval": "month"}).json()
    assert res["updated"] and res["url"] is None
    kind, kw = stripe_on.calls[-1]
    assert kind == "modify" and kw["proration_behavior"] == "create_prorations"
    assert {"id": "si_data_visualization", "deleted": True} in kw["items"]
    assert {"price": "price_dcp_data_science_ml_month"} in kw["items"]
    assert not any(i.get("id") == "si_data_cleaning" for i in kw["items"])   # the base stays
    assert "data_science_ml" in client.get("/api/accounts/services", headers=_h(token)).json()["active"]


def test_a_fake_webhook_is_rejected(stripe_on):
    _, account_id = _signup()
    payload, headers = _signed(_sub_event("customer.subscription.created", account_id, "active"), secret="wrong")
    assert client.post("/api/billing/webhook", content=payload, headers=headers).status_code == 400
    assert account_store._subscriptions == {}


def test_paid_consulting_hours_are_recorded_once(stripe_on):
    token, account_id = _signup()
    event = {"id": "evt_2", "object": "event", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_9", "object": "checkout.session", "payment_status": "paid", "amount_total": 24000,
        "metadata": {"account_id": account_id, "kind": "consulting_hours", "hours": "3", "service_id": ""}}}}
    for _ in range(2):                                  # Stripe may send an event twice
        payload, headers = _signed(event)
        client.post("/api/billing/webhook", content=payload, headers=headers)
    assert client.get("/api/accounts/services", headers=_h(token)).json()["consulting"]["hours_bought"] == 3.0


# ── scripts/sync_stripe.py ───────────────────────────────────────────────────

def test_sync_creates_products_and_both_prices_once():
    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_stripe", "scripts/sync_stripe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    products, prices = {}, {}

    class Product:
        @staticmethod
        def search(query, limit):
            key = query.split("'")[3]
            return NS(data=[p for p in products.values() if p.metadata["dcp_key"] == key])

        @staticmethod
        def create(name, description, metadata):
            p = NS(id=f"prod_{len(products)}", name=name, description=description, metadata=metadata)
            products[p.id] = p
            return p

        @staticmethod
        def modify(*a, **k):
            pass

    class Price:
        @staticmethod
        def list(lookup_keys, active, limit):
            return NS(data=[p for p in prices.values() if p.lookup_key == lookup_keys[0] and p.active])

        @staticmethod
        def create(product, currency, unit_amount, lookup_key, transfer_lookup_key, recurring=None):
            p = NS(id=f"price_{len(prices)}", product=product, unit_amount=unit_amount, lookup_key=lookup_key,
                   recurring=NS(interval=recurring["interval"]) if recurring else None, active=True)
            prices[p.id] = p
            return p

        @staticmethod
        def modify(pid, active):
            prices[pid].active = active

    fake = NS(Product=Product, Price=Price)
    rows = mod.sync(fake, log=lambda *_: None)
    assert len(products) == len(pricing.SUBSCRIPTIONS) + 1          # + the consulting hour
    assert len(prices) == 2 * len(pricing.SUBSCRIPTIONS) + 1
    assert all(r["stripe_monthly_price_id"] and r["stripe_annual_price_id"] for r in rows)
    mod.sync(fake, log=lambda *_: None)                              # running again changes nothing
    assert len(prices) == 2 * len(pricing.SUBSCRIPTIONS) + 1
