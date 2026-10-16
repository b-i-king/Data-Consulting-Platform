"""
Consultants, "done for you" clients, the access rule, admin approval,
capacity, picking a consultant by name, and the "Other" industry
(api/services/consultants.py, api/routers/consultants.py, api/routers/admin.py).
Runs on the in-memory account store, like the rest of the API tests.
"""

import io

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store

client = TestClient(app)


@pytest.fixture(autouse=True)
def consultant_settings(monkeypatch):
    """
    One admin (Brandon, the default consultant); fresh memory store.

    Runs automatically for every test: sets ADMIN_EMAILS, clears cached settings,
    and empties the in-memory accounts, teams and consultants. Settings are
    cleared again after the test.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.

    Returns:
        Iterator[None]: yields once while the test runs (setup before, cleanup after).
    """
    monkeypatch.setenv("ADMIN_EMAILS", "brandon@example.com")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email, first="Dana", **extra):
    """
    Post a signup for an individual account and return the raw response.

    Args:
        email: Email for the new account.
        first: First name (default "Dana"); the last name is always "Lee".
        **extra: Extra signup fields merged into the JSON body, e.g. wants_consultant=True
            or consultant_request=True.

    Returns:
        httpx.Response: The TestClient response (check .status_code, then .json()).

    Example:
        >>> _signup("ana@example.com", "Ana", wants_consultant=True).status_code
        201
    """
    body = {"password": "Correct-horse-9", "accept_terms": True, "account_type": "individual", "first_name": first, "last_name": "Lee", "email": email, **extra}
    return client.post("/api/accounts/signup", json=body)


def _auth(token, acting_as=None):
    """
    Build request headers for a signed-in user, optionally acting for a client.

    Args:
        token: The access token of the person making the request.
        acting_as: A client account id to work on (sent as X-Acting-As), or None to act as
            yourself. Only that client's own consultant is allowed (get_acting_context).

    Returns:
        dict[str, str]: Headers with "Authorization: Bearer <token>" and, when acting_as is
        given, "X-Acting-As: <client id>".

    Example:
        >>> _auth("abc", "acct-1")
        {'Authorization': 'Bearer abc', 'X-Acting-As': 'acct-1'}
    """
    h = {"Authorization": f"Bearer {token}"}
    if acting_as:
        h["X-Acting-As"] = acting_as
    return h


def _consultant(email="brandon@example.com", first="Brandon"):
    """
    Sign up a consultant: the admin (brandon@example.com) directly; anyone else
    by asking and being approved by the admin (who must already exist).

    Args:
        email: Consultant email (default "brandon@example.com", the admin and default consultant).
        first: Consultant first name (default "Brandon").

    Returns:
        str: The consultant's access token (for an approved consultant, from a fresh
        sign-in, so it carries the consultant role).

    Example:
        >>> token = _consultant()
        >>> client.get("/api/consultant/clients", headers=_auth(token)).status_code
        200
    """
    if email == "brandon@example.com":
        return _signup(email, first).json()["access_token"]
    asked = _signup(email, first, consultant_request=True).json()
    admin = client.post("/api/accounts/signin", json={"email": "brandon@example.com", "password": "Correct-horse-9"})
    res = client.post(f"/api/admin/consultant-requests/{asked['account']['id']}/approve",
                      headers=_auth(admin.json()["access_token"]))
    assert res.status_code == 200, res.text
    return client.post("/api/accounts/signin", json={"email": email, "password": "Correct-horse-9"}).json()["access_token"]


