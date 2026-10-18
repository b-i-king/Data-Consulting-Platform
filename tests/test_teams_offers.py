"""
Business team roles (owner / developer / member = view only) and the
5-person limit (api/services/teams.py), plus offers on a first package: free
trials (dim.plan_prices.trial_days) and seasonal sales that repeat every year
(dim.seasonal_sales, api/services/offers.py). In-memory store; Stripe faked.
"""

import re
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, billing, offers, price_store, pricing
from tests.test_billing import FakeStripe

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """One admin (boss@example.com), Stripe off, an empty memory store, no trials or sales."""
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    for key in ("STRIPE_SECRET_KEY", "SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants,
                  account_store._subscriptions, account_store._deletions, price_store._prices,
                  pricing.TRIAL_DAYS, offers._sales):
        store.clear()
    yield
    pricing.apply_prices(pricing.DEFAULT_PRICES)
    pricing.TRIAL_DAYS.clear()
    get_settings.cache_clear()


def _h(token):
    return {"Authorization": f"Bearer {token}"}


def _signup(email, first="Ana", **extra):
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": first,
                                                    "last_name": "Lee", "email": email, "password": PW,
                                                    "accept_terms": True, **extra})
    return res


def _business(colleagues):
    """A business owner (Ann) with colleagues [(first, email, role)]; returns the owner's token."""
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": "ann@example.com",
        "company_name": "Ko Bakery", "password": PW, "accept_terms": True,
        "team_members": [{"first_name": f, "last_name": "Ko", "email": e, "role": r} for f, e, r in colleagues]})
    assert res.status_code == 201, res.text
    return res.json()["access_token"]


def _sign_in(email):
    """A colleague's first sign-in sets their password; returns their token."""
    return client.post("/api/accounts/signin", json={"email": email, "password": PW}).json()["access_token"]


# ── Team size ────────────────────────────────────────────────────────────────

def test_a_team_holds_five_people_then_points_to_enterprise():
    """Sign-up with 5 colleagues (6 people) is refused; adding a 6th later is refused too."""
    too_many = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": "ann@example.com",
        "company_name": "Ko Bakery", "password": PW, "accept_terms": True,
        "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": f"p{i}@example.com"} for i in range(5)]})
    assert too_many.status_code == 409 and "Enterprise" in too_many.json()["detail"]
    assert account_store._accounts == {}                                  # nothing half-made

    owner = _business([("Bo", f"p{i}@example.com", "developer") for i in range(3)])   # 4 people
    team = client.get("/api/accounts/team", headers=_h(owner)).json()
    assert team["seats"] == {"used": 4, "limit": 5} and team["can_manage"]
    add = {"first_name": "Cy", "last_name": "Ko", "email": "cy@example.com", "role": "member"}
    assert client.post("/api/accounts/team/members", headers=_h(owner), json=add).status_code == 201
    sixth = client.post("/api/accounts/team/members", headers=_h(owner), json={**add, "email": "dee@example.com"})
    assert sixth.status_code == 409 and "5 of 5 people" in sixth.json()["detail"]


# ── Roles ────────────────────────────────────────────────────────────────────

def test_developers_change_data_members_only_look():
    """
    A developer may add dashboard tabs and review matches; a member (view only) gets 403
    for both but still sees the workspace. Only the owner manages roles.
    """
    owner = _business([("Dev", "dev@example.com", "developer"), ("Viv", "viv@example.com", "member")])
    client.post("/api/billing/checkout", headers=_h(owner), json={"plan_ids": ["all_tools"], "interval": "month"})
    dev, viv = _sign_in("dev@example.com"), _sign_in("viv@example.com")

    dev_plan = client.get("/api/accounts/services", headers=_h(dev)).json()
    viv_plan = client.get("/api/accounts/services", headers=_h(viv)).json()
    assert dev_plan["can_edit"] and dev_plan["team_role"] == "developer"
    assert not viv_plan["can_edit"] and viv_plan["team_role"] == "member"
    assert "data_visualization" in viv_plan["active"]                     # members see what the team pays for

    tab = {"name": "Sales", "questions": ["total amount by store"]}
    assert client.post("/api/dashboards/tabs", headers=_h(dev), json=tab).status_code == 201
    blocked = client.post("/api/dashboards/tabs", headers=_h(viv), json=tab)
    assert blocked.status_code == 403 and "view-only" in blocked.json()["detail"]
    assert client.post("/api/entities/review", headers=_h(dev), json={"link_id": 1, "same": True}).status_code == 404
    assert client.post("/api/entities/review", headers=_h(viv), json={"link_id": 1, "same": True}).status_code == 403

    # Only the owner changes roles.
    viv_id = next(a["id"] for a in account_store._accounts.values() if a["email"] == "viv@example.com")
    assert client.put(f"/api/accounts/team/members/{viv_id}", headers=_h(dev), json={"role": "developer"}).status_code == 403
    up = client.put(f"/api/accounts/team/members/{viv_id}", headers=_h(owner), json={"role": "developer"})
    assert up.status_code == 200 and up.json()["team_role"] == "developer"
    assert client.post("/api/dashboards/tabs", headers=_h(viv), json={**tab, "name": "Mine"}).status_code == 201
    assert client.put(f"/api/accounts/team/members/{viv_id}", headers=_h(owner),
                      json={"role": "owner"}).status_code == 422             # ownership doesn't move here


