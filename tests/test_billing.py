"""
Payments: plans with monthly + annual prices, Stripe Checkout, the signed
webhook, the billing portal, consulting (booked on Cal.com, not sold here),
and test (placeholder) mode. Stripe itself is never called: a small fake stands in for its library,
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
from api.services import account_store, billing, pricing, warehouse_store

client = TestClient(app)
WHSEC = "whsec_test_secret"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """
    Give every test a clean, Stripe-free starting point (runs automatically for each test).

    Turns Stripe off (empty STRIPE_SECRET_KEY, so billing runs in test/placeholder mode),
    sets a known webhook signing secret, clears the cached settings, and empties the
    in-memory account store (accounts, teams, subscriptions, consultants). Settings are
    cleared again after the test.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.

    Returns:
        Iterator[None]: yields once while the test runs (setup before, cleanup after).
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._subscriptions,
                  account_store._consultants):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email="pat@example.com", **extra):
    """
    Sign up an individual account through the API and return its token and id.

    Args:
        email: Email for the new account (default "pat@example.com"); must be unused.
        **extra: Extra signup fields merged into the JSON body (e.g. company_name).

    Returns:
        tuple[str, str]: (access_token, account_id).

    Example:
        >>> token, account_id = _signup()
        >>> client.get("/api/accounts/services", headers=_h(token)).status_code
        200
    """
    res = client.post("/api/accounts/signup", json={"password": "Correct-horse-9", "accept_terms": True, "account_type": "individual", "first_name": "Pat",
                                                    "last_name": "Lee", "email": email, **extra})
    body = res.json()
    return body["access_token"], body["account"]["id"]


def _h(token):
    """
    Build the Authorization header for a signed-in request.

    Args:
        token: The access token from signup or sign-in.

    Returns:
        dict[str, str]: {"Authorization": "Bearer <token>"}.

    Example:
        >>> _h("abc")
        {'Authorization': 'Bearer abc'}
    """
    return {"Authorization": f"Bearer {token}"}


# ── Catalog and consulting prices ────────────────────────────────────────────

def test_every_tool_has_a_monthly_and_a_cheaper_annual_price():
    """
    Check the plan catalog: the six tools, every annual price below 12 monthly payments, a
    bundle cheaper than buying every tool, and lookup keys that round-trip to (plan, interval).
    """
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
    """
    Check /api/billing/price: the Data Cleaning base is added, the total is the sum of the lines,
    AI needs Data Engineering (and is $0 until launch), and the bundle replaces other picks.
    """
    res = client.post("/api/billing/price", json={"plan_ids": ["data_visualization", "data_exploration_analysis"],
                                                  "interval": "month"}).json()
    assert res["plan_ids"] == ["data_cleaning", "data_exploration_analysis", "data_visualization"]   # base added
    assert res["total_usd"] == sum(l["amount_usd"] for l in res["lines"])
    # $57 one by one — the Growth bundle covers all three for less, so it's suggested.
    assert res["bundle_cheaper"] and res["cheaper_bundle"]["id"] == "growth"
    # Every tool picked separately costs more than the bundle.
    everything = [p.id for p in pricing.SUBSCRIPTIONS if p.kind == "tool"]
    assert client.post("/api/billing/price", json={"plan_ids": everything, "interval": "month"}).json()["bundle_cheaper"]
    # AI needs Data Engineering, and (launched) is charged at its own price.
    bad = client.post("/api/billing/price", json={"plan_ids": ["ai"], "interval": "month"})
    assert bad.status_code == 400 and "needs Data Engineering" in bad.json()["detail"]
    ok = client.post("/api/billing/price", json={"plan_ids": ["ai", "data_engineering"], "interval": "year"}).json()
    assert next(l for l in ok["lines"] if l["id"] == "ai")["amount_usd"] == pricing.SUBSCRIPTIONS_BY_ID["ai"].annual_usd
    # The bundle replaces any other picks.
    assert client.post("/api/billing/price", json={"plan_ids": ["all_tools", "ai"]}).json()["plan_ids"] == ["all_tools"]


