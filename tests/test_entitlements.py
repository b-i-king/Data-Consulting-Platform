"""
Tests for the services on an account's plan (api/services/entitlements.py),
which decide what the signed-in workspace's left pane opens or grays out.
"""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def _signup(email: str, **extra) -> dict:
    res = client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "A", "last_name": "B", "email": email, **extra})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_cleaning_is_always_included_and_signup_choices_are_kept():
    h = _signup("plan1@example.com", service_ids=["ai", "data_visualization"])
    body = client.get("/api/accounts/services", headers=h).json()
    assert body["active"] == ["data_cleaning", "data_visualization", "ai"]       # catalog order
    assert body["can_add"] is True
    flags = {s["id"]: s["active"] for s in body["services"]}
    assert flags["data_science_ml"] is False and flags["data_cleaning"] is True


def test_adding_a_service_and_bad_ids():
    h = _signup("plan2@example.com")
    assert client.get("/api/accounts/services", headers=h).json()["active"] == ["data_cleaning"]
    res = client.post("/api/accounts/services", headers=h, json={"service_id": "data_science_ml"})
    assert res.status_code == 200 and "data_science_ml" in res.json()["active"]
    assert client.post("/api/accounts/services", headers=h, json={"service_id": "nope"}).status_code == 400
    assert client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "A", "last_name": "B",
        "email": "plan3@example.com", "service_ids": ["nope"]}).status_code == 400
    assert client.get("/api/accounts/services").status_code == 401


def test_team_members_share_the_owners_plan_but_cannot_change_it():
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "O", "last_name": "W", "email": "owner-plan@example.com",
        "company_name": "Plan Co", "service_ids": ["data_visualization"],
        "team_members": [{"first_name": "M", "last_name": "B", "email": "member-plan@example.com"}]})
    assert res.status_code == 201, res.text
    member = client.post("/api/accounts/signin", json={"email": "member-plan@example.com"}).json()
    h = {"Authorization": f"Bearer {member['access_token']}"}
    body = client.get("/api/accounts/services", headers=h).json()
    assert "data_visualization" in body["active"] and body["can_add"] is False
    assert client.post("/api/accounts/services", headers=h, json={"service_id": "ai"}).status_code == 403