def test_the_owner_removes_a_colleague():
    """Removing a colleague deletes their account, frees a seat, and keeps only the anonymous count."""
    owner = _business([("Bo", "bo@example.com", "member")])
    bo_id = next(a["id"] for a in account_store._accounts.values() if a["email"] == "bo@example.com")
    res = client.delete(f"/api/accounts/team/members/{bo_id}", headers=_h(owner))
    assert res.status_code == 200 and res.json()["seats"] == {"used": 1, "limit": 5}
    assert all(a["email"] != "bo@example.com" for a in account_store._accounts.values())
    assert [(d["role"], d["removed_by"]) for d in account_store._deletions] == [("team_member", "team_owner")]
    owner_id = next(a["id"] for a in account_store._accounts.values() if a["email"] == "ann@example.com")
    assert client.delete(f"/api/accounts/team/members/{owner_id}", headers=_h(owner)).status_code == 404


# ── Seasonal sales ───────────────────────────────────────────────────────────

def test_the_schema_seed_matches_the_default_sales():
    """db/schema.sql seeds dim.seasonal_sales with offers.DEFAULT_SALES (switched off)."""
    sql = Path("db/schema.sql").read_text(encoding="utf-8")
    block = sql[sql.index("INSERT INTO dim.seasonal_sales"):sql.index("ON CONFLICT (id) DO NOTHING")]
    seeded = [{"id": i, "name": n, "starts_on": s, "ends_on": e, "percent_off": int(p)}
              for i, n, s, e, p in re.findall(r"\('(\w+)', '([^']+)', '([\d-]+)', '([\d-]+)', (\d+)\)", block)]
    assert seeded == list(offers.DEFAULT_SALES)


def test_sale_windows_repeat_every_year_and_may_wrap_the_new_year():
    """MM-DD windows: inside / outside, any year, and a December-to-January window."""
    assert offers.in_window(date(2026, 12, 1), "11-24", "12-31")
    assert offers.in_window(date(2031, 12, 31), "11-24", "12-31")             # every year
    assert not offers.in_window(date(2026, 11, 23), "11-24", "12-31")
    assert offers.in_window(date(2027, 1, 3), "12-15", "01-05")               # wraps
    assert offers.in_window(date(2026, 12, 20), "12-15", "01-05")
    assert not offers.in_window(date(2027, 1, 6), "12-15", "01-05")


@pytest.mark.anyio
async def test_the_running_sale_and_when_it_ends():
    """Only a switched-on sale counts; its end date is this season's."""
    assert await offers.current_sale(date(2026, 12, 1)) is None               # seeded switched off
    offers._memory_sales()["year_end"]["active"] = True
    sale = await offers.current_sale(date(2026, 12, 1))
    assert sale["id"] == "year_end" and sale["percent_off"] == 25 and sale["ends"] == "2026-12-31"
    offers._memory_sales()["new_year"].update(active=True, starts_on="12-28", ends_on="01-31")
    assert (await offers.current_sale(date(2026, 12, 29)))["id"] == "year_end"   # the bigger discount wins
    assert (await offers.current_sale(date(2027, 1, 10)))["ends"] == "2027-01-31"


def _today_window():
    """A one-day window that includes today (UTC)."""
    md = datetime.now(timezone.utc).strftime("%m-%d")
    return {"starts_on": md, "ends_on": md}


