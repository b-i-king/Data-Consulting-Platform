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
from tests.consultant_helpers import APPLICATION, apply_as_consultant

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
    for store in (account_store._accounts, account_store._teams, account_store._consultants,
                  account_store._invites, account_store._applications):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email, first="Dana", **extra):
    """
    Post a signup for an individual account and return the raw response.

    Args:
        email: Email for the new account.
        first: First name (default "Dana"); the last name is always "Lee".
        **extra: Extra signup fields merged into the JSON body, e.g. wants_consultant=True.

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
    by being invited, applying and being approved by the admin (who must already exist).

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
    asked = _signup(email, first).json()
    admin = client.post("/api/accounts/signin", json={"email": "brandon@example.com", "password": "Correct-horse-9"})
    applied = apply_as_consultant(client, _auth(admin.json()["access_token"]), asked["access_token"], email)
    assert applied.status_code == 200, applied.text
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
    kim = _signup("kim@example.com", "Kim").json()
    lee = _signup("lee@example.com", "Lee").json()
    for who in (kim, lee):
        res = apply_as_consultant(client, _auth(admin["access_token"]), who["access_token"], who["account"]["email"])
        assert res.status_code == 200, res.text
        who["account"] = res.json()
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
    panel = client.post(f"/api/admin/consultant-requests/{lee['account']['id']}/deny", headers=h,
                        json={"denial_reason": "work_sample",
                              "note": "The work sample link was empty; please share a finished project."}).json()
    assert {r["email"]: r["status"] for r in panel["requests"]} == {"kim@example.com": "approved", "lee@example.com": "denied"}
    assert [c["name"] for c in panel["consultants"]] == ["Brandon", "Kim"]   # default consultant first
    # Approved: a consultant from the next page load (no new sign-in needed for /me).
    assert client.get("/api/accounts/me", headers=_auth(kim["access_token"])).json()["role"] == "consultant"
    assert client.get("/api/consultant/clients", headers=_auth(kim["access_token"])).status_code == 200
    # Denied: still a client, and must wait before applying again (test_consultant_screening).
    assert client.get("/api/accounts/me", headers=_auth(lee["access_token"])).json()["role"] == "client"
    again = client.post("/api/accounts/consultant-request", headers=_auth(lee["access_token"]), json=APPLICATION)
    assert again.status_code == 409 and "apply again on" in again.json()["detail"]


def test_signup_choice_and_signing_in_as_a_consultant():
    """
    The clean choice at sign-up: Client, Request to be a consultant, or I'm an
    admin (refused for a non-admin email). At sign-in, "Consultant" works only
    for approved consultants; "Account" works for everyone.
    """
    fake = _signup("eve@example.com", "Eve", signup_as="admin")
    assert fake.status_code == 403 and "admin list" in fake.json()["detail"]
    assert all(a["email"] != "eve@example.com" for a in account_store._accounts.values())   # nothing created
    admin = _signup("brandon@example.com", "Brandon", signup_as="admin").json()["account"]
    assert admin["is_admin"] and admin["role"] == "consultant"
    # "Request to be a consultant" at sign-up is gone: the account comes first, then the application.
    refused = _signup("kim@example.com", "Kim", signup_as="consultant")
    assert refused.status_code == 400 and "Create your account first" in refused.json()["detail"]
    assert _signup("kim@example.com", "Kim", consultant_request=True).status_code == 400
    kim_token = _signup("kim@example.com", "Kim").json()["access_token"]
    admin_token = client.post("/api/accounts/signin", json={"email": "brandon@example.com",
                                                             "password": "Correct-horse-9"}).json()["access_token"]
    assert apply_as_consultant(client, _auth(admin_token), kim_token, "kim@example.com").status_code == 200

    def sign_in(email, as_consultant):
        return client.post("/api/accounts/signin", json={"email": email, "password": "Correct-horse-9",
                                                         "as_consultant": as_consultant})
    assert sign_in("brandon@example.com", True).status_code == 200
    waiting = sign_in("kim@example.com", True)
    assert waiting.status_code == 403 and "waiting for approval" in waiting.json()["detail"]
    assert sign_in("kim@example.com", False).status_code == 200
    _signup("sam@example.com", "Sam")
    assert sign_in("sam@example.com", True).status_code == 403
    assert sign_in("sam@example.com", True).json()["detail"].startswith("This account isn't a consultant")


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

    ana = _signup("ana@example.com", "Ana", wants_consultant=True, consultant_id=kim_id, consultant_plan="monthly",
                  service_ids=["data_visualization", "data_science_ml"]).json()
    assert ana["account"]["consultant_name"] == "Kim" and ana["account"]["consultant_plan"] == "monthly"
    [summary] = client.get("/api/consultant/clients", headers=_auth(kim)).json()["clients"]
    assert summary["consultant_plan"] == "monthly"
    # The "Working on" picker filters on what they need help with, and sorts by last sign-in.
    assert summary["interested_services"] == ["data_visualization", "data_science_ml"]
    assert summary["last_login_at"]
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
    # A consultant doesn't unlock tools: the client's package does.
    assert plan["active"] == ["data_cleaning"]


def test_done_for_you_clients_go_to_the_default_consultant_and_pay_for_tools():
    """
    Check that a "done for you" client is assigned to the default consultant, but tools still
    come from their package: without one, only Data Cleaning — same as a do-it-yourself client.
    """
    _consultant()
    opts = client.get("/api/intake/options").json()
    assert opts["consultant"] == {"name": "Brandon"}
    res = _signup("ana@example.com", "Ana", wants_consultant=True)
    assert res.status_code == 201
    acc = res.json()["account"]
    assert acc["wants_consultant"] and acc["consultant_name"] == "Brandon"
    plan = client.get("/api/accounts/services", headers=_auth(res.json()["access_token"])).json()
    assert plan["active"] == ["data_cleaning"]               # no package yet
    client.post("/api/billing/checkout", headers=_auth(res.json()["access_token"]),
                json={"plan_ids": ["data_engineering"], "interval": "month"})   # test mode: on at once
    plan = client.get("/api/accounts/services", headers=_auth(res.json()["access_token"])).json()
    assert "data_engineering" in plan["active"]

    diy = _signup("sam@example.com", "Sam").json()
    assert diy["account"]["consultant_name"] is None
    plan = client.get("/api/accounts/services", headers=_auth(diy["access_token"])).json()
    assert plan["active"] == ["data_cleaning"]


def test_admins_get_everything_free_consultants_get_what_the_client_pays_for():
    """
    An admin gets all seven services free (AI as a preview) in their own workspace AND a
    client's, downloads included, and can't buy anything. A consultant gets only what the
    client's package pays for, and needs their own package for their own data.
    """
    admin = _consultant()
    every = [s["id"] for s in client.get("/api/accounts/services", headers=_auth(admin)).json()["services"]]
    mine = client.get("/api/accounts/services", headers=_auth(admin)).json()
    assert mine["active"] == every and "ai" in mine["active"] and len(every) == 7
    assert mine["admin"] and mine["downloads_included"]
    buy = client.post("/api/billing/checkout", headers=_auth(admin), json={"plan_ids": ["data_visualization"], "interval": "month"})
    assert buy.status_code == 400 and "free" in buy.json()["detail"]

    kim = _consultant("kim@example.com", "Kim")
    ana = _signup("ana@example.com", "Ana", wants_consultant=True,
                  consultant_id=next(c["id"] for c in client.get("/api/accounts/consultants").json()["consultants"]
                                     if c["name"] == "Kim")).json()
    client.post("/api/billing/checkout", headers=_auth(ana["access_token"]),
                json={"plan_ids": ["data_visualization"], "interval": "month"})
    acting = client.get("/api/accounts/services", headers=_auth(kim, ana["account"]["id"])).json()
    assert acting["active"] == ["data_cleaning", "data_visualization"] and not acting["admin"]
    own = client.get("/api/accounts/services", headers=_auth(kim)).json()
    assert own["active"] == ["data_cleaning"]                # Kim's own data: her own package
    # The admin, working on Kim's client, still gets everything.
    assert "ai" in client.get("/api/accounts/services", headers=_auth(admin, ana["account"]["id"])).json()["active"]


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
    assert clients["load"]["clients"] == 3 and clients["load"]["full"]   # capacity 3: the 4th still waits
    assert len(clients["clients"]) == 4          # the admin still SEES every client (and can open them)


def test_the_access_rule():
    """
    Check the access rule: a consultant can act for their own client but not another
    consultant's (an admin can act for any client); a client can never act as anyone or list
    clients; each consultant sees only their own clients.
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
    # ...but not on another consultant's client (Avery can't open Brandon's) — the admin can.
    assert client.get("/api/accounts/services", headers=_auth(other, mine_id)).status_code == 403
    assert client.get("/api/accounts/services", headers=_auth(brandon, theirs_id)).status_code == 200
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


