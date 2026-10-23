"""
Tests for "Connect Google Sheets" (api/services/google_sheets.py): sign in
(read-only), paste a link, list the tabs, open one as CSV — live, nothing stored.
Google is faked (httpx.MockTransport), like the other OAuth tests.
"""

from __future__ import annotations

from urllib.parse import parse_qs, unquote, urlparse

import httpx
import pytest

from api.core.config import get_settings
from api.services import account_store, oauth
from tests.test_oauth_integrations import FakeProviders, _connect, _signup, _token_route, client

SHEET = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
LINK = f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=0"


@pytest.fixture(autouse=True)
def google_keys(monkeypatch):
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    for key, value in {"GOOGLE_CLIENT_ID": "g-id", "GOOGLE_CLIENT_SECRET": "g-secret",
                       "PUBLIC_API_URL": "https://api.example.com", "SITE_URL": "https://site.example.com"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    account_store._accounts.clear()
    account_store._connections.clear()
    yield
    oauth.HTTP_TRANSPORT = None
    get_settings.cache_clear()


def _google() -> FakeProviders:
    fake = FakeProviders({})

    def values(request: httpx.Request):
        assert unquote(request.url.path).endswith("'Sales'!A1:GR20001")    # tab quoted, range capped
        return {"values": [["date", "item", "amount"], ["2026-10-01", "Cake", "12.5"], ["2026-10-02", "Pie"]]}

    fake.routes.update({
        ("POST", "https://oauth2.googleapis.com/token"): _token_route(fake, "google"),
        ("GET", "https://openidconnect.googleapis.com/v1/userinfo"): {"email": "owner@sunrisebakery.com"},
        ("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET}"): {
            "properties": {"title": "Daily sales"},
            "sheets": [{"properties": {"title": "Sales", "gridProperties": {"rowCount": 1000, "columnCount": 26}}}]},
        ("GET", f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET}/values/%27Sales%27%21A1%3AGR20001"): values,
    })
    return fake


def test_connect_google_sheets_read_only_and_open_a_tab_live():
    fake = _google()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()
    url = client.get("/api/connections/google_sheets/authorize", headers=h).json()["url"]
    q = parse_qs(urlparse(url).query)
    assert q["scope"] == ["openid email https://www.googleapis.com/auth/spreadsheets.readonly"]   # read-only
    assert q["access_type"] == ["offline"] and q["code_challenge_method"] == ["S256"]
    assert _connect(h, "google_sheets").headers["location"].endswith("connected=1")
    listed = {c["connector"]: c for c in client.get("/api/connections", headers=h).json()["connectors"]}
    assert listed["google_sheets"]["status"] == "connected" and "owner@sunrisebakery.com" in listed["google_sheets"]["display_name"]

    tabs = client.get("/api/connections/google_sheets/tabs", headers=h, params={"link": LINK}).json()
    assert tabs == {"id": SHEET, "title": "Daily sales", "tabs": [{"title": "Sales", "rows": 1000, "columns": 26}]}
    csv = client.get("/api/connections/google_sheets/tab", headers=h, params={"link": LINK, "tab": "Sales"})
    assert csv.status_code == 200 and csv.headers["X-Rows"] == "2"
    assert csv.text.splitlines() == ["date,item,amount", "2026-10-01,Cake,12.5", "2026-10-02,Pie,"]   # short row padded
    assert client.get("/api/connections/google_sheets/tabs", headers=h,
                      params={"link": "https://example.com/not-a-sheet"}).status_code == 400