def test_consultations_are_free_for_30_minutes_except_data_strategy():
    """
    Check consultation terms: 30 free minutes at $80/hour for most services, but Data Strategy &
    Discovery is paid from the first minute (and its quote line is $80).
    """
    terms = {s["id"]: s["consultation"] for s in client.get("/api/services").json()}
    assert terms["data_visualization"]["free_minutes"] == 30 and terms["data_visualization"]["rate_per_hour_usd"] == 80
    assert terms["data_strategy_discovery"]["free_minutes"] == 0
    assert "Paid consulting" in terms["data_strategy_discovery"]["text"]
    quote = client.post("/api/quote", json={"service_ids": ["data_strategy_discovery"]}).json()
    line = next(l for l in quote["lines"] if l["service_id"] == "data_strategy_discovery")
    assert line["amount_usd"] == 80


# ── Test (placeholder) mode: no Stripe key ───────────────────────────────────

def test_test_mode_turns_a_plan_on_and_off_without_charging():
    """
    Check test mode (no Stripe key): checkout activates a plan immediately with no charge, the same
    package again is 409, downloads are $0.00 while subscribed, and cancel returns to Data Cleaning only.
    """
    token, _ = _signup()
    res = client.post("/api/billing/checkout", headers=_h(token), json={"plan_ids": ["all_tools"], "interval": "year"})
    assert res.json() == {"url": None, "activated": True, "updated": False, "test_mode": True,
                          "trial_days": 0, "sale": None}   # no trial or sale switched on
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
    """
    Check that a one-tool package unlocks only that tool (plus the base) at its monthly total, and
    that adding a tool updates the same subscription instead of creating a second one.
    """
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


def test_consulting_is_booked_on_calcom_not_bought_here():
    """
    Check that consulting hours are not sold through the API (they're paid on Cal.com),
    the free first call is reported, and the billing portal is unavailable (400) without Stripe.
    """
    token, _ = _signup()
    assert client.post("/api/billing/consulting-hours", headers=_h(token), json={"hours": 2}).status_code == 404
    consulting = client.get("/api/accounts/services", headers=_h(token)).json()["consulting"]
    assert {k: consulting[k] for k in ("rate_per_hour_usd", "free_minutes")} == \
        {"rate_per_hour_usd": 80.0, "free_minutes": 30}
    assert "hours_bought" not in consulting and consulting["free_used"] is False
    assert client.post("/api/billing/portal", headers=_h(token)).status_code == 400   # no portal in test mode

def test_team_members_cant_change_billing():
    """
    Check that a business team member gets 403 from checkout while the owner succeeds, and that the
    member still shares the owner's active plan.
    """
    owner = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "accept_terms": True, "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": "ann@example.com",
        "company_name": "Ko Co", "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": "bo@example.com"}],
    }).json()["access_token"]
    member = client.post("/api/accounts/signin", json={"email": "bo@example.com", "password": "Correct-horse-9"}).json()["access_token"]
    assert client.post("/api/billing/checkout", headers=_h(member), json={"plan_ids": ["all_tools"], "interval": "month"}).status_code == 403
    assert client.post("/api/billing/checkout", headers=_h(owner), json={"plan_ids": ["all_tools"], "interval": "month"}).status_code == 200
    # ...but they share the owner's plan.
    assert len(client.get("/api/accounts/services", headers=_h(member)).json()["active"]) > 1


# ── With Stripe (a fake library) ─────────────────────────────────────────────