def test_the_consultant_directory_is_paged():
    """GET /api/accounts/consultants: pages of consultants, those with room first, filter by name."""
    _consultant()
    for name in ["Kim", "Lee", "Max"]:
        _consultant(f"{name.lower()}@example.com", name)
    first = client.get("/api/accounts/consultants?page=1&page_size=2").json()
    assert (first["total"], first["pages"], len(first["consultants"])) == (4, 2, 2)
    assert first["consultants"][0]["name"] == "Brandon"             # the default consultant first
    second = client.get("/api/accounts/consultants?page=2&page_size=2").json()
    assert {c["name"] for c in first["consultants"] + second["consultants"]} == {"Brandon", "Kim", "Lee", "Max"}
    assert [c["name"] for c in client.get("/api/accounts/consultants?q=ki").json()["consultants"]] == ["Kim"]


def test_delete_my_account_removes_everything():
    """
    DELETE /api/accounts/me (type your email): a business owner takes the team, the package,
    consents and stored data with them; a deleted consultant's clients wait for a new one.
    """
    from api.services import warehouse_store

    _consultant()
    kim = _consultant("kim@example.com", "Kim")
    kim_id = next(c["id"] for c in client.get("/api/accounts/consultants").json()["consultants"] if c["name"] == "Kim")
    owner = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "accept_terms": True, "account_type": "business", "first_name": "Ann",
        "last_name": "Ko", "email": "ann@example.com", "company_name": "Ko Bakery", "wants_consultant": True,
        "consultant_id": kim_id, "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": "bo@example.com"}]}).json()
    t = owner["access_token"]
    client.post("/api/billing/checkout", headers=_auth(t), json={"plan_ids": ["data_engineering"], "interval": "month"})
    csv = b"order_id,amount\n" + b"".join(f"{i},{i}.5\n".encode() for i in range(40))
    upload = [("files", ("o.csv", csv, "text/csv"))]
    fp = client.post("/api/warehouse/preview", headers=_auth(t), files=upload).json()["fingerprint"]
    client.post("/api/warehouse/load", headers=_auth(t), files=upload, data={"fingerprint": fp, "consent": "true"})
    assert any(k[0] == owner["account"]["id"] for k in warehouse_store._stored)

    wrong = client.request("DELETE", "/api/accounts/me", headers=_auth(t), json={"confirm_email": "nope@example.com"})
    assert wrong.status_code == 400
    gone = client.request("DELETE", "/api/accounts/me", headers=_auth(t), json={"confirm_email": "ANN@example.com"})
    assert gone.status_code == 200 and gone.json() == {"deleted": 2}          # owner + colleague
    emails = {a["email"] for a in account_store._accounts.values()}
    assert "ann@example.com" not in emails and "bo@example.com" not in emails
    ann_id = owner["account"]["id"]
    assert not any(k[0] == ann_id for k in warehouse_store._stored)
    assert not any(sub["account_id"] == ann_id for sub in account_store._subscriptions.values())
    assert client.get("/api/accounts/me", headers=_auth(t)).status_code == 401

    # A consultant deletes their account: their clients go back to waiting.
    sam = _signup("sam@example.com", "Sam", wants_consultant=True, consultant_id=kim_id).json()
    assert sam["account"]["consultant_name"] == "Kim"
    client.request("DELETE", "/api/accounts/me", headers=_auth(kim), json={"confirm_email": "kim@example.com"})
    me = client.get("/api/accounts/me", headers=_auth(sam["access_token"])).json()
    assert me["consultant_name"] is None and me["wants_consultant"]


