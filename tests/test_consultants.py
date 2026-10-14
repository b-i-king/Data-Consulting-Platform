"""
Consultants, "done for you" clients, the access rule, subscriptions and the
"Other" industry (api/services/consultants.py, api/routers/consultants.py).
Runs on the in-memory account store, like the rest of the API tests.
"""

import io

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store

client = TestClient(app)
CODE = "lavender-test-code"


@pytest.fixture(autouse=True)
def consultant_settings(monkeypatch):
    """Two consultants (the first is the default) and an access code; fresh memory store."""
    monkeypatch.setenv("CONSULTANT_EMAILS", "brandon@example.com, second@example.com")
    monkeypatch.setenv("CONSULTANT_ACCESS_CODE", CODE)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants, account_store._services):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email, first="Dana", **extra):
    body = {"account_type": "individual", "first_name": first, "last_name": "Lee", "email": email, **extra}
    return client.post("/api/accounts/signup", json=body)


def _auth(token, acting_as=None):
    h = {"Authorization": f"Bearer {token}"}
    if acting_as:
        h["X-Acting-As"] = acting_as
    return h


def _consultant(email="brandon@example.com", first="Brandon"):
    return _signup(email, first, access_code=CODE).json()["access_token"]


def test_consultant_sign_in_needs_the_access_code():
    assert _signup("brandon@example.com", "Brandon").status_code == 403            # no code
    assert _signup("brandon@example.com", "Brandon", access_code="nope").status_code == 403
    res = _signup("brandon@example.com", "Brandon", access_code=CODE)
    assert res.status_code == 201 and res.json()["account"]["role"] == "consultant"
    assert client.post("/api/accounts/signin", json={"email": "brandon@example.com"}).status_code == 403
    ok = client.post("/api/accounts/signin", json={"email": "brandon@example.com", "access_code": CODE})
    assert ok.status_code == 200 and ok.json()["account"]["role"] == "consultant"


def test_consultant_sign_in_is_off_without_a_code(monkeypatch):
    monkeypatch.setenv("CONSULTANT_ACCESS_CODE", "")
    get_settings.cache_clear()
    res = _signup("brandon@example.com", "Brandon", access_code="anything")
    assert res.status_code == 403 and "isn't switched on" in res.json()["detail"]


def test_done_for_you_clients_go_to_the_default_consultant_and_get_every_tool():
    _consultant()
    opts = client.get("/api/intake/options").json()
    assert opts["consultant"] == {"name": "Brandon"}
    res = _signup("ana@example.com", "Ana", wants_consultant=True)
    assert res.status_code == 201
    acc = res.json()["account"]
    assert acc["wants_consultant"] and acc["consultant_name"] == "Brandon"
    plan = client.get("/api/accounts/services", headers=_auth(res.json()["access_token"])).json()
    available = [s["id"] for s in plan["services"] if s["available"]]
    assert plan["active"] == available                       # every available service

    diy = _signup("sam@example.com", "Sam").json()
    assert diy["account"]["consultant_name"] is None
    plan = client.get("/api/accounts/services", headers=_auth(diy["access_token"])).json()
    assert plan["active"] == ["data_cleaning"]


def test_clients_waiting_for_a_consultant_are_assigned_when_one_signs_in():
    waiting = _signup("early@example.com", "Early", wants_consultant=True).json()["account"]
    assert waiting["consultant_name"] is None                # nobody set up yet
    token = _consultant()
    clients = client.get("/api/consultant/clients", headers=_auth(token)).json()["clients"]
    assert [c["email"] for c in clients] == ["early@example.com"]


def test_the_access_rule():
    brandon = _consultant()
    other = _consultant("second@example.com", "Avery")
    mine = _signup("ana@example.com", "Ana", wants_consultant=True).json()
    theirs_id = client.post("/api/consultant/clients", headers=_auth(other), json={
        "account_type": "business", "first_name": "Kai", "last_name": "Ono", "email": "kai@example.com",
        "company_name": "Ono Coffee"}).json()["id"]
    mine_id = mine["account"]["id"]

    # A consultant works on their own client...
    res = client.get("/api/accounts/services", headers=_auth(brandon, mine_id))
    assert res.status_code == 200 and res.json()["acting"] and res.json()["account"]["email"] == "ana@example.com"
    # ...but not on another consultant's client,
    assert client.get("/api/accounts/services", headers=_auth(brandon, theirs_id)).status_code == 403
    # and a client can never act as anyone else.
    assert client.get("/api/accounts/services", headers=_auth(mine["access_token"], theirs_id)).status_code == 403
    # Clients can't list clients.
    assert client.get("/api/consultant/clients", headers=_auth(mine["access_token"])).status_code == 403
    # Each consultant sees only their own clients.
    names = [c["label"] for c in client.get("/api/consultant/clients", headers=_auth(other)).json()["clients"]]
    assert names == ["Ono Coffee"]


def test_a_consultants_download_is_billed_to_the_client():
    brandon = _consultant()
    ana = _signup("ana@example.com", "Ana", wants_consultant=True).json()["account"]["id"]
    csv = b"customer,amount\n" + b"".join(f"C{i},{i}\n".encode() for i in range(40))
    account_store._usage.clear()
    res = client.post("/api/files/download", headers=_auth(brandon, ana),
                      files=[("files", ("s.csv", io.BytesIO(csv), "text/csv"))], data={"file_format": "csv"})
    assert res.status_code == 200
    [event] = account_store._usage
    assert event["account_id"] == ana and event["consultant_id"]


def test_other_industry_is_saved_and_empty_means_nothing():
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Lu", "last_name": "Ma", "email": "lu@example.com",
        "company_name": "Lu Kitchen", "industry_other": "  Food   truck "})
    assert res.status_code == 201 and res.json()["account"]["industry_other"] == "Food truck"
    blank = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Jo", "last_name": "Ma", "email": "jo@example.com",
        "company_name": "Jo Co", "industry_other": "   "}).json()["account"]
    assert blank["industry_other"] is None and blank["naics_sector"] is None
    both = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Al", "last_name": "Ma", "email": "al@example.com",
        "company_name": "Al Co", "industry_other": "x", "industry_supersector": "service_providing",
        "naics_sector": "44-45"})
    assert both.status_code == 422