class FakeStripe:
    """Just enough of the stripe library for billing.py, recording what was asked."""

    def __init__(self):
        """
        Build the fake library: Price, Customer, Subscription, checkout.Session and billing_portal.Session.

        Each fake records the calls billing.py makes in `self.calls`, as (kind, kwargs) tuples.
        Webhook signature checking uses the REAL stripe.Webhook, so signatures are verified
        exactly as in production.

        Returns:
            None.
        """
        self.calls = []
        fake = self

        class Price:
            @staticmethod
            def list(lookup_keys, active, limit):
                """
                Fake stripe.Price.list: return one price whose id is "price_" + the lookup key.

                Args:
                    lookup_keys: List with one lookup key, e.g. ["dcp_data_cleaning_month"].
                    active: Ignored (Stripe filter for active prices).
                    limit: Ignored (Stripe page size).

                Returns:
                    SimpleNamespace: An object with `.data` = [one price with `.id`].
                """
                return NS(data=[NS(id=f"price_{lookup_keys[0]}")])

        class Customer:
            @staticmethod
            def create(**kw):
                """
                Fake stripe.Customer.create: record the call and return customer "cus_123".

                Args:
                    **kw: The customer fields billing.py sends (email, name, metadata, ...).

                Returns:
                    SimpleNamespace: An object with `.id` = "cus_123".
                """
                fake.calls.append(("customer", kw))
                return NS(id="cus_123")

        class Session:
            @staticmethod
            def create(**kw):
                """
                Fake stripe.checkout.Session.create: record the call and return a Checkout URL.

                Args:
                    **kw: The Checkout Session options billing.py sends (mode, customer, line_items, ...).

                Returns:
                    SimpleNamespace: An object with `.url` pointing at checkout.stripe.com.
                """
                fake.calls.append(("checkout", kw))
                return NS(url="https://checkout.stripe.com/c/pay/cs_test_1")

        class Portal:
            @staticmethod
            def create(**kw):
                """
                Fake stripe.billing_portal.Session.create: record the call and return a portal URL.

                Args:
                    **kw: The portal options billing.py sends (customer, return_url).

                Returns:
                    SimpleNamespace: An object with `.url` pointing at billing.stripe.com.
                """
                fake.calls.append(("portal", kw))
                return NS(url="https://billing.stripe.com/p/session/1")

        class Subscription:
            @staticmethod
            def modify(sub_id, **kw):
                """
                Fake stripe.Subscription.modify: record the call and return the changed subscription.

                The returned subscription always holds Data Science & ML plus Data Cleaning (monthly),
                which is what test_changing_tools_modifies_the_stripe_subscription asks for.

                Args:
                    sub_id: The Stripe subscription id being changed.
                    **kw: The changes billing.py sends (items, metadata, proration_behavior, ...);
                        `metadata` is required and echoed back.

                Returns:
                    dict: A subscription shaped like Stripe's JSON (id, customer, status, metadata, items).
                """
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
    """
    Turn "real" Stripe mode on, backed by FakeStripe instead of the network.

    Sets a test secret key, clears cached settings, and makes billing._stripe() return the fake.

    Args:
        monkeypatch: pytest fixture for setting environment variables and attributes for one test.

    Returns:
        FakeStripe: The fake, so the test can inspect `.calls`.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    get_settings.cache_clear()
    fake = FakeStripe()
    monkeypatch.setattr(billing, "_stripe", lambda: fake)
    return fake


def test_checkout_sends_the_client_to_stripe(stripe_on, monkeypatch):
    """
    Check that checkout with Stripe on returns a Stripe Checkout URL with one line per billable tool
    (AI is free, so not charged), unlocks nothing until the webhook, and that the portal works.

    Args:
        stripe_on: Fixture that installs FakeStripe and returns it.
        monkeypatch: pytest fixture; marks the account as having stored data (AI needs it,
            tests/test_warehouse.py checks that rule).
    """
    async def has_data(_holder):
        return True

    monkeypatch.setattr(warehouse_store, "has_data", has_data)
    token, account_id = _signup()
    res = client.post("/api/billing/checkout", headers=_h(token),
                      json={"plan_ids": ["data_visualization", "data_engineering", "ai"], "interval": "year"})
    assert res.json()["url"].startswith("https://checkout.stripe.com/")
    kind, kw = stripe_on.calls[-1]
    assert kind == "checkout" and kw["mode"] == "subscription" and kw["customer"] == "cus_123"
    # One line per billable tool (AI is launched, so it's charged like the others).
    assert kw["line_items"] == [{"price": f"price_dcp_{t}_year", "quantity": 1}
                                for t in ("data_cleaning", "data_visualization", "data_engineering", "ai")]
    assert kw["subscription_data"]["metadata"] == {"account_id": account_id, "free_plans": ""}
    # Nothing is unlocked until Stripe's webhook says it's paid.
    assert client.get("/api/accounts/services", headers=_h(token)).json()["subscriptions"] == []
    assert client.post("/api/billing/portal", headers=_h(token)).json()["url"].startswith("https://billing.stripe.com/")
    # Stripe sends the client back to Plan & billing in the workspace (web/lib/routes.ts).
    assert kw["success_url"].endswith("/workspace/settings/billing?checkout=success")
    assert kw["cancel_url"].endswith("/workspace/settings/billing?checkout=canceled")
    assert stripe_on.calls[-1][1]["return_url"].endswith("/workspace/settings/billing")


def _signed(event: dict, secret=WHSEC):
    """
    Sign a webhook event the same way Stripe does, so the real signature check accepts it.

    The signature is HMAC-SHA256 of "<timestamp>.<payload>" with the webhook secret, sent in a
    "Stripe-Signature: t=<timestamp>,v1=<hex>" header.

    Args:
        event: The event as a dict (it is JSON-encoded here).
        secret: Signing secret (default WHSEC, the one the app expects); pass a wrong one to
            fake a forged event.

    Returns:
        tuple[str, dict]: (JSON payload, request headers including Stripe-Signature).

    Example:
        >>> payload, headers = _signed({"id": "evt_1", "type": "ping"})
        >>> headers["Stripe-Signature"].startswith("t=")
        True
    """
    payload = json.dumps(event)
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.{payload}".encode(), hashlib.sha256).hexdigest()
    return payload, {"Stripe-Signature": f"t={ts},v1={sig}", "Content-Type": "application/json"}


def _sub_event(kind, account_id, status, tools=("data_cleaning", "data_visualization"), interval="month"):
    """
    Build a Stripe "customer.subscription.*" webhook event for one account.

    The subscription (id "sub_123", customer "cus_123") has one item per tool, each priced by
    lookup key "dcp_<tool>_<interval>" and renewing on 2030-01-01 (timestamp 1893456000).

    Args:
        kind: Event type, e.g. "customer.subscription.created" or "customer.subscription.deleted".
        account_id: Our account id, stored in the subscription metadata.
        status: Stripe subscription status, e.g. "active" or "canceled".
        tools: Plan ids to put on the subscription (default Data Cleaning + Dashboards).
        interval: "month" or "year" (default "month").

    Returns:
        dict: The event shaped like Stripe's JSON, ready for _signed().

    Example:
        >>> _sub_event("customer.subscription.created", "acct-1", "active")["data"]["object"]["status"]
        'active'
    """
    return {"id": "evt_1", "object": "event", "type": kind, "data": {"object": {
        "id": "sub_123", "object": "subscription", "customer": "cus_123", "status": status,
        "cancel_at_period_end": False, "metadata": {"account_id": account_id},
        "items": {"data": [{"id": f"si_{t}", "current_period_end": 1893456000,
                            "price": {"lookup_key": f"dcp_{t}_{interval}", "recurring": {"interval": interval}}}
                           for t in tools]},
    }}}


def test_the_webhook_keeps_subscriptions_in_sync(stripe_on):
    """
    Check that a signed "subscription created" webhook activates the plan (with its renewal date)
    and a "subscription deleted" webhook switches it back off.

    Args:
        stripe_on: Fixture that installs FakeStripe and returns it.
    """
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
    """
    Check that changing tools on an existing Stripe subscription modifies it in place with
    prorations: the old tool's item is deleted, the new price added, the base left alone.

    Args:
        stripe_on: Fixture that installs FakeStripe and returns it.
    """
    token, account_id = _signup()
    payload, headers = _signed(_sub_event("customer.subscription.created", account_id, "active"))
    client.post("/api/billing/webhook", content=payload, headers=headers)
    # Swap Dashboards for Data Science & ML, same interval.
    res = client.post("/api/billing/checkout", headers=_h(token),
                      json={"plan_ids": ["data_science_ml"], "interval": "month"}).json()
    assert res["updated"] and res["url"] is None
    kind, kw = stripe_on.calls[-1]
    assert kind == "modify" and kw["proration_behavior"] == "always_invoice"
    assert {"id": "si_data_visualization", "deleted": True} in kw["items"]
    assert {"price": "price_dcp_data_science_ml_month"} in kw["items"]
    assert not any(i.get("id") == "si_data_cleaning" for i in kw["items"])   # the base stays
    assert "data_science_ml" in client.get("/api/accounts/services", headers=_h(token)).json()["active"]


def test_a_fake_webhook_is_rejected(stripe_on):
    """
    Check that a webhook signed with the wrong secret is rejected (400) and changes nothing.

    Args:
        stripe_on: Fixture that installs FakeStripe and returns it.
    """
    _, account_id = _signup()
    payload, headers = _signed(_sub_event("customer.subscription.created", account_id, "active"), secret="wrong")
    assert client.post("/api/billing/webhook", content=payload, headers=headers).status_code == 400
    assert account_store._subscriptions == {}


# ── scripts/sync_stripe.py ───────────────────────────────────────────────────

def test_sync_creates_products_and_both_prices_once():
    """
    Check scripts/sync_stripe.py against an in-memory fake Stripe: one product per plan plus the
    consulting hour, a monthly and an annual price per plan, and a second run creates nothing new.
    """
    import importlib.util
    spec = importlib.util.spec_from_file_location("sync_stripe", "scripts/sync_stripe.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    products, prices = {}, {}

    class Product:
        @staticmethod
        def search(query, limit):
            """
            Fake stripe.Product.search: find products by the dcp_key in the metadata query.

            Args:
                query: Stripe search query like "metadata['dcp_key']:'ai'"; the key is the 4th quoted part.
                limit: Ignored (Stripe page size).

            Returns:
                SimpleNamespace: An object with `.data` = matching products.
            """
            key = query.split("'")[3]
            return NS(data=[p for p in products.values() if p.metadata["dcp_key"] == key])

        @staticmethod
        def retrieve(pid):
            """Fake stripe.Product.retrieve: the product with this id, or an error like Stripe's."""
            if pid not in products:
                raise RuntimeError("No such product")
            return products[pid]

        @staticmethod
        def create(name, description, metadata, id=None):  # noqa: A002 (Stripe's own name)
            """
            Fake stripe.Product.create: store a new product with our id (or "prod_<n>").

            Args:
                name: Product name.
                description: Product description (or None).
                metadata: Product metadata, including "dcp_key".

            Returns:
                SimpleNamespace: The new product (id, name, description, metadata).
            """
            p = NS(id=id or f"prod_{len(products)}", name=name, description=description, metadata=metadata)
            products[p.id] = p
            return p

        @staticmethod
        def modify(*a, **k):
            """
            Fake stripe.Product.modify: accept and ignore product updates.

            Args:
                *a: Positional arguments (the product id).
                **k: Fields to change (name, description).

            Returns:
                None.
            """
            pass

    class Price:
        @staticmethod
        def list(lookup_keys, active, limit):
            """
            Fake stripe.Price.list: return active prices with this lookup key.

            Args:
                lookup_keys: List with one lookup key.
                active: Ignored; only active prices are ever returned.
                limit: Ignored (Stripe page size).

            Returns:
                SimpleNamespace: An object with `.data` = matching active prices.
            """
            return NS(data=[p for p in prices.values() if p.lookup_key == lookup_keys[0] and p.active])

        @staticmethod
        def create(product, currency, unit_amount, lookup_key, transfer_lookup_key, recurring=None):
            """
            Fake stripe.Price.create: store a new active price with id "price_<n>".

            Args:
                product: Product id the price belongs to.
                currency: Currency code (always "usd" here).
                unit_amount: Amount in cents.
                lookup_key: Our lookup key for the price.
                transfer_lookup_key: Ignored (Stripe moves the key off the old price).
                recurring: {"interval": "month" | "year"} for subscriptions, None for one-time prices.

            Returns:
                SimpleNamespace: The new price (id, product, unit_amount, lookup_key, recurring, active).
            """
            p = NS(id=f"price_{len(prices)}", product=product, unit_amount=unit_amount, lookup_key=lookup_key,
                   recurring=NS(interval=recurring["interval"]) if recurring else None, active=True)
            prices[p.id] = p
            return p

        @staticmethod
        def modify(pid, active):
            """
            Fake stripe.Price.modify: set a price active or archived.

            Args:
                pid: Price id.
                active: New active flag (False archives the price).

            Returns:
                None.
            """
            prices[pid].active = active

    fake = NS(Product=Product, Price=Price)
    rows = mod.sync(fake, log=lambda *_: None)
    assert len(products) == len(pricing.SUBSCRIPTIONS)              # consulting is paid on Cal.com, not Stripe
    assert len(prices) == 2 * len(pricing.SUBSCRIPTIONS)
    assert all(r["stripe_monthly_price_id"] and r["stripe_annual_price_id"] for r in rows)
    mod.sync(fake, log=lambda *_: None)                              # running again changes nothing
    assert len(prices) == 2 * len(pricing.SUBSCRIPTIONS)