def test_admin_moves_a_client_to_another_consultant():
    """Admin reassigns a client: the new consultant can open them, the old one can't, old ongoing help ends now."""
    import asyncio

    admin = _signup("brandon@example.com", "Brandon", signup_as="admin").json()
    h = _auth(admin["access_token"])
    kim, lee = _signup("kim@example.com", "Kim").json(), _signup("lee@example.com", "Lee").json()
    for who in (kim, lee):
        apply_as_consultant(client, h, who["access_token"], who["account"]["email"])
        client.post(f"/api/admin/consultant-requests/{who['account']['id']}/approve", headers=h)
    kim_c = asyncio.run(account_store.get_consultant_by_account(kim["account"]["id"]))
    lee_c = asyncio.run(account_store.get_consultant_by_account(lee["account"]["id"]))
    ana = _signup("ana@example.com", "Ana", wants_consultant=True, consultant_id=kim_c["id"]).json()
    ana_id = ana["account"]["id"]
    assert account_store._accounts[ana_id]["consultant_id"] == kim_c["id"]
    asyncio.run(account_store.create_engagement(account_id=ana_id, consultant_id=kim_c["id"], kind="retainer",
                                                amount_usd=200.0, platform_fee_percent=20, status="active",
                                                billing_interval="month"))

    url = f"/api/admin/clients/{ana_id}/reassign"
    assert client.post(url, headers=_auth(kim["access_token"]), json={"consultant_id": lee_c["id"]}).status_code == 403
    assert client.post(url, headers=h, json={"consultant_id": kim_c["id"]}).status_code == 400        # already theirs
    assert client.post(url, headers=h, json={"consultant_id": "nope"}).status_code == 404
    assert client.post(f"/api/admin/clients/{kim['account']['id']}/reassign", headers=h,
                       json={"consultant_id": lee_c["id"]}).status_code == 400                       # a consultant
    moved = client.post(url, headers=h, json={"consultant_id": lee_c["id"]}).json()
    assert moved["from"] == "Kim Lee" and moved["to"] == "Lee Lee" and moved["client"] == "Ana Lee"
    assert [e["amount_usd"] for e in moved["help_ended"]] == [200.0]                                  # refund to-do
    assert account_store._accounts[ana_id]["consultant_id"] == lee_c["id"]
    # Lee can open Ana's workspace now; Kim can't.
    assert client.get("/api/accounts/services", headers=_auth(lee["access_token"], ana_id)).status_code == 200
    assert client.get("/api/accounts/services", headers=_auth(kim["access_token"], ana_id)).status_code == 403
