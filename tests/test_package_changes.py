"""
Changing a live Stripe package mid-cycle (api/services/billing.py start_checkout):
add a tool, drop one, switch to All tools and back, switch monthly <-> annual,
re-subscribe after "cancel at period end", and keep not-yet-charged tools (AI).
A fake Stripe keeps the subscription's items like the real one does, so each
change is checked end to end: what's sent, and what the package holds after.
"""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, billing, pricing, warehouse_store

client = TestClient(app)


class StatefulStripe:
    """Prices by lookup key, and subscriptions whose items change with Subscription.modify."""

    def __init__(self):
        self.subs: dict[str, dict] = {}
        self.calls: list[dict] = []
        self.n = 0
        fake = self

        class Price:
            @staticmethod
            def list(lookup_keys, active, limit):
                return NS(data=[NS(id=f"price_{lookup_keys[0]}")])

        class Subscription:
            @staticmethod
            def modify(sub_id, **kw):
                fake.calls.append(kw)
                sub = fake.subs[sub_id]
                for change in kw.get("items", []):
                    if change.get("deleted"):
                        sub["items"] = [i for i in sub["items"] if i["id"] != change["id"]]
                    else:
                        fake.n += 1
                        sub["items"].append({"id": f"si_{fake.n}", "price": {"lookup_key": change["price"][len("price_"):]}})
                sub["metadata"] = kw["metadata"]
                sub["cancel_at_period_end"] = kw.get("cancel_at_period_end", sub["cancel_at_period_end"])
                return {"id": sub_id, "customer": "cus_1", "status": "active", "metadata": sub["metadata"],
                        "cancel_at_period_end": sub["cancel_at_period_end"], "items": {"data": list(sub["items"])}}

        self.Price, self.Subscription = Price, Subscription


@pytest.fixture
def stripe_live(monkeypatch):
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._subscriptions):
        store.clear()
    fake = StatefulStripe()
    monkeypatch.setattr(billing, "_stripe", lambda: fake)

    async def has_data(_holder):
        return True

    monkeypatch.setattr(warehouse_store, "has_data", has_data)   # AI may be picked
    yield fake
    get_settings.cache_clear()


def _subscribed(fake, tools, interval="month", cancel_at_period_end=False):
    """A signed-up client already paying through Stripe for `tools`. Returns their token."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Pat", "last_name": "Lee",
                                                    "email": "pat@example.com", "password": "Correct-horse-9",
                                                    "accept_terms": True}).json()
    items = [{"id": f"si_{t}", "price": {"lookup_key": pricing.lookup_key(t, interval)}} for t in tools]
    fake.subs["sub_1"] = {"items": items, "metadata": {"account_id": res["account"]["id"]},
                          "cancel_at_period_end": cancel_at_period_end}
    import asyncio

    asyncio.run(account_store.upsert_subscription(
        account_id=res["account"]["id"], billing_interval=interval, status="active",
        items=[{"plan_id": t, "stripe_subscription_item_id": f"si_{t}"} for t in tools],
        stripe_subscription_id="sub_1", stripe_customer_id="cus_1", cancel_at_period_end=cancel_at_period_end))
    return res["access_token"]


def _change(token, tools, interval="month"):
    return client.post("/api/billing/checkout", headers={"Authorization": f"Bearer {token}"},
                       json={"plan_ids": tools, "interval": interval})


def _package(token):
    [sub] = client.get("/api/accounts/services", headers={"Authorization": f"Bearer {token}"}).json()["subscriptions"]
    return sub


def test_adding_a_tool_mid_cycle_bills_the_difference_now(stripe_live):
    t = _subscribed(stripe_live, ["data_cleaning", "data_visualization"])
    res = _change(t, ["data_cleaning", "data_visualization", "data_engineering"])
    assert res.json()["updated"] and res.json()["url"] is None          # no new checkout: same subscription
    kw = stripe_live.calls[-1]
    assert kw["items"] == [{"price": "price_dcp_data_engineering_month"}]   # only the new tool is added
    assert kw["proration_behavior"] == "always_invoice"                 # the difference is billed now
    assert sorted(_package(t)["plan_ids"]) == ["data_cleaning", "data_engineering", "data_visualization"]


def test_dropping_a_tool_removes_only_that_item(stripe_live):
    t = _subscribed(stripe_live, ["data_cleaning", "data_visualization", "data_science_ml"])
    _change(t, ["data_cleaning", "data_visualization"])
    assert stripe_live.calls[-1]["items"] == [{"id": "si_data_science_ml", "deleted": True}]
    assert sorted(_package(t)["plan_ids"]) == ["data_cleaning", "data_visualization"]


def test_switching_to_all_tools_and_back(stripe_live):
    t = _subscribed(stripe_live, ["data_cleaning", "data_visualization", "data_engineering"])
    _change(t, ["all_tools"])
    kw = stripe_live.calls[-1]
    assert sorted(c["id"] for c in kw["items"] if c.get("deleted")) == ["si_data_cleaning", "si_data_engineering",
                                                                         "si_data_visualization"]
    assert [c["price"] for c in kw["items"] if "price" in c] == ["price_dcp_all_tools_month"]
    assert _package(t)["plan_ids"] == ["all_tools"]
    _change(t, ["data_cleaning", "data_visualization"])                   # back to two tools
    assert sorted(_package(t)["plan_ids"]) == ["data_cleaning", "data_visualization"]
    assert len(stripe_live.subs["sub_1"]["items"]) == 2                   # no leftover bundle item


def test_monthly_to_annual_swaps_every_item(stripe_live):
    t = _subscribed(stripe_live, ["data_cleaning", "data_visualization"])
    _change(t, ["data_cleaning", "data_visualization"], interval="year")
    kw = stripe_live.calls[-1]
    assert sum(1 for c in kw["items"] if c.get("deleted")) == 2
    assert sorted(c["price"] for c in kw["items"] if "price" in c) == ["price_dcp_data_cleaning_year",
                                                                       "price_dcp_data_visualization_year"]
    pkg = _package(t)
    assert pkg["interval"] == "year" and all(i["price"]["lookup_key"].endswith("_year")
                                             for i in stripe_live.subs["sub_1"]["items"])
    assert _change(t, ["data_cleaning", "data_visualization"], interval="year").status_code == 409   # same again


def test_changing_the_package_undoes_a_scheduled_cancel(stripe_live):
    t = _subscribed(stripe_live, ["data_cleaning"], cancel_at_period_end=True)
    _change(t, ["data_cleaning", "data_visualization"])
    assert stripe_live.calls[-1]["cancel_at_period_end"] is False
    assert _package(t)["cancel_at_period_end"] is False


def test_tools_not_charged_yet_are_kept(stripe_live):
    """AI is free until it launches: it never goes to Stripe, but stays in the package."""
    t = _subscribed(stripe_live, ["data_cleaning", "data_engineering"])
    _change(t, ["data_cleaning", "data_engineering", "ai"])
    kw = stripe_live.calls[-1]
    assert "items" not in kw                                              # nothing to bill: Stripe items unchanged
    assert kw["metadata"]["free_plans"] == "ai"
    assert "ai" in _package(t)["plan_ids"]
