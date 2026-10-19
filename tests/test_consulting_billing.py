"""
Paying a consultant through the platform (api/services/consulting_billing.py):
a one-time SETUP fee and ONGOING HELP priced by the services picked, with the
consultant's share sent by Stripe Connect — and the rules around it (billing
terms, one ongoing help at a time, switching or losing a consultant, refunds).
"""

from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, billing, price_store, pricing
from tests.consultant_helpers import apply_as_consultant
from tests.test_billing import WHSEC, _signed

client = TestClient(app)
PW = "Correct-horse-9"
OK = {"accept_terms": True}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """One admin (boss@example.com), test mode (no Stripe), an empty memory store, default prices."""
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants, account_store._invites,
                  account_store._applications, account_store._engagements, account_store._subscriptions,
                  price_store._consulting):
        store.clear()
    pricing.apply_consulting_prices(pricing.CONSULTING_DEFAULTS)
    yield
    pricing.apply_consulting_prices(pricing.CONSULTING_DEFAULTS)
    get_settings.cache_clear()


def _signup(email, first, **extra):
    """Sign up an individual; returns (headers, account id)."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": first,
                                                    "last_name": "Lee", "email": email, "password": PW,
                                                    "accept_terms": True, **extra})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}, res.json()["account"]["id"]


def _signin(email):
    res = client.post("/api/accounts/signin", json={"email": email, "password": PW})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def _consultant(admin, email, first):
    """An approved consultant with payouts connected (test mode); returns (headers, consultant id)."""
    h, acc_id = _signup(email, first)
    apply_as_consultant(client, admin, h["Authorization"].split()[1], email)
    client.post(f"/api/admin/consultant-requests/{acc_id}/approve", headers=admin)
    h = _signin(email)
    cid = next(c["id"] for c in client.get("/api/accounts/consultants").json()["consultants"] if c["name"] == first)
    return h, cid


def _kim_and_ana(tools=("data_engineering", "data_visualization")):
    """The admin, Kim (an approved consultant), and Ana (Kim's client, with these tools in her package)."""
    admin, _ = _signup("boss@example.com", "Boss")
    kim, cid = _consultant(admin, "kim@example.com", "Kim")
    ana, ana_id = _signup("ana@example.com", "Ana", wants_consultant=True, consultant_id=cid, consultant_plan="monthly")
    if tools:
        client.post("/api/billing/checkout", headers=ana, json={"plan_ids": list(tools), "interval": "month"})
    return admin, kim, cid, ana, ana_id


def _retainer(h, services, interval="month", **extra):
    return client.post("/api/billing/consulting/checkout", headers=h,
                       json={"kind": "retainer", "services": services, "interval": interval, **OK, **extra})


def test_hours_come_from_the_services_picked():
    assert pricing.help_hours(["data_cleaning"]) == 10                                    # the core block
    assert pricing.help_hours(["data_cleaning", "data_engineering"]) == 10               # ...once
    assert pricing.help_hours(["data_visualization", "data_science_ml"]) == 5            # cloud data: no core
    assert pricing.help_hours(["data_cleaning", "data_visualization", "ai"]) == 12.5     # AI counts 0
    every = [*pricing.CORE_HELP_SERVICES, *pricing.LIGHT_HELP_SERVICES]
    assert pricing.help_hours(every) == 17.5                                             # 10 + 3 x 2.5
    assert pricing.help_hours([*every, "data_strategy_discovery"]) == 17.5               # consulting only: 0
    assert pricing.retainer_price_usd(12.5, "month") == 1083.33                          # 12.5 x 52/12 x $20
    assert pricing.retainer_price_usd(12.5, "year") == 10833.3                           # a year = 10 months
    c = client.get("/api/intake/options").json()["consulting"]
    assert c["setup_usd"] == 300 and c["min_monthly_usd"] == 216.67


def test_a_client_pays_for_setup_and_ongoing_help_in_test_mode():
    _, kim, _, ana, _ = _kim_and_ana()
    # Kim hasn't connected payouts: nothing can be bought yet.
    first = client.get("/api/billing/consulting", headers=ana).json()
    assert first["consultant"] == {"name": "Kim"} and not first["can_buy"] and "setting up" in first["reason"]
    assert "end of the" in first["cancel_terms"]
    client.post("/api/consultant/settings/payouts", headers=kim)
    view = client.get("/api/billing/consulting", headers=ana).json()
    # Only her package's tools can be picked (Strategy & Discovery is hourly consulting, never ongoing help).
    assert view["can_buy"] and [s["id"] for s in view["services"]] == [
        "data_cleaning", "data_engineering", "data_visualization"]
    assert {s["id"]: s["hours"] for s in view["services"]}["data_visualization"] == 2.5
    # The billing terms must be accepted.
    no_terms = client.post("/api/billing/consulting/checkout", headers=ana, json={"kind": "setup"})
    assert no_terms.status_code == 400 and "terms" in no_terms.json()["detail"]
    setup = client.post("/api/billing/consulting/checkout", headers=ana, json={"kind": "setup", **OK}).json()
    assert setup["activated"] and setup["amount_usd"] == 300
    # A tool not in the package can't be picked; nothing picked is refused.
    assert _retainer(ana, ["data_science_ml"]).status_code == 400
    assert _retainer(ana, ["ai"]).status_code == 400
    res = _retainer(ana, ["data_cleaning", "data_engineering", "data_visualization"]).json()
    assert res["hours_per_week"] == 12.5 and res["amount_usd"] == 1083.33
    view = client.get("/api/billing/consulting", headers=ana).json()
    assert view["retainer"]["services"] == ["data_cleaning", "data_engineering", "data_visualization"]
    assert all(e["terms_accepted_at"] for e in account_store._engagements.values())
    assert all(e["platform_fee_percent"] == 20.0 for e in account_store._engagements.values())


def test_changes_wait_for_the_period_to_end():
    _, kim, _, ana, _ = _kim_and_ana()
    client.post("/api/consultant/settings/payouts", headers=kim)
    assert _retainer(ana, ["data_visualization"]).status_code == 200
    again = _retainer(ana, ["data_cleaning"])
    assert again.status_code == 409 and "end it" in again.json()["detail"]
    ended = client.post("/api/billing/consulting/cancel", headers=ana).json()
    assert ended["status"] == "canceled" and ended["ended_reason"] == "client"   # test mode: at once
    assert _retainer(ana, ["data_cleaning"]).status_code == 200                   # then choose again


def test_switching_consultant_waits_for_ongoing_help_to_end():
    admin, kim, kim_cid, ana, _ = _kim_and_ana()
    client.post("/api/consultant/settings/payouts", headers=kim)
    _, lee_cid = _consultant(admin, "lee@example.com", "Lee")
    _retainer(ana, ["data_visualization"])
    switch = client.post("/api/accounts/consultant", headers=ana, json={"consultant_id": lee_cid,
                                                                       "consultant_plan": "monthly"})
    assert switch.status_code == 409 and "End it first" in switch.json()["detail"]
    # Keeping Kim (only changing one-time / monthly) is fine.
    assert client.post("/api/accounts/consultant", headers=ana, json={"consultant_id": kim_cid,
                                                                     "consultant_plan": "one_time"}).status_code == 200
    client.post("/api/billing/consulting/cancel", headers=ana)
    assert client.post("/api/accounts/consultant", headers=ana, json={"consultant_id": lee_cid,
                                                                     "consultant_plan": "monthly"}).status_code == 200


def test_a_deactivated_consultants_clients_have_ongoing_help_ended_at_once():
    admin, kim, kim_cid, ana, _ = _kim_and_ana()
    client.post("/api/consultant/settings/payouts", headers=kim)
    _retainer(ana, ["data_visualization"])
    res = client.post(f"/api/admin/consultants/{kim_cid}/deactivate", headers=admin, json={"reason": "Left"}).json()
    assert "ongoing help ended now" in res["notice"] and "ana@example.com" in res["notice"]
    assert "Reverse transfer" in res["notice"]
    view = client.get("/api/billing/consulting", headers=ana).json()
    assert view["retainer"] is None and view["ended"]["ended_reason"] == "consultant_deactivated"
    assert view["ended"]["consultant_name"] == "Kim"


def test_a_consultant_deleting_their_account_ends_their_clients_ongoing_help():
    _, kim, _, ana, _ = _kim_and_ana()
    client.post("/api/consultant/settings/payouts", headers=kim)
    _retainer(ana, ["data_visualization"])
    gone = client.request("DELETE", "/api/accounts/me", headers=kim, json={"confirm_email": "kim@example.com"})
    assert gone.status_code == 200, gone.text
    [e] = account_store._engagements.values()
    assert e["status"] == "canceled" and e["ended_reason"] == "consultant_left"


def test_who_may_buy():
    _, kim, _, ana, ana_id = _kim_and_ana()
    client.post("/api/consultant/settings/payouts", headers=kim)
    as_kim = {**kim, "X-Acting-As": ana_id}
    assert client.get("/api/billing/consulting", headers=as_kim).status_code == 200      # Kim can look
    assert client.post("/api/billing/consulting/checkout", headers=as_kim,
                       json={"kind": "setup", **OK}).status_code == 403                   # but not buy her own time
    bo, _ = _signup("bo@example.com", "Bo")
    view = client.get("/api/billing/consulting", headers=bo).json()
    assert view["consultant"] is None and "Choose a consultant first" in view["reason"]


def test_an_admin_consultant_needs_no_payout_account_and_takes_no_fee():
    admin, _ = _signup("boss@example.com", "Boss")
    boss_cid = next(c["id"] for c in client.get("/api/accounts/consultants").json()["consultants"])
    ana, _ = _signup("ana@example.com", "Ana", wants_consultant=True, consultant_id=boss_cid)
    assert client.get("/api/billing/consulting", headers=ana).json()["can_buy"] is True
    client.post("/api/billing/consulting/checkout", headers=ana, json={"kind": "setup", **OK})
    [row] = account_store._engagements.values()
    assert row["platform_fee_percent"] == 0.0
    assert client.post("/api/consultant/settings/payouts", headers=admin).status_code == 400


def test_an_admin_changes_the_prices():
    admin, kim, _, ana, _ = _kim_and_ana()
    assert client.put("/api/admin/consulting-prices", headers=kim, json={"help_rate_usd": 30}).status_code == 403
    bad = client.put("/api/admin/consulting-prices", headers=admin, json={"max_hours": 5})
    assert bad.status_code == 400                                     # below the 10 core hours
    saved = client.put("/api/admin/consulting-prices", headers=admin,
                       json={"help_rate_usd": 25, "setup_usd": 350, "platform_fee_percent": 15}).json()
    assert saved["values"]["help_rate_usd"] == 25 and saved["example"]["hours"] == 12.5
    client.post("/api/consultant/settings/payouts", headers=kim)
    res = _retainer(ana, ["data_visualization"]).json()
    assert res["amount_usd"] == round(2.5 * 52 / 12 * 25, 2)           # the new rate, at once
    assert [e["platform_fee_percent"] for e in account_store._engagements.values()] == [15.0]
    assert client.get("/api/intake/options").json()["consulting"]["setup_usd"] == 350


class ConnectStripe:
    """Just enough of Stripe (with Connect) for consulting_billing, recording each call."""

    def __init__(self):
        self.calls = []
        fake = self

        class Customer:
            @staticmethod
            def create(**kw):
                return NS(id="cus_ana")

        class Account:
            @staticmethod
            def create(**kw):
                fake.calls.append(("account", kw))
                return {"id": "acct_kim"}

            @staticmethod
            def retrieve(acct):
                return {"id": acct, "details_submitted": True, "capabilities": {"transfers": "active"}}

            @staticmethod
            def create_login_link(acct):
                return {"url": f"https://connect.stripe.com/express/{acct}"}

        class AccountLink:
            @staticmethod
            def create(**kw):
                return {"url": "https://connect.stripe.com/setup/e/acct_kim"}

        class Session:
            @staticmethod
            def create(**kw):
                fake.calls.append(("checkout", kw))
                return {"id": f"cs_{len(fake.calls)}", "url": "https://checkout.stripe.com/c/pay/cs"}

        class Subscription:
            @staticmethod
            def cancel(sub_id):
                fake.calls.append(("cancel", sub_id))

        import stripe as real

        self.Customer, self.Account, self.AccountLink, self.Subscription = Customer, Account, AccountLink, Subscription
        self.checkout = NS(Session=Session)
        self.Webhook = real.Webhook
        self.SignatureVerificationError = real.SignatureVerificationError


def _hook(kind, obj):
    payload, headers = _signed({"id": f"evt_{kind}", "type": kind, "data": {"object": obj}})
    assert client.post("/api/billing/webhook", content=payload, headers=headers).status_code == 200


def test_with_stripe_the_consultants_share_goes_to_their_account(monkeypatch):
    admin, kim, kim_cid, ana, ana_id = _kim_and_ana()
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    get_settings.cache_clear()
    fake = ConnectStripe()
    monkeypatch.setattr(billing, "_stripe", lambda: fake)

    # Kim connects payouts: an Express account, then Stripe's own form.
    assert client.post("/api/consultant/settings/payouts", headers=kim).json()["url"].startswith("https://connect")
    assert fake.calls[0][1]["type"] == "express"
    assert client.get("/api/consultant/settings", headers=kim).json()["payouts"]["ready"] is True

    # Ongoing help: the price Stripe charges is the one worked out here; Kim gets all but 20%.
    res = _retainer(ana, ["data_engineering", "data_visualization"]).json()
    _, kw = fake.calls[-1]
    assert kw["mode"] == "subscription"
    price = kw["line_items"][0]["price_data"]
    assert price["unit_amount"] == 108333 and price["recurring"] == {"interval": "month"}
    assert price["product"] == "dcp_consult_retainer" and res["amount_usd"] == 1083.33
    assert kw["subscription_data"]["transfer_data"] == {"destination": "acct_kim"}
    assert kw["subscription_data"]["application_fee_percent"] == 20.0
    meta = kw["subscription_data"]["metadata"]
    engagement = account_store._engagements[meta["engagement_id"]]
    _hook("customer.subscription.created", {"id": "sub_help", "object": "subscription", "status": "active",
                                            "customer": "cus_ana", "metadata": meta, "current_period_end": 1798761600,
                                            "items": {"data": []}})
    assert engagement["status"] == "active" and engagement["stripe_subscription_id"] == "sub_help"
    assert not account_store._subscriptions or all(s["account_id"] != ana_id or s["items"]
                                                   for s in account_store._subscriptions.values())

    # Setup: a one-time payment; the platform keeps 20% of $300; then refunded in the Stripe dashboard.
    client.post("/api/billing/consulting/checkout", headers=ana, json={"kind": "setup", **OK})
    _, kw = fake.calls[-1]
    assert kw["mode"] == "payment" and kw["payment_intent_data"]["application_fee_amount"] == 6000
    _hook("checkout.session.completed", {"id": "cs_paid", "object": "checkout.session", "mode": "payment",
                                         "payment_status": "paid", "payment_intent": "pi_setup",
                                         "metadata": kw["metadata"]})
    setup = account_store._engagements[kw["metadata"]["engagement_id"]]
    assert setup["status"] == "paid"
    _hook("charge.refunded", {"id": "ch_1", "object": "charge", "payment_intent": "pi_setup", "refunded": True})
    assert setup["status"] == "refunded"

    # Kim is deactivated: Ana's ongoing help is canceled in Stripe at once.
    client.post(f"/api/admin/consultants/{kim_cid}/deactivate", headers=admin, json={})
    assert ("cancel", "sub_help") in fake.calls
    assert engagement["status"] == "canceled" and engagement["ended_reason"] == "consultant_deactivated"