def test_admins_edit_sales_and_clients_see_the_running_one():
    """Admin -> Seasonal sales: bad dates refused; switched on, the plans page shows it."""
    admin = _signup("boss@example.com", "Boss").json()["access_token"]
    ana = _signup("ana@example.com").json()["access_token"]
    body = {"name": "Year-end sale", "percent_off": 30, "active": True, **_today_window()}
    assert client.put("/api/admin/sales/year_end", headers=_h(ana), json=body).status_code == 403
    assert client.put("/api/admin/sales/year_end", headers=_h(admin),
                      json={**body, "starts_on": "02-30"}).status_code == 400
    assert client.put("/api/admin/sales/nope", headers=_h(admin), json=body).status_code == 404
    res = client.put("/api/admin/sales/year_end", headers=_h(admin), json=body).json()
    assert res["running"]["percent_off"] == 30
    assert client.get("/api/billing/plans").json()["sale"]["name"] == "Year-end sale"
    assert client.get("/api/accounts/services", headers=_h(ana)).json()["offer"]["applies"] == "sale"


# ── Free trials ──────────────────────────────────────────────────────────────

def test_a_first_package_gets_one_free_trial(monkeypatch):
    """
    Trial days set in Admin -> Prices: a first package starts "trialing" (test mode);
    cancel and subscribe again: no second trial.
    """
    admin = _signup("boss@example.com", "Boss").json()["access_token"]
    client.put("/api/admin/prices/all_tools", headers=_h(admin),
               json={"monthly_usd": 99, "annual_usd": 990, "trial_days": 14})
    assert next(p for p in client.get("/api/billing/plans").json()["plans"] if p["id"] == "all_tools")["trial_days"] == 14
    ana = _signup("ana@example.com").json()["access_token"]
    assert client.get("/api/accounts/services", headers=_h(ana)).json()["offer"]["trial_eligible"]

    first = client.post("/api/billing/checkout", headers=_h(ana), json={"plan_ids": ["all_tools"], "interval": "month"})
    assert first.json()["trial_days"] == 14
    [sub] = account_store._subscriptions.values()
    assert sub["status"] == "trialing" and sub["current_period_end"]
    assert len(client.get("/api/accounts/services", headers=_h(ana)).json()["active"]) > 1   # tools on during the trial

    client.post("/api/billing/cancel", headers=_h(ana))
    again = client.post("/api/billing/checkout", headers=_h(ana), json={"plan_ids": ["all_tools"], "interval": "month"})
    assert again.json()["trial_days"] == 0
    assert not client.get("/api/accounts/services", headers=_h(ana)).json()["offer"]["trial_eligible"]


def test_with_stripe_a_trial_or_a_sale_goes_to_checkout(monkeypatch):
    """
    Stripe Checkout gets trial_period_days for a trial; during a sale (no trial) it gets the
    sale's coupon, made once, instead of the promotion-code box.
    """
    admin = _signup("boss@example.com", "Boss").json()["access_token"]
    client.put("/api/admin/sales/year_end", headers=_h(admin),
               json={"name": "Year-end sale", "percent_off": 25, "active": True, **_today_window()})
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    get_settings.cache_clear()
    fake = FakeStripe()
    coupons = {}

    class Coupon:
        @staticmethod
        def retrieve(cid):
            if cid not in coupons:
                raise RuntimeError("No such coupon")
            return coupons[cid]

        @staticmethod
        def create(id, percent_off, duration, name):
            coupons[id] = NS(id=id, percent_off=percent_off, duration=duration)
            return coupons[id]

    fake.Coupon = Coupon
    monkeypatch.setattr(billing, "_stripe", lambda: fake)

    ana = _signup("ana@example.com").json()["access_token"]
    client.post("/api/billing/checkout", headers=_h(ana), json={"plan_ids": ["data_visualization"], "interval": "month"})
    kind, kw = fake.calls[-1]
    assert kind == "checkout" and kw["discounts"] == [{"coupon": "dcp_sale_year_end_25"}]
    assert "allow_promotion_codes" not in kw and "trial_period_days" not in kw["subscription_data"]
    assert coupons["dcp_sale_year_end_25"].duration == "once"                 # the first payment only

    pricing.TRIAL_DAYS["data_visualization"] = 7                             # a trial beats the sale
    bo = _signup("bo@example.com", "Bo").json()["access_token"]
    client.post("/api/billing/checkout", headers=_h(bo), json={"plan_ids": ["data_visualization"], "interval": "month"})
    kind, kw = fake.calls[-1]
    assert kw["subscription_data"]["trial_period_days"] == 7
    assert kw["allow_promotion_codes"] is True and "discounts" not in kw
    assert len(coupons) == 1
