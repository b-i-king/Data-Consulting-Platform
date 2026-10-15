"""
Tests for the signup "foolproof" rules (api/services/validation.py), the city
picker (api/services/locations.py) and one-email-per-account.
"""

import pytest
from fastapi.testclient import TestClient

from api.main import app
from api.services import locations
from api.services.validation import clean_company_name, clean_email, clean_person_name

client = TestClient(app)


@pytest.mark.parametrize("raw, clean", [
    ("José María", "José María"), ("O'Brien-Smith", "O'Brien-Smith"), ("Nguyễn", "Nguyễn"),
    ("D’Angelo", "D’Angelo"), ("  Ana   Lee ", "Ana Lee"), ("李", "李"), ("Zoë", "Zoë"),
])
def test_person_names_allow_accents_and_name_punctuation(raw, clean):
    """Check person names keep accents, apostrophes and hyphens, with extra spaces squeezed.

    Args:
        raw: str, the name as typed.
        clean: str, the expected cleaned name.
    """
    assert clean_person_name(raw) == clean


@pytest.mark.parametrize("raw, why", [
    ("ana", "capital"), ("Ana3", "only use letters"), ("Ana--Lee", "two separators"),
    ("Ana-", "end with"), ("", "required"), ("Ana!", "only use letters"), ("-Ana", "start with a letter"),
])
def test_person_names_reject_bad_input(raw, why):
    """Check bad person names raise ValueError with a helpful message.

    Args:
        raw: str, the invalid name as typed.
        why: str, text the error message must contain.
    """
    with pytest.raises(ValueError, match=why):
        clean_person_name(raw)


def test_company_names():
    """Check real company names (with &, digits, accents) pass and bad ones raise ValueError."""
    assert clean_company_name("Ben & Jerry's, LLC") == "Ben & Jerry's, LLC"
    assert clean_company_name("1-800-Flowers.com") == "1-800-Flowers.com"
    assert clean_company_name("Café Olé") == "Café Olé"
    for bad in ["A", "Acme 🚀", "<script>", "-Acme"]:
        with pytest.raises(ValueError):
            clean_company_name(bad)


def test_emails_are_checked_and_lowercased():
    """Check emails are trimmed and lowercased, and malformed ones raise ValueError."""
    assert clean_email("  Ana@Example.COM ") == "ana@example.com"
    for bad in ["ana@", "ana@example", "a b@x.com", "ana.example.com"]:
        with pytest.raises(ValueError):
            clean_email(bad)


def test_signup_rejects_bad_names_and_a_missing_business_name():
    """Check signup returns 422 for a bad name, a bad email, or a business with no company name."""
    base = {"password": "Correct-horse-9", "account_type": "individual", "first_name": "Ana", "last_name": "Lee", "email": "v1@example.com"}
    assert client.post("/api/accounts/signup", json={**base, "first_name": "ana"}).status_code == 422
    assert client.post("/api/accounts/signup", json={**base, "email": "not-an-email"}).status_code == 422
    assert client.post("/api/accounts/signup", json={**base, "password": "Correct-horse-9", "account_type": "business"}).status_code == 422


def test_one_email_per_account_case_insensitive_and_no_half_signups():
    """Check an email can be used only once (any case), and a failed team signup creates nothing."""
    base = {"password": "Correct-horse-9", "account_type": "individual", "first_name": "Ana", "last_name": "Lee"}
    assert client.post("/api/accounts/signup", json={**base, "email": "once@example.com"}).status_code == 201
    again = client.post("/api/accounts/signup", json={**base, "email": "ONCE@Example.com"})
    assert again.status_code == 409 and "only be used once" in again.json()["detail"]
    # Same email twice in one form.
    biz = {"password": "Correct-horse-9", "account_type": "business", "first_name": "Bo", "last_name": "Kim", "company_name": "Kim Co"}
    dup = client.post("/api/accounts/signup", json={**biz, "email": "bo@example.com",
                      "team_members": [{"first_name": "Al", "last_name": "Ray", "email": "BO@example.com"}]})
    assert dup.status_code == 422 and "more than once" in dup.text
    # A team member's email is already taken: nothing is created, so the owner can retry.
    taken = client.post("/api/accounts/signup", json={**biz, "email": "bo2@example.com",
                        "team_members": [{"first_name": "Al", "last_name": "Ray", "email": "once@example.com"}]})
    assert taken.status_code == 409
    retry = client.post("/api/accounts/signup", json={**biz, "email": "bo2@example.com"})
    assert retry.status_code == 201


def test_location_search_and_signup():
    """Check city search (accent-insensitive, 2+ letters) and that signup saves a valid location id."""
    hits = client.get("/api/locations", params={"q": "atl"}).json()
    assert hits[0]["label"] == "Atlanta, Georgia, United States"
    assert locations.search("sao paulo")[0]["city"] == "São Paulo"            # accent-insensitive
    assert locations.search("paris, tex")[0]["label"] == "Paris, Texas, United States"
    assert locations.search("a") == []                                        # 2 letters minimum
    res = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "account_type": "individual", "first_name": "Lu", "last_name": "Mo", "email": "loc@example.com",
        "location_id": hits[0]["id"]})
    assert res.status_code == 201 and res.json()["account"]["location"] == "Atlanta, Georgia, United States"
    bad = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "account_type": "individual", "first_name": "Lu", "last_name": "Mo", "email": "loc2@example.com",
        "location_id": 1})
    assert bad.status_code == 422