def test_admin_is_a_consultant_and_others_must_be_approved():
    """
    The ADMIN_EMAILS address is an admin and consultant at once (no access code).
    Anyone else who asks waits as a client until the admin approves (consultant at
    once) or denies (stays a client). Only admins see or decide requests.
    """
    admin = _signup("brandon@example.com", "Brandon").json()
    assert admin["account"]["role"] == "consultant" and admin["account"]["is_admin"]
    kim = _signup("kim@example.com", "Kim", consultant_request=True).json()
    lee = _signup("lee@example.com", "Lee", consultant_request=True).json()
    assert kim["account"]["role"] == "client" and kim["account"]["consultant_request"] == "pending"
    # Nobody but an admin sees the list or decides.
    assert client.get("/api/admin/consultant-requests", headers=_auth(kim["access_token"])).status_code == 403
    assert client.post(f"/api/admin/consultant-requests/{kim['account']['id']}/approve",
                       headers=_auth(kim["access_token"])).status_code == 403
    panel = client.get("/api/admin/consultant-requests", headers=_auth(admin["access_token"])).json()
    assert [(r["email"], r["status"]) for r in panel["requests"]] == [("kim@example.com", "pending"),
                                                                      ("lee@example.com", "pending")]
    h = _auth(admin["access_token"])
    client.post(f"/api/admin/consultant-requests/{kim['account']['id']}/approve", headers=h)
    panel = client.post(f"/api/admin/consultant-requests/{lee['account']['id']}/deny", headers=h).json()
    assert {r["email"]: r["status"] for r in panel["requests"]} == {"kim@example.com": "approved", "lee@example.com": "denied"}
    assert [c["name"] for c in panel["consultants"]] == ["Brandon", "Kim"]   # default consultant first
    # Approved: a consultant from the next page load (no new sign-in needed for /me).
    assert client.get("/api/accounts/me", headers=_auth(kim["access_token"])).json()["role"] == "consultant"
    assert client.get("/api/consultant/clients", headers=_auth(kim["access_token"])).status_code == 200
    # Denied: still a client, and may ask again.
    assert client.get("/api/accounts/me", headers=_auth(lee["access_token"])).json()["role"] == "client"
    again = client.post("/api/accounts/consultant-request", headers=_auth(lee["access_token"]))
    assert again.status_code == 200 and again.json()["consultant_request"] == "pending"
    assert client.post("/api/accounts/consultant-request", headers=_auth(lee["access_token"])).status_code == 409


def test_clients_pick_a_consultant_by_name_within_capacity():
    """
    A client picks a consultant by name and one-time or monthly help; the fill bar
    counts their clients; a full consultant can't be picked (409), and a client who
    picks nobody goes to whoever has room.
    """
    brandon = _consultant()
    kim = _consultant("kim@example.com", "Kim")
    kim_id = next(c["id"] for c in client.get("/api/accounts/consultants").json()["consultants"] if c["name"] == "Kim")
    # Kim takes one client at most.
    assert client.put("/api/consultant/settings", headers=_auth(kim), json={"capacity": 11}).status_code == 422
    settings = client.put("/api/consultant/settings", headers=_auth(kim), json={"capacity": 1}).json()
    assert settings["load"]["capacity"] == 1 and settings["max_capacity"] == 10

    ana = _signup("ana@example.com", "Ana", wants_consultant=True, consultant_id=kim_id, consultant_plan="monthly").json()
    assert ana["account"]["consultant_name"] == "Kim" and ana["account"]["consultant_plan"] == "monthly"
    [summary] = client.get("/api/consultant/clients", headers=_auth(kim)).json()["clients"]
    assert summary["consultant_plan"] == "monthly"
    kim_row = next(c for c in client.get("/api/intake/options").json()["consultants"] if c["name"] == "Kim")
    assert (kim_row["clients"], kim_row["percent"], kim_row["full"]) == (1, 100, True)
    assert "email" not in kim_row                            # guests never see emails

    # Kim is full: picking her is refused, and nothing is created.
    full = _signup("bo@example.com", "Bo", wants_consultant=True, consultant_id=kim_id)
    assert full.status_code == 409 and "fully booked" in full.json()["detail"]
    assert account_store._accounts and all(a["email"] != "bo@example.com" for a in account_store._accounts.values())
    # Picking nobody: the default consultant (Brandon) has room; one-time is the default.
    bo = _signup("bo@example.com", "Bo", wants_consultant=True).json()["account"]
    assert bo["consultant_name"] == "Brandon" and bo["consultant_plan"] == "one_time"
    assert client.get("/api/consultant/settings", headers=_auth(brandon)).json()["load"]["clients"] == 1
    # A consultant at capacity can't add more clients by hand either.
    added = client.post("/api/consultant/clients", headers=_auth(kim), json={
        "account_type": "individual", "first_name": "Cy", "last_name": "Ro", "email": "cy@example.com"})
    assert added.status_code == 409


