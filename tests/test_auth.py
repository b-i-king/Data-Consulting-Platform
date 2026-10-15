"""
Email + password sign-in (api/services/auth.py, api/routers/accounts.py).

Runs in LOCAL mode (no SUPABASE_URL): passwords are scrypt-hashed by the API,
the same code path a laptop without Supabase uses. Supabase mode is checked
with a fake Supabase client, so no network is needed.
"""

import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, auth

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def fresh_store(monkeypatch):
    """
    Empty in-memory accounts and no Supabase settings, for every test.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.

    Returns:
        Iterator[None]: yields once while the test runs.
    """
    monkeypatch.setenv("SUPABASE_URL", "")
    monkeypatch.setenv("SUPABASE_KEY", "")
    monkeypatch.setenv("CONSULTANT_EMAILS", "coach@example.com")
    monkeypatch.setenv("CONSULTANT_ACCESS_CODE", "code-123")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email="ana@example.com", password=PW, **extra):
    """
    Post an individual sign-up and return the response.

    Args:
        email: Email for the new account.
        password: The password to choose (None to leave it out).
        **extra: Extra fields for the JSON body.

    Returns:
        httpx.Response: The TestClient response.
    """
    body = {"account_type": "individual", "first_name": "Ana", "last_name": "Lee", "email": email, **extra}
    if password is not None:
        body["password"] = password
    return client.post("/api/accounts/signup", json=body)


def test_hash_round_trip():
    """A local hash checks the right password, rejects a wrong one, and is salted (never equal twice)."""
    stored = auth.hash_password(PW)
    assert stored.startswith("scrypt$")
    assert auth.verify_password(PW, stored)
    assert not auth.verify_password("wrong-password", stored)
    assert auth.hash_password(PW) != stored          # a new random salt every time
    assert not auth.verify_password(PW, "garbage")    # malformed hash -> False, not a crash


def test_signup_needs_a_password_of_8_or_more():
    """Sign-up without a password, or with a short one, is refused with a clear 422."""
    assert _signup(password=None).status_code == 422
    short = _signup(password="short")
    assert short.status_code == 422
    assert "at least 8" in short.json()["detail"]


def test_signup_then_signin_with_password():
    """The right password signs in; a wrong password or unknown email get the same 401; no hash leaks."""
    res = _signup()
    assert res.status_code == 201
    assert res.json()["access_token"]
    assert "password_hash" not in res.json()["account"]
    assert account_store._accounts[res.json()["account"]["id"]]["password_hash"].startswith("scrypt$")

    ok = client.post("/api/accounts/signin", json={"email": "ANA@example.com", "password": PW})
    assert ok.status_code == 200 and ok.json()["access_token"]
    wrong = client.post("/api/accounts/signin", json={"email": "ana@example.com", "password": "not-it-at-all"})
    assert wrong.status_code == 401
    nobody = client.post("/api/accounts/signin", json={"email": "nobody@example.com", "password": PW})
    assert nobody.status_code == 401
    assert "Wrong email or password" in nobody.json()["detail"]


def test_invited_colleague_sets_password_on_first_signin():
    """A colleague added by the owner has no password; their first sign-in sets it, later ones must match."""
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": "ann@example.com",
        "company_name": "Ko Bakery", "password": PW,
        "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": "bo@example.com"}]})
    assert res.status_code == 201
    bo = next(a for a in account_store._accounts.values() if a["email"] == "bo@example.com")
    assert bo["status"] == "invited" and not auth.has_password(bo)

    first = client.post("/api/accounts/signin", json={"email": "bo@example.com", "password": "Bos-own-pass"})
    assert first.status_code == 200
    assert bo["status"] == "active" and auth.has_password(bo)
    again = client.post("/api/accounts/signin", json={"email": "bo@example.com", "password": "someone-else"})
    assert again.status_code == 401


def test_consultant_needs_code_and_password():
    """A consultant signs up with the access code and a password; sign-in needs both."""
    assert _signup("coach@example.com", access_code="code-123").status_code == 201
    no_code = client.post("/api/accounts/signin", json={"email": "coach@example.com", "password": PW})
    assert no_code.status_code == 403
    wrong_pw = client.post("/api/accounts/signin",
                           json={"email": "coach@example.com", "password": "nope-nope", "access_code": "code-123"})
    assert wrong_pw.status_code == 401
    ok = client.post("/api/accounts/signin", json={"email": "coach@example.com", "password": PW, "access_code": "code-123"})
    assert ok.status_code == 200 and ok.json()["account"]["role"] == "consultant"


def test_health_reports_auth_mode():
    """/health says which password checker is on (local here)."""
    assert client.get("/health").json()["auth"] == "local"


class _FakeUser:
    """Stands in for Supabase's User object (only `id` is used)."""

    def __init__(self, uid):
        self.id = uid


class _FakeResponse:
    """Stands in for Supabase's AuthResponse (user + session)."""

    def __init__(self, uid, session):
        self.user = _FakeUser(uid)
        self.session = session


class _FakeAuthError(Exception):
    """Like supabase_auth.errors.AuthApiError: carries a `code`."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class _FakeSupabase:
    """A tiny in-memory Supabase Auth: remembers email -> (id, password)."""

    users: dict = {}
    confirm_email = False

    class auth:  # noqa: N801 — mirrors supabase.auth
        @staticmethod
        def sign_up(creds):
            """Register a user; no session when email confirmation is on."""
            uid = f"00000000-0000-4000-8000-{len(_FakeSupabase.users):012d}"
            _FakeSupabase.users[creds["email"]] = (uid, creds["password"])
            return _FakeResponse(uid, None if _FakeSupabase.confirm_email else object())

        @staticmethod
        def sign_in_with_password(creds):
            """Check the password like Supabase does."""
            uid, pw = _FakeSupabase.users.get(creds["email"], (None, None))
            if pw != creds["password"]:
                raise _FakeAuthError("invalid_credentials")
            return _FakeResponse(uid, object())


def test_supabase_mode(monkeypatch):
    """With Supabase settings, passwords go to Supabase: the account stores its user id, never a hash."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_test")
    get_settings.cache_clear()
    _FakeSupabase.users = {}
    _FakeSupabase.confirm_email = False
    monkeypatch.setattr(auth, "_supabase", lambda: _FakeSupabase)
    assert auth.mode() == "supabase"

    res = _signup("sb@example.com")
    assert res.status_code == 201 and res.json()["access_token"]
    stored = next(a for a in account_store._accounts.values() if a["email"] == "sb@example.com")
    assert stored["auth_user_id"] and stored["password_hash"] is None

    assert client.post("/api/accounts/signin", json={"email": "sb@example.com", "password": PW}).status_code == 200
    bad = client.post("/api/accounts/signin", json={"email": "sb@example.com", "password": "wrong-one"})
    assert bad.status_code == 401

    # "Confirm email" switched on in Supabase: no token until the link is clicked.
    _FakeSupabase.confirm_email = True
    pending = _signup("confirm@example.com")
    assert pending.status_code == 201
    assert pending.json()["needs_confirmation"] is True and pending.json()["access_token"] is None