def test_running_the_sync_twice_quickly_makes_no_duplicates():
    """
    Stripe's search can lag about a minute behind; products have our own fixed ids
    ("dcp_<plan>") and are looked up by id, so a second run right away finds them.
    """
    from api.services import stripe_catalog

    products, prices = {}, {}

    class Product:
        @staticmethod
        def retrieve(pid):
            if pid not in products:
                raise RuntimeError("No such product")
            return products[pid]

        @staticmethod
        def search(query, limit):
            return NS(data=[])            # lagging search: never finds anything yet

        @staticmethod
        def create(name, description, metadata, id=None):  # noqa: A002
            products[id] = NS(id=id, name=name, description=description, metadata=metadata)
            return products[id]

        @staticmethod
        def modify(*a, **k):
            pass

    class Price:
        @staticmethod
        def list(lookup_keys, active, limit):
            return NS(data=[p for p in prices.values() if p.lookup_key == lookup_keys[0] and p.active])

        @staticmethod
        def create(product, currency, unit_amount, lookup_key, transfer_lookup_key, recurring=None):
            p = NS(id=f"price_{len(prices)}", unit_amount=unit_amount, lookup_key=lookup_key, active=True,
                   recurring=NS(interval=recurring["interval"]) if recurring else None)
            prices[p.id] = p
            return p

        @staticmethod
        def modify(pid, active):
            prices[pid].active = active

    fake = NS(Product=Product, Price=Price)
    stripe_catalog.sync(fake, log=lambda *_: None)
    stripe_catalog.sync(fake, log=lambda *_: None)                  # right away
    assert sorted(products) == sorted(f"dcp_{p.id}" for p in pricing.SUBSCRIPTIONS)
    assert len(prices) == 2 * len(pricing.SUBSCRIPTIONS)


