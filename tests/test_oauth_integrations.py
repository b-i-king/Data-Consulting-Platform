"""
Tests for the "Connect" integrations: QuickBooks, Xero and "Sign in with Supabase"
(api/services/oauth.py, business_tools.py, cloud_sources.py Management API part,
api/routers/connections.py).

No real provider is called: oauth.HTTP_TRANSPORT is an httpx.MockTransport that
answers like Intuit, Xero and Supabase do (shapes from their API docs).
"""

from __future__ import annotations

import json
import time
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, business_tools, connections, oauth

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Local mode, empty stores, keys for all three providers, and no real HTTP."""
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    for key, value in {"QUICKBOOKS_CLIENT_ID": "qb-id", "QUICKBOOKS_CLIENT_SECRET": "qb-secret",
                       "XERO_CLIENT_ID": "xero-id", "XERO_CLIENT_SECRET": "xero-secret",
                       "SUPABASE_OAUTH_CLIENT_ID": "sb-id", "SUPABASE_OAUTH_CLIENT_SECRET": "sb-secret",
                       "PUBLIC_API_URL": "https://api.example.com", "SITE_URL": "https://site.example.com"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants,
                  account_store._connections, account_store._subscriptions, business_tools._minute_calls):
        store.clear()
    yield
    oauth.HTTP_TRANSPORT = None
    get_settings.cache_clear()


def _signup(email="ana@example.com"):
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Ana", "last_name": "Lee",
                                                    "email": email, "password": PW, "accept_terms": True})
    assert res.status_code == 201, res.text
    return {"Authorization": f"Bearer {res.json()['access_token']}"}


class FakeProviders:
    """Answers like the real APIs; records every call so tests can check what was sent."""

    def __init__(self, routes: dict):
        self.routes = routes        # (method, url without query) -> function(request) or JSON
        self.calls: list[httpx.Request] = []
        self.refresh_count = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        key = (request.method, str(request.url).split("?")[0])
        if key not in self.routes:
            return httpx.Response(404, json={"message": f"no fake for {key}"})
        answer = self.routes[key]
        if callable(answer):
            answer = answer(request)
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


def _token_route(fake: FakeProviders, prefix: str):
    """A token endpoint: the code swap and refreshes (each refresh rotates the refresh token)."""
    def handle(request: httpx.Request):
        form = parse_qs(request.content.decode())
        assert request.headers["authorization"].startswith("Basic ")      # client id + secret, HTTP Basic
        if form["grant_type"] == ["authorization_code"]:
            assert form["redirect_uri"] == ["https://api.example.com/api/connections/oauth/callback"]
            return {"access_token": f"{prefix}-access-1", "refresh_token": f"{prefix}-refresh-1", "expires_in": 1800}
        fake.refresh_count += 1
        return {"access_token": f"{prefix}-access-{fake.refresh_count + 1}",
                "refresh_token": f"{prefix}-refresh-{fake.refresh_count + 1}", "expires_in": 1800}
    return handle


def _connect(h, provider: str, **callback_params) -> httpx.Response:
    """Click Connect, 'sign in' at the provider, and land on our callback."""
    url = client.get(f"/api/connections/{provider}/authorize", headers=h).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    return client.get("/api/connections/oauth/callback", params={"state": state, "code": "one-time-code", **callback_params},
                      follow_redirects=False)


# ── Keys and the state ticket ────────────────────────────────────────────────

def test_connect_buttons_appear_only_with_keys(monkeypatch):
    """No keys -> QuickBooks and Xero show as coming soon; with keys they can be connected."""
    h = _signup()
    listed = {c["connector"]: c for c in client.get("/api/connections", headers=h).json()["connectors"]}
    assert listed["xero"]["available"] and listed["xero"]["sign_in"] and listed["quickbooks"]["live"]
    assert "Xero" not in {p["name"] for p in connections.COMING_SOON}
    monkeypatch.setenv("XERO_CLIENT_ID", "")
    get_settings.cache_clear()
    listed = {c["connector"]: c for c in client.get("/api/connections", headers=h).json()["connectors"]}
    assert not listed["xero"]["available"] and not listed["xero"]["sign_in"]
    assert client.get("/api/connections/xero/authorize", headers=h).status_code == 503


def test_state_ticket_is_encrypted_and_expires(monkeypatch):
    state = oauth.make_state("xero", "acct-1", None)
    assert "acct-1" not in state
    assert oauth.read_state(state)["account_id"] == "acct-1"
    with pytest.raises(Exception, match="expired"):
        oauth.read_state(state[:-4] + "AAAA")                 # changed -> refused
    monkeypatch.setattr(oauth, "STATE_TTL_SECONDS", -120)
    with pytest.raises(Exception, match="expired"):
        oauth.read_state(state)                               # too old -> refused


def test_authorize_urls_ask_for_read_only_access():
    h = _signup()
    xero = urlparse(client.get("/api/connections/xero/authorize", headers=h).json()["url"])
    q = parse_qs(xero.query)
    assert xero.netloc == "login.xero.com" and q["redirect_uri"] == ["https://api.example.com/api/connections/oauth/callback"]
    assert "accounting.invoices.read" in q["scope"][0] and "offline_access" in q["scope"][0]
    assert "accounting.invoices " not in q["scope"][0] + " "                              # never write access
    qb = parse_qs(urlparse(client.get("/api/connections/quickbooks/authorize", headers=h).json()["url"]).query)
    assert qb["scope"] == ["com.intuit.quickbooks.accounting"]
    sb = parse_qs(urlparse(client.get("/api/connections/supabase/authorize", headers=h).json()["url"]).query)
    assert sb["code_challenge_method"] == ["S256"] and "scope" not in sb               # PKCE; scopes live on the app


def test_bad_callback_goes_back_with_a_message():
    res = client.get("/api/connections/oauth/callback", params={"state": "nonsense", "code": "x"}, follow_redirects=False)
    assert res.status_code == 303 and res.headers["location"].startswith("https://site.example.com/app?")
    assert "integration_error=" in res.headers["location"]


# ── Xero ─────────────────────────────────────────────────────────────────────

XERO_INVOICES = [
    {"Type": "ACCREC", "Status": "PAID", "DateString": "2026-01-15T00:00:00", "SubTotal": 1000.0, "Total": 1080.0,
     "AmountDue": 0, "CurrencyCode": "USD", "Contact": {"Name": "Blue Door Cafe"}},
    {"Type": "ACCREC", "Status": "AUTHORISED", "DateString": "2026-02-03T00:00:00", "SubTotal": 500.0, "Total": 540.0,
     "AmountDue": 540.0, "CurrencyCode": "USD", "Contact": {"Name": "Corner Market"}},
    {"Type": "ACCREC", "Status": "DRAFT", "DateString": "2026-02-10T00:00:00", "SubTotal": 9999.0,
     "Contact": {"Name": "Not counted"}},
    {"Type": "ACCPAY", "Status": "PAID", "Date": "/Date(1768694400000+0000)/", "SubTotal": 300.0, "AmountDue": 0,
     "Contact": {"Name": "Flour Supplier"}},
]
XERO_BANK = [
    {"Type": "RECEIVE", "Status": "AUTHORISED", "DateString": "2026-02-20T00:00:00", "SubTotal": 200.0,
     "Contact": {"Name": "Blue Door Cafe"}},
    {"Type": "SPEND", "Status": "AUTHORISED", "DateString": "2026-02-21T00:00:00", "SubTotal": 150.0},
    {"Type": "SPEND-TRANSFER", "Status": "AUTHORISED", "DateString": "2026-02-22T00:00:00", "SubTotal": 5000.0},
]


def test_xero_numbers_are_added_up_correctly():
    """Drafts, transfers and tax are left out; unpaid amounts come from AmountDue."""
    unpaid = [i for i in XERO_INVOICES if i["Status"] == "AUTHORISED"] + [{"Type": "ACCPAY", "AmountDue": 75.5}]
    s = business_tools.xero_summarize(XERO_INVOICES, XERO_BANK, unpaid)
    assert s["totals"] == {"income": 1700.0, "expenses": 450.0, "net_income": 1250.0, "owed_to_you": 540.0, "you_owe": 75.5}
    assert s["monthly"] == [{"month": "2026-01", "income": 1000.0, "expenses": 300.0, "net": 700.0},
                            {"month": "2026-02", "income": 700.0, "expenses": 150.0, "net": 550.0}]
    assert s["top_customers"][0] == {"name": "Blue Door Cafe", "amount": 1200.0}
    assert business_tools.xero_date("/Date(1767225600000+0000)/") == "2026-01-01"


def _xero_fake() -> FakeProviders:
    fake = FakeProviders({})
    api = business_tools.XERO_API

    def invoices(request):
        p = request.url.params
        assert request.headers["xero-tenant-id"] == "tenant-1"
        if p.get("pageSize") == "1":                                   # "does it have records?"
            return {"Invoices": XERO_INVOICES[:1] if 'ACCREC' in p.get("where", "") else []}
        if p.get("Statuses") == "AUTHORISED":
            return {"Invoices": [XERO_INVOICES[1]], "pagination": {"page": 1, "pageCount": 1}}
        rows = [i for i in XERO_INVOICES if 'ACCREC' not in p.get("where", "") or i["Type"] == "ACCREC"]
        return {"Invoices": rows, "pagination": {"page": int(p["page"]), "pageCount": 1}}

    fake.routes.update({
        ("POST", "https://identity.xero.com/connect/token"): _token_route(fake, "xero"),
        ("GET", "https://api.xero.com/connections"): [{"id": "conn-1", "tenantId": "tenant-1", "tenantType": "ORGANISATION",
                                                       "tenantName": "Sam's Bakery"}],
        ("GET", f"{api}/Invoices"): invoices,
        ("GET", f"{api}/BankTransactions"): lambda r: {"BankTransactions": XERO_BANK if r.url.params.get("pageSize") != "1" else XERO_BANK[:1]},
        ("GET", f"{api}/Payments"): {"Payments": []},
        ("GET", f"{api}/Contacts"): {"Contacts": [{"ContactID": "c1", "Name": "Blue Door Cafe", "IsCustomer": True}]},
        ("DELETE", "https://api.xero.com/connections/conn-1"): httpx.Response(204),
        ("POST", "https://identity.xero.com/connect/revocation"): httpx.Response(200),
    })
    return fake


def test_xero_connect_quick_look_open_and_disconnect():
    """The whole Xero flow, live: nothing about the books is stored, only the encrypted tokens."""
    fake = _xero_fake()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()

    landed = _connect(h, "xero")
    assert landed.status_code == 303 and landed.headers["location"] == "https://site.example.com/app?integration=xero&connected=1"
    row = next(c for c in account_store._connections.values() if c["connector"] == "xero")
    assert row["display_name"] == "Sam's Bakery" and row["status"] == "connected"
    assert "xero-access-1" not in row["credentials_encrypted"] and "tenant-1" not in row["credentials_encrypted"]

    look = client.get("/api/connections/xero/quick-look?start=2026-01-01&end=2026-10-16", headers=h).json()
    assert look["stored"] is False and look["estimate"] is True and look["company"] == "Sam's Bakery"
    assert look["totals"]["income"] == 1700.0 and look["totals"]["owed_to_you"] == 540.0
    period = next(c for c in fake.calls if c.url.params.get("summaryOnly") == "true" and "where" in c.url.params)
    assert "DateTime(2026, 01, 01)" in period.url.params["where"]                      # only the asked-for dates

    sets = {d["id"]: d["has_rows"] for d in client.get("/api/connections/xero/datasets", headers=h).json()["datasets"]}
    assert sets["sales_invoices"] and not sets["bills"] and not sets["payments"]       # empty ones flagged
    csv = client.get("/api/connections/xero/dataset?id=sales_invoices", headers=h)
    assert csv.status_code == 200 and "Contact.Name" in csv.text.splitlines()[0] and csv.headers["X-Rows"] == "3"
    assert client.get("/api/connections/xero/dataset?id=payments", headers=h).status_code == 422

    # An expired access token is refreshed once, and the ROTATED refresh token is saved.
    creds = oauth.unpack(row["credentials_encrypted"])
    creds["tokens"]["expires_at"] = int(time.time()) - 10
    account_store._connections[next(k for k in account_store._connections if k[1] == "xero")]["credentials_encrypted"] = \
        oauth.pack(creds["tokens"], creds["extra"])
    client.get("/api/connections/xero/datasets", headers=h)
    assert fake.refresh_count == 1
    saved = oauth.unpack(next(c for c in account_store._connections.values() if c["connector"] == "xero")["credentials_encrypted"])
    assert saved["tokens"]["refresh_token"] == "xero-refresh-2"

    gone = client.delete("/api/connections/xero", headers=h).json()["connectors"]
    assert next(c for c in gone if c["connector"] == "xero")["status"] == "not_set_up"
    assert any(c.method == "DELETE" and c.url.path == "/connections/conn-1" for c in fake.calls)   # frees a Xero slot
    assert any("revocation" in str(c.url) for c in fake.calls)


# ── QuickBooks ───────────────────────────────────────────────────────────────

def _money_col(title, start=None, key=None):
    meta = ([{"Name": "StartDate", "Value": start}] if start else []) + [{"Name": "ColKey", "Value": key or title}]
    return {"ColTitle": title, "ColType": "Money", "MetaData": meta}


QB_PNL = {
    "Header": {"ReportName": "ProfitAndLoss", "Currency": "USD"},
    "Columns": {"Column": [{"ColTitle": "", "ColType": "Account"}, _money_col("Jan 2026", "2026-01-01"),
                           _money_col("Feb 2026", "2026-02-01"), _money_col("Total", key="total")]},
    "Rows": {"Row": [
        {"type": "Section", "group": "Income", "Header": {"ColData": [{"value": "Income"}]},
         "Rows": {"Row": [{"type": "Data", "ColData": [{"value": "Sales"}, {"value": "1,000.00"}, {"value": "500.00"}, {"value": "1,500.00"}]}]},
         "Summary": {"ColData": [{"value": "Total Income"}, {"value": "1000.00"}, {"value": "500.00"}, {"value": "1500.00"}]}},
        {"type": "Section", "group": "COGS", "Summary": {"ColData": [{"value": "Total COGS"}, {"value": "100.00"}, {"value": ""}, {"value": "100.00"}]}},
        {"type": "Section", "group": "GrossProfit", "Summary": {"ColData": [{"value": "Gross Profit"}, {"value": "900.00"}, {"value": "500.00"}, {"value": "1400.00"}]}},
        {"type": "Section", "group": "Expenses", "Summary": {"ColData": [{"value": "Total Expenses"}, {"value": "200.00"}, {"value": "300.00"}, {"value": "500.00"}]}},
        {"type": "Section", "group": "NetIncome", "Summary": {"ColData": [{"value": "Net Income"}, {"value": "700.00"}, {"value": "200.00"}, {"value": "900.00"}]}},
    ]},
}
QB_CUSTOMERS = {"Rows": {"Row": [
    {"type": "Data", "ColData": [{"value": "Corner Market"}, {"value": "400.00"}]},
    {"type": "Section", "Header": {"ColData": [{"value": "Blue Door Cafe"}]},
     "Rows": {"Row": [{"type": "Data", "ColData": [{"value": "Blue Door Cafe: Midtown"}, {"value": "1100.00"}]}]}},
    {"type": "Section", "group": "GrandTotal", "Summary": {"ColData": [{"value": "TOTAL"}, {"value": "1500.00"}]}},
]}}


def _aging(total):
    return {"Rows": {"Row": [{"type": "Section", "group": "GrandTotal",
                              "Summary": {"ColData": [{"value": "TOTAL"}, {"value": "1"}, {"value": total}]}}]}}


def test_quickbooks_reports_are_read_like_quickbooks_shows_them():
    parsed = business_tools.parse_qb_pnl(QB_PNL)
    assert parsed["totals"] == {"income": 1500.0, "expenses": 600.0, "net_income": 900.0}
    assert parsed["monthly"][0] == {"month": "2026-01", "income": 1000.0, "expenses": 300.0, "net": 700.0}
    assert parsed["monthly"][1]["net"] == 200.0
    assert [c["name"] for c in business_tools.parse_qb_customer_sales(QB_CUSTOMERS)] == ["Blue Door Cafe: Midtown", "Corner Market"]
    assert business_tools.parse_qb_grand_total(_aging("2,345.67")) == 2345.67


def test_quickbooks_connect_quick_look_and_open_invoices():
    base = "https://sandbox-quickbooks.api.intuit.com/v3/company/9130"
    invoices = [{"Id": "130", "TxnDate": "2026-01-14", "TotalAmt": 96.0, "CustomerRef": {"value": "1", "name": "Blue Door Cafe"},
                 "Line": [{"Amount": 60.0, "DetailType": "SalesItemLineDetail"}, {"Amount": 36.0, "DetailType": "SalesItemLineDetail"}]}]

    def query(request):
        q = request.url.params["query"]
        assert request.url.params["minorversion"] == business_tools.QB_MINOR_VERSION
        if "COUNT(*)" in q:
            return {"QueryResponse": {"totalCount": 1 if "Invoice" in q else 0}}
        return {"QueryResponse": {"Invoice": invoices}}

    fake = FakeProviders({})
    fake.routes.update({
        ("POST", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"): _token_route(fake, "qb"),
        ("GET", f"{base}/companyinfo/9130"): {"CompanyInfo": {"CompanyName": "Sam's Bakery LLC"}},
        ("GET", f"{base}/reports/ProfitAndLoss"): QB_PNL,
        ("GET", f"{base}/reports/CustomerSales"): QB_CUSTOMERS,
        ("GET", f"{base}/reports/AgedReceivables"): _aging("640.00"),
        ("GET", f"{base}/reports/AgedPayables"): _aging("120.00"),
        ("GET", f"{base}/query"): query,
    })
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()
    assert _connect(h, "quickbooks", realmId="9130").headers["location"].endswith("integration=quickbooks&connected=1")

    look = client.get("/api/connections/quickbooks/quick-look?start=2026-01-01&end=2026-02-28", headers=h).json()
    assert look["company"] == "Sam's Bakery LLC" and look["estimate"] is False
    assert look["totals"] == {"income": 1500.0, "expenses": 600.0, "net_income": 900.0, "owed_to_you": 640.0, "you_owe": 120.0}
    pnl_call = next(c for c in fake.calls if c.url.path.endswith("ProfitAndLoss"))
    assert pnl_call.url.params["summarize_column_by"] == "Month" and pnl_call.url.params["start_date"] == "2026-01-01"

    sets = client.get("/api/connections/quickbooks/datasets", headers=h).json()["datasets"]
    assert [d["id"] for d in sets if d["has_rows"]] == ["Invoice"]
    csv = client.get("/api/connections/quickbooks/dataset?id=Invoice", headers=h)
    lines = csv.text.strip().splitlines()
    assert csv.status_code == 200 and len(lines) == 4                     # header + invoice header line + 2 item lines
    assert "line_amount" in lines[0] and "customer_name" in lines[0]      # the platform's unified names
    assert client.get("/api/connections/quickbooks/dataset?id=Bill", headers=h).status_code == 422   # no bills
    assert client.get("/api/connections/quickbooks/dataset?id=Customer;DROP", headers=h).status_code == 404


def test_quickbooks_callback_without_company_is_refused():
    fake = FakeProviders({})
    fake.routes[("POST", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer")] = _token_route(fake, "qb")
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    landed = _connect(_signup(), "quickbooks")                                       # no realmId
    assert "integration_error=" in landed.headers["location"]
    assert not account_store._connections


# ── Sign in with Supabase ────────────────────────────────────────────────────

def test_sign_in_with_supabase_pick_project_and_read_in_pages(monkeypatch):
    """Two projects -> pick one; tables found through the read-only API; a table read in ctid pages."""
    from api.services import cloud_sources

    monkeypatch.setattr(cloud_sources, "CHUNK_ROWS", 2)
    rows = [{"__ctid": f"(0,{i})", "id": i, "store": "Midtown" if i % 2 else "Decatur"} for i in range(1, 6)]
    queries: list[str] = []

    def run(request):
        assert request.url.path.endswith("/database/query/read-only")         # never the read-write endpoint
        sql = json.loads(request.content)["query"]
        queries.append(sql)
        if "information_schema.tables" in sql:
            return httpx.Response(201, json=[
                {"schema": "public", "name": "orders", "kind": "table", "rows_estimate": -1},
                {"schema": "public", "name": "returns", "kind": "table", "rows_estimate": 0}])
        if "UNION ALL" in sql or sql.startswith("SELECT 0 AS i"):
            return httpx.Response(201, json=[{"i": 0, "has": True}, {"i": 1, "has": False}])
        after = 0
        if "ctid >" in sql:
            after = int(sql.split("'(0,")[1].split(")")[0])
        want = int(sql.rsplit("LIMIT", 1)[1])
        return httpx.Response(201, json=[r for r in rows if int(r["__ctid"][3:-1]) > after][:want])

    fake = FakeProviders({})
    fake.routes.update({
        ("POST", "https://api.supabase.com/v1/oauth/token"): _token_route(fake, "sb"),
        ("GET", "https://api.supabase.com/v1/projects"): [{"id": "aaaa", "name": "Shop"}, {"id": "bbbb", "name": "Blog"}],
        ("POST", "https://api.supabase.com/v1/projects/aaaa/database/query/read-only"): run,
    })
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()

    assert _connect(h, "supabase").headers["location"].endswith("integration=supabase&connected=1")
    token_call = next(c for c in fake.calls if c.url.path.endswith("/oauth/token"))
    assert "code_verifier" in parse_qs(token_call.content.decode())               # PKCE verifier sent with the swap
    assert client.get("/api/connections/supabase/tables", headers=h).json()["needs_project"] is True
    assert client.get("/api/connections/supabase/projects", headers=h).json()["selected"] is None
    assert client.put("/api/connections/supabase/project", headers=h, json={"ref": "zzzz"}).status_code == 404
    picked = client.put("/api/connections/supabase/project", headers=h, json={"ref": "aaaa"}).json()["connectors"]
    assert next(c for c in picked if c["connector"] == "supabase")["display_name"] == "Supabase: Shop"

    listing = client.get("/api/connections/supabase/tables", headers=h).json()
    assert {t["name"]: t["has_rows"] for t in listing["tables"]} == {"orders": True, "returns": False}
    assert client.get("/api/connections/supabase/table?schema=public&name=returns", headers=h).status_code == 422

    csv = client.get("/api/connections/supabase/table?schema=public&name=orders", headers=h)
    assert csv.status_code == 200 and csv.headers["X-Rows"] == "5" and csv.headers["X-Truncated"] == "false"
    assert csv.text.strip().splitlines()[0] == "id,store" and "__ctid" not in csv.text
    assert sum("ORDER BY ctid" in q for q in queries) == 3                        # 2 + 2 + 1 rows, nothing read twice

    capped = client.get("/api/connections/supabase/table?schema=public&name=orders&limit=3", headers=h)
    assert capped.headers["X-Rows"] == "3" and capped.headers["X-Truncated"] == "true"


# ── Xero's plan limits: 5 connections, 1,000 calls a day per organization ────

def _xero_row():
    return next(c for c in account_store._connections.values() if c["connector"] == "xero")


def test_xero_connection_limit_is_never_passed(monkeypatch):
    """At the limit, Connect is refused; a sign-in that finishes anyway is undone at Xero too."""
    monkeypatch.setenv("XERO_MAX_CONNECTIONS", "1")
    get_settings.cache_clear()
    fake = _xero_fake()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    ana, bo = _signup("ana@example.com"), _signup("bo@example.com")

    # Bo starts signing in first; Ana finishes first and takes the only spot.
    bo_url = client.get("/api/connections/xero/authorize", headers=bo).json()["url"]
    assert _connect(ana, "xero").headers["location"].endswith("connected=1")
    slots = client.get("/api/connections", headers=ana).json()["slots"]
    assert slots["xero"] == {"used": 1, "max": 1} and slots["quickbooks"] == {"used": 0, "max": 15}

    full = client.get("/api/connections/xero/authorize", headers=bo)
    assert full.status_code == 409 and "full" in full.json()["detail"]
    assert client.get("/api/connections/xero/authorize", headers=ana).status_code == 200   # reconnecting is fine

    fake.calls.clear()
    bo_state = parse_qs(urlparse(bo_url).query)["state"][0]
    late = client.get("/api/connections/oauth/callback", params={"state": bo_state, "code": "c"}, follow_redirects=False)
    assert "integration_error=" in late.headers["location"] and "full" in parse_qs(urlparse(late.headers["location"]).query)["integration_error"][0]
    assert any(c.method == "DELETE" and c.url.path == "/connections/conn-1" for c in fake.calls)   # undone at Xero
    assert any("revocation" in str(c.url) for c in fake.calls)
    assert sum(1 for k in account_store._connections if k[1] == "xero") == 1                     # still only Ana


def test_extra_organizations_from_one_sign_in_are_disconnected():
    """Sharing two organizations would use two slots: we keep the first and free the other."""
    fake = _xero_fake()
    fake.routes[("GET", "https://api.xero.com/connections")] = [
        {"id": "conn-1", "tenantId": "tenant-1", "tenantType": "ORGANISATION", "tenantName": "Sam's Bakery"},
        {"id": "conn-2", "tenantId": "tenant-2", "tenantType": "ORGANISATION", "tenantName": "Sam's Catering"}]
    fake.routes[("DELETE", "https://api.xero.com/connections/conn-2")] = httpx.Response(204)
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    _connect(_signup(), "xero")
    assert _xero_row()["display_name"] == "Sam's Bakery"
    assert any(c.method == "DELETE" and c.url.path == "/connections/conn-2" for c in fake.calls)
    assert not any(c.method == "DELETE" and c.url.path == "/connections/conn-1" for c in fake.calls)


def test_xero_daily_calls_stop_before_the_limit(monkeypatch):
    """A daily limit of 10 -> we stop at 9 (90%); the count is saved and starts over the next day."""
    from datetime import date, timedelta

    monkeypatch.setenv("XERO_DAILY_CALL_LIMIT", "10")
    get_settings.cache_clear()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(_xero_fake())
    h = _signup()
    _connect(h, "xero")

    assert client.get("/api/connections/xero/datasets", headers=h).status_code == 200          # 5 calls
    xero = next(c for c in client.get("/api/connections", headers=h).json()["connectors"] if c["connector"] == "xero")
    assert xero["calls_today"] == 5 and xero["calls_budget"] == 9
    over = client.get("/api/connections/xero/datasets", headers=h)                              # needs 5 more, 4 left
    assert over.status_code == 429 and "used up" in over.json()["detail"]
    assert _xero_row()["api_calls_count"] == 9                                                 # saved even though it failed

    _xero_row()["api_calls_day"] = date.today() - timedelta(days=2)                             # a new day
    assert client.get("/api/connections/xero/datasets", headers=h).status_code == 200


def test_xero_own_count_wins_and_its_429_is_explained(monkeypatch):
    fake = _xero_fake()
    old = fake.routes[("GET", f"{business_tools.XERO_API}/Contacts")]
    # Xero says only 100 of 1,000 calls are left today (e.g. used by an earlier server).
    fake.routes[("GET", f"{business_tools.XERO_API}/Contacts")] = lambda r: httpx.Response(
        200, json=old, headers={"X-DayLimit-Remaining": "100"})
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()
    _connect(h, "xero")
    res = client.get("/api/connections/xero/dataset?id=contacts", headers=h)
    assert res.status_code == 200 and _xero_row()["api_calls_count"] == 900                    # believed Xero
    assert client.get("/api/connections/xero/quick-look", headers=h).status_code == 429        # budget (900) reached

    _xero_row()["api_calls_count"] = 0
    fake.routes[("GET", f"{business_tools.XERO_API}/Contacts")] = httpx.Response(
        429, headers={"X-Rate-Limit-Problem": "day", "Retry-After": "3600"})
    res = client.get("/api/connections/xero/dataset?id=contacts", headers=h)
    assert res.status_code == 429 and "used up" in res.json()["detail"]
    assert _xero_row()["api_calls_count"] >= 900                                               # stop for the day


def test_xero_minute_guard(monkeypatch):
    """Xero allows 60 a minute per organization: past our guard, a request waits or is refused."""
    monkeypatch.setitem(business_tools.LIMITS["xero"], "minute", 2)
    monkeypatch.setattr(business_tools, "MINUTE_MAX_WAIT_S", 0)
    oauth.HTTP_TRANSPORT = httpx.MockTransport(_xero_fake())
    h = _signup()
    _connect(h, "xero")
    res = client.get("/api/connections/xero/datasets", headers=h)                              # 5 calls in a row
    assert res.status_code == 429 and "a minute" in res.json()["detail"]


# ── QuickBooks' limits: 500,000 calls a month for the whole app ──────────────

def _qb_fake() -> FakeProviders:
    base = "https://sandbox-quickbooks.api.intuit.com/v3/company/9130"
    fake = FakeProviders({})
    fake.routes.update({
        ("POST", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"): _token_route(fake, "qb"),
        ("POST", "https://developer.api.intuit.com/v2/oauth2/tokens/revoke"): httpx.Response(200),
        ("GET", f"{base}/companyinfo/9130"): {"CompanyInfo": {"CompanyName": "Sam's Bakery LLC"}},
        ("GET", f"{base}/reports/ProfitAndLoss"): QB_PNL,
        ("GET", f"{base}/reports/CustomerSales"): QB_CUSTOMERS,
        ("GET", f"{base}/reports/AgedReceivables"): _aging("0"),
        ("GET", f"{base}/reports/AgedPayables"): _aging("0"),
        ("GET", f"{base}/query"): {"QueryResponse": {"totalCount": 0}},
    })
    return fake


def test_quickbooks_limits_keep_the_free_month_safe(monkeypatch):
    """Connection cap, a daily share per company, and the app-wide monthly allowance (stops at 90%)."""
    from datetime import datetime, timedelta, timezone

    for key, value in {"QUICKBOOKS_MAX_CONNECTIONS": "1", "QUICKBOOKS_DAILY_CALL_LIMIT": "10",
                       "QUICKBOOKS_MONTHLY_CALL_LIMIT": "20"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    monkeypatch.setattr(account_store, "_api_usage", {})
    fake = _qb_fake()
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    ana, bo = _signup("ana@example.com"), _signup("bo@example.com")
    month = datetime.now(timezone.utc).date().replace(day=1)

    assert _connect(ana, "quickbooks", realmId="9130").headers["location"].endswith("connected=1")
    full = client.get("/api/connections/quickbooks/authorize", headers=bo)
    assert full.status_code == 409 and "QuickBooks connections are full" in full.json()["detail"]

    assert client.get("/api/connections/quickbooks/datasets", headers=ana).status_code == 200      # 5 COUNT calls
    qb = next(c for c in client.get("/api/connections", headers=ana).json()["connectors"] if c["connector"] == "quickbooks")
    assert qb["calls_today"] == 5 and qb["calls_budget"] == 10
    assert account_store._api_usage[("quickbooks", month)] == 5                                    # the month counts too
    assert client.get("/api/connections/quickbooks/quick-look", headers=ana).status_code == 200    # 4 reports -> 9
    daily = client.get("/api/connections/quickbooks/datasets", headers=ana)                        # 10th ok, 11th refused
    assert daily.status_code == 429 and "Today's QuickBooks allowance" in daily.json()["detail"]
    assert account_store._api_usage[("quickbooks", month)] == 10

    # A new day for this company, but the whole app is near its monthly allowance (90% of 20 = 18).
    row = next(c for c in account_store._connections.values() if c["connector"] == "quickbooks")
    row["api_calls_day"] = datetime.now(timezone.utc).date() - timedelta(days=1)
    account_store._api_usage[("quickbooks", month)] = 16
    monthly = client.get("/api/connections/quickbooks/datasets", headers=ana)                      # 2 calls, then stop
    assert monthly.status_code == 429 and "this month's allowance" in monthly.json()["detail"]
    assert account_store._api_usage[("quickbooks", month)] == 18


def test_default_limits_fit_quickbooks_free_month():
    """15 companies x 1,000 calls x 30 days = 450,000 = 90% of the 500,000 free calls."""
    s = get_settings()
    assert s.quickbooks_max_connections * business_tools.daily_budget("quickbooks") * 30 \
        <= business_tools.monthly_budget("quickbooks") == 450_000
    assert business_tools.daily_budget("xero") == 900 and s.xero_max_connections == 5
