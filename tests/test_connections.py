"""
Connected data sources: refresh choices and prices, the schedule rules
(api/services/connections.py), and GET/PUT /api/connections.
"""

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, connections, pricing

client = TestClient(app)
T = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """
    Start every test in test mode (no Stripe) with an empty in-memory store.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.

    Returns:
        Iterator[None]: yields while the test runs; settings are reset afterwards.
    """
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._subscriptions, account_store._connections,
                  account_store._consultants):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email="owner@example.com"):
    """
    Sign up an individual through the API.

    Args:
        email: str, an unused email address.

    Returns:
        dict: the Authorization header for the new account.

    Example:
        >>> h = _signup()
        >>> client.get("/api/connections", headers=h).status_code
        200
    """
    res = client.post("/api/accounts/signup", json={"password": "Correct-horse-9", "accept_terms": True, "account_type": "individual", "first_name": "Pat",
                                                    "last_name": "Lee", "email": email})
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_refresh_choices_daily_default_and_fast_ones_are_add_ons():
    """Check the five choices, that daily is the default, and that only hourly and live cost extra."""
    opts = {o["id"]: o for o in pricing.refresh_catalog()}
    assert list(opts) == ["manual", "weekly", "daily", "hourly", "live"]
    assert opts["daily"]["default"] and opts["daily"]["price_label"] == "Included"
    assert [k for k, o in opts.items() if not o["included"]] == ["hourly", "live"]


def test_schedule_rules():
    """Check next_sync_at (manual never, first sync now, missed slots not in the past) and validation."""
    assert connections.next_sync_at("manual", T, now=T) is None
    assert connections.next_sync_at("hourly", None, now=T) == T
    assert connections.next_sync_at("weekly", T, now=T) == T + timedelta(days=7)
    assert connections.next_sync_at("daily", T - timedelta(days=3), now=T) == T     # missed -> now
    with pytest.raises(ValueError):
        connections.validate("quickbooks", "every_second")
    with pytest.raises(ValueError):
        connections.validate("myspace", "daily")


def test_refresh_now_cannot_beat_the_chosen_frequency():
    """Check the "Refresh now" gap: 4 hours on daily, 15 minutes on hourly, always fine before a first sync."""
    assert connections.refresh_now_allowed("daily", None, now=T) == (True, None)
    assert connections.refresh_now_allowed("daily", T, now=T + timedelta(hours=1)) == (False, T + timedelta(hours=4))
    assert connections.refresh_now_allowed("daily", T, now=T + timedelta(hours=4)) == (True, None)
    assert connections.refresh_now_allowed("hourly", T, now=T + timedelta(minutes=20))[0]


def test_add_ons_cost_nothing_until_billable(monkeypatch):
    """
    Check addon_total: $0 while hourly/live aren't billable, their price once they are.

    Args:
        monkeypatch: pytest fixture for swapping the option table for one test.
    """
    rows = [{"refresh_frequency": "hourly"}, {"refresh_frequency": "daily"}]
    assert connections.addon_total(rows) == 0.0
    billable = {k: (pricing.RefreshOption(**{**o.__dict__, "billable": True})) for k, o in pricing.REFRESH_OPTIONS_BY_ID.items()}
    monkeypatch.setattr(connections, "REFRESH_OPTIONS_BY_ID", billable)
    assert connections.addon_total(rows) == 15.0


def test_api_needs_data_engineering_then_saves_the_choice():
    """Check that setting a schedule is 403 without Data Engineering and saved as pending with it."""
    h = _signup()
    info = client.get("/api/connections", headers=h).json()
    assert info["can_connect"] is False
    assert info["connectors"][0] == {**info["connectors"][0], "connector": "quickbooks",
                                     "refresh_frequency": "daily", "status": "not_set_up"}
    res = client.put("/api/connections/quickbooks/refresh", headers=h, json={"refresh_frequency": "weekly"})
    assert res.status_code == 403

    client.post("/api/billing/checkout", headers=h, json={"plan_ids": ["data_engineering"], "interval": "month"})
    res = client.put("/api/connections/quickbooks/refresh", headers=h, json={"refresh_frequency": "weekly"})
    assert res.status_code == 200
    qb = res.json()["connectors"][0]
    assert qb["refresh_frequency"] == "weekly" and qb["status"] == "pending"
    assert client.get("/api/connections", headers=h).json()["can_connect"] is True
    assert client.put("/api/connections/myspace/refresh", headers=h,
                      json={"refresh_frequency": "daily"}).status_code == 404
    assert client.put("/api/connections/quickbooks/refresh", headers=h,
                      json={"refresh_frequency": "always"}).status_code == 422