def test_the_cancellation_reason_from_stripes_portal_is_saved(stripe_on, monkeypatch):
    """A cancel in Stripe's portal (with "Collect cancellation reason") reaches the Admin panel's counts."""
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    get_settings.cache_clear()
    _, account_id = _signup()
    payload, headers = _signed(_sub_event("customer.subscription.created", account_id, "active"))
    client.post("/api/billing/webhook", content=payload, headers=headers)
    event = _sub_event("customer.subscription.deleted", account_id, "canceled")
    event["data"]["object"].update(canceled_at=1767225600, cancellation_details={
        "feedback": "too_expensive", "comment": "  Great tools, but over my budget right now.  ", "reason": "cancellation_requested"})
    payload, headers = _signed(event)
    assert client.post("/api/billing/webhook", content=payload, headers=headers).status_code == 200
    [sub] = account_store._subscriptions.values()
    assert sub["cancel_feedback"] == "too_expensive" and sub["cancel_comment"] == "Great tools, but over my budget right now."
    admin, _ = _signup("boss@example.com")
    summary = client.get("/api/admin/consultant-requests", headers=_h(admin)).json()["cancellations"]
    assert summary["by_reason"] == {"too_expensive": 1} and summary["comments"][0]["comment"].startswith("Great tools")


def test_bundles_never_charge_twice_and_suggest_the_cheaper_one():
    """Starter / Growth / Pro: tools a bundle covers are dropped, add-ons stay, one bundle at most."""
    from api.services import pricing
    n = pricing.normalize_selection
    assert n(["starter", "data_cleaning", "data_visualization"]) == ["starter"]           # no double purchase
    assert n(["starter", "data_science_ml"]) == ["starter", "data_science_ml"]           # add-on kept
    assert n(["starter", "growth"]) == ["growth"] and n(["growth", "all_tools", "ai"]) == ["all_tools"]
    assert n(["growth", "ai"]) == ["growth"]                                              # AI is in Growth
    with pytest.raises(ValueError):
        n(["starter", "ai"])                                                              # AI needs Data Engineering
    price = pricing.selection_price(["data_cleaning", "data_visualization"], "month")
    assert price["total_usd"] == 38 and price["cheaper_bundle"]["id"] == "starter"
    catalog = {c["id"]: c for c in pricing.subscription_catalog()}
    assert catalog["starter"]["monthly_usd"] == 19 and catalog["growth"]["monthly_usd"] == 49
    assert catalog["all_tools"]["monthly_usd"] == 99
    for b in ("starter", "growth", "all_tools"):                                          # always a real saving
        one_by_one = sum(catalog[t]["monthly_usd"] for t in catalog[b]["covers"])
        assert catalog[b]["bundle_savings_month_usd"] == one_by_one - catalog[b]["monthly_usd"] > 0
