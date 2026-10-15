"""
Tests for what an account can use (api/services/entitlements.py): Data
Cleaning always; other tools only with a package (tests/test_billing.py).
Projects picked on "Your needs" are recorded as interest and unlock nothing.
"""

from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)


def _signup(email: str, **extra) -> dict:
    """
    Sign up an individual account (asserting it worked) and return its auth header.

    Args:
        email: Email for the new account; must be unused.
        **extra: Extra signup fields merged into the JSON body, e.g. service_ids=["ai"].

    Returns:
        dict: {"Authorization": "Bearer <token>"} for the new account.

    Raises:
        AssertionError: If signup does not return 201.

    Example:
        >>> h = _signup("plan9@example.com", service_ids=["ai"])
        >>> client.get("/api/accounts/services", headers=h).json()["active"]
        ['data_cleaning']
    """
    res = client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "A", "last_name": "B", "email": email, **extra})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


def test_projects_picked_at_signup_are_interest_only():
    """
    Check that services picked at signup are recorded as interests (in catalog order) and unlock
    nothing beyond Data Cleaning.
    """
    h = _signup("plan1@example.com", service_ids=["ai", "data_visualization"])
    body = client.get("/api/accounts/services", headers=h).json()
    assert body["active"] == ["data_cleaning"]                                    # nothing unlocked
    assert body["interests"] == ["data_visualization", "ai"]                      # catalog order
    assert body["can_add"] is True
    flags = {s["id"]: s["active"] for s in body["services"]}
    assert flags["data_visualization"] is False and flags["data_cleaning"] is True


def test_interest_in_a_project_and_bad_ids():
    """
    Check that adding a service later records interest without unlocking it, that unknown service
    ids are 400 (later or at signup), and that the endpoint needs sign-in (401).
    """
    h = _signup("plan2@example.com")
    assert client.get("/api/accounts/services", headers=h).json()["active"] == ["data_cleaning"]
    res = client.post("/api/accounts/services", headers=h, json={"service_id": "data_science_ml"})
    assert res.status_code == 200 and res.json()["interests"] == ["data_science_ml"]
    assert "data_science_ml" not in res.json()["active"]
    assert client.post("/api/accounts/services", headers=h, json={"service_id": "nope"}).status_code == 400
    assert client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "A", "last_name": "B",
        "email": "plan3@example.com", "service_ids": ["nope"]}).status_code == 400
    assert client.get("/api/accounts/services").status_code == 401


def test_team_members_share_the_owners_package_but_cannot_change_it():
    """
    Check that a team member shares the owner's package and interests but cannot add services
    (can_add is False and adding returns 403).
    """
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "O", "last_name": "W", "email": "owner-plan@example.com",
        "company_name": "Plan Co", "service_ids": ["data_visualization"],
        "team_members": [{"first_name": "M", "last_name": "B", "email": "member-plan@example.com"}]})
    assert res.status_code == 201, res.text
    owner = {"Authorization": f"Bearer {res.json()['access_token']}"}
    client.post("/api/billing/checkout", headers=owner, json={"plan_ids": ["data_visualization"], "interval": "month"})
    member = client.post("/api/accounts/signin", json={"email": "member-plan@example.com"}).json()
    h = {"Authorization": f"Bearer {member['access_token']}"}
    body = client.get("/api/accounts/services", headers=h).json()
    assert "data_visualization" in body["active"] and body["can_add"] is False
    assert body["interests"] == ["data_visualization"]
    assert client.post("/api/accounts/services", headers=h, json={"service_id": "ai"}).status_code == 403