def test_a_signed_in_client_can_choose_a_consultant_later():
    """POST /api/accounts/consultant: a do-it-yourself client asks for help after signing up."""
    _consultant()
    sam = _signup("sam@example.com", "Sam").json()
    assert sam["account"]["consultant_name"] is None
    plan = client.post("/api/accounts/consultant", headers=_auth(sam["access_token"]),
                       json={"consultant_plan": "monthly"}).json()
    assert plan["account"]["consultant_name"] == "Brandon" and plan["account"]["consultant_plan"] == "monthly"
    # Now consultant-managed: every available tool, including Data Engineering (to store data).
    assert "data_engineering" in plan["active"]


def test_done_for_you_clients_go_to_the_default_consultant_and_get_every_tool():
    """
    Check that a "done for you" client is assigned to the default consultant and gets every
    available tool, while a do-it-yourself client gets only Data Cleaning.
    """
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


def test_consultants_see_all_seven_services_including_ai_preview():
    """
    Check that a consultant sees every service, including AI (not launched yet), in their own
    workspace AND in a client's, while the client themselves still sees only launched services.
    """
    token = _consultant()
    every = [s["id"] for s in client.get("/api/accounts/services", headers=_auth(token)).json()["services"]]
    mine = client.get("/api/accounts/services", headers=_auth(token)).json()
    assert mine["active"] == every and "ai" in mine["active"] and len(every) == 7
    ana = _signup("ana@example.com", "Ana", wants_consultant=True).json()
    acting = client.get("/api/accounts/services", headers=_auth(token, ana["account"]["id"])).json()
    assert "ai" in acting["active"]
    own = client.get("/api/accounts/services", headers=_auth(ana["access_token"])).json()
    assert "ai" not in own["active"]


def test_clients_waiting_for_a_consultant_are_assigned_when_one_signs_in():
    """
    Check that clients who asked for a consultant before any existed are assigned to the
    default consultant when they sign up — oldest first, and only as many as their capacity.
    """
    for i in range(4):
        waiting = _signup(f"early{i}@example.com", "Early", wants_consultant=True).json()["account"]
        assert waiting["consultant_name"] is None            # nobody set up yet
    token = _consultant()
    clients = client.get("/api/consultant/clients", headers=_auth(token)).json()
    assert len(clients["clients"]) == 3 and clients["load"]["full"]   # capacity 3: the 4th still waits


def test_the_access_rule():
    """
    Check the access rule: a consultant can act for their own client but not another
    consultant's; a client can never act as anyone or list clients; each consultant sees only
    their own clients.
    """
    brandon = _consultant()
    other = _consultant("second@example.com", "Avery")
    mine = _signup("ana@example.com", "Ana", wants_consultant=True).json()
    theirs_id = client.post("/api/consultant/clients", headers=_auth(other), json={
        "password": "Correct-horse-9", "accept_terms": True, "account_type": "business", "first_name": "Kai", "last_name": "Ono", "email": "kai@example.com",
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
    """
    Check that a download a consultant makes while acting for a client is recorded as the
    client's usage, with the consultant's id attached.
    """
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
    """
    Check the "Other" industry field: extra spaces are tidied, blank means none, and giving both
    "Other" and a NAICS sector is rejected (422).
    """
    res = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "accept_terms": True, "account_type": "business", "first_name": "Lu", "last_name": "Ma", "email": "lu@example.com",
        "company_name": "Lu Kitchen", "industry_other": "  Food   truck "})
    assert res.status_code == 201 and res.json()["account"]["industry_other"] == "Food truck"
    blank = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "accept_terms": True, "account_type": "business", "first_name": "Jo", "last_name": "Ma", "email": "jo@example.com",
        "company_name": "Jo Co", "industry_other": "   "}).json()["account"]
    assert blank["industry_other"] is None and blank["naics_sector"] is None
    both = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "accept_terms": True, "account_type": "business", "first_name": "Al", "last_name": "Ma", "email": "al@example.com",
        "company_name": "Al Co", "industry_other": "x", "industry_supersector": "service_providing",
        "naics_sector": "44-45"})
    assert both.status_code == 422
