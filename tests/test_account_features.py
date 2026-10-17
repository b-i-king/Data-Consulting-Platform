"""
Forgot / reset password, business context, dashboard tabs + suggestions, the
question log, and integrations (QuickBooks + a client's own Supabase database).

(api/services/auth.py, dashboards.py, cloud_sources.py, connections.py;
api/routers/accounts.py, dashboards.py, connections.py.) Runs on the in-memory
store; the client's database is faked here (a real PostgreSQL check is run by
hand: docs/RUN_THROUGH.md).
"""

import asyncio

import pandas as pd
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, auth, cloud_sources, dashboards

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Local password mode, one admin, no database, empty stores."""
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants, account_store._tabs,
                  account_store._questions, account_store._connections, account_store._subscriptions):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email="ana@example.com", **extra):
    """Sign up an individual and return the JSON reply."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Ana", "last_name": "Lee",
                                                    "email": email, "password": PW, "accept_terms": True, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ── Forgot / reset password ──────────────────────────────────────────────────

def test_forgot_and_reset_password_local_mode(monkeypatch):
    """Same reply for any email; the emailed (here: logged) link sets a new password once."""
    links = []
    monkeypatch.setattr(auth.log, "warning", lambda msg, *args: links.append(msg % args))
    _signup()
    unknown = client.post("/api/accounts/forgot-password", json={"email": "nobody@example.com"}).json()
    known = client.post("/api/accounts/forgot-password", json={"email": "ANA@example.com"}).json()
    assert unknown == known and "If that email has an account" in known["message"]
    assert len(links) == 1 and "/reset-password?token=lr_" in links[0]
    token = links[0].split("token=")[1]
    stored = next(iter(account_store._accounts.values()))
    assert stored["password_reset_hash"] and token not in str(stored)        # only a hash is kept

    assert client.post("/api/accounts/reset-password", json={"token": token, "password": "short"}).status_code == 422
    ok = client.post("/api/accounts/reset-password", json={"token": token, "password": "A-brand-new-pass"})
    assert ok.status_code == 200 and ok.json()["email"] == "ana@example.com"
    assert client.post("/api/accounts/signin", json={"email": "ana@example.com", "password": PW}).status_code == 401
    assert client.post("/api/accounts/signin", json={"email": "ana@example.com", "password": "A-brand-new-pass"}).status_code == 200
    again = client.post("/api/accounts/reset-password", json={"token": token, "password": "Another-new-pass"})
    assert again.status_code == 400 and "expired" in again.json()["detail"]        # used once only


def test_reset_link_from_the_account_page(monkeypatch):
    """Signed in: "Reset password" emails a link to my own address."""
    monkeypatch.setattr(auth.log, "warning", lambda *a: None)
    me = _signup()
    res = client.post("/api/accounts/reset-link", headers=_h(me["access_token"]))
    assert res.status_code == 200 and "ana@example.com" in res.json()["message"]


def test_supabase_reset_uses_the_links_access_token(monkeypatch):
    """Supabase mode: the new password goes to Supabase with the recovery token; the account is found by its id."""
    monkeypatch.setenv("SUPABASE_URL", "https://abc.supabase.co")
    monkeypatch.setenv("SUPABASE_KEY", "sb_publishable_x")
    get_settings.cache_clear()
    acc = {"id": "a1", "email": "sb@example.com", "auth_user_id": "11111111-1111-1111-1111-111111111111",
           "first_name": "S", "last_name": "B", "account_type": "individual"}
    account_store._accounts["a1"] = acc
    sent = {}

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def put(self, url, json, headers):
            sent.update(url=url, json=json, headers=headers)

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"id": acc["auth_user_id"]}
            return R()

    monkeypatch.setattr(auth.httpx, "AsyncClient", FakeClient)
    res = client.post("/api/accounts/reset-password", json={"token": "aaa.bbb.ccc", "password": "A-brand-new-pass"})
    assert res.status_code == 200 and res.json()["email"] == "sb@example.com"
    assert sent["url"] == "https://abc.supabase.co/auth/v1/user" and sent["headers"]["Authorization"] == "Bearer aaa.bbb.ccc"


# ── Business context, suggestions, tabs, question log ─────────────────────────

COLUMNS = [{"name": "order_date", "type": "date"}, {"name": "product", "type": "text"}, {"name": "store", "type": "text"},
           {"name": "amount", "type": "numeric"}, {"name": "payment_method", "type": "text"},
           {"name": "customer_type", "type": "text"}]


def test_suggestions_follow_the_industry_and_the_table():
    """Retail gets retail questions, written with this table's own column names."""
    found = dashboards.suggest(COLUMNS, "44-45", {}, [])
    assert found[0] == {"question": "top 5 product by amount", "why": "Best sellers", "source": "industry"}
    assert {"total amount by store", "how many rows by payment_method"} <= {s["question"] for s in found}
    # Their own key questions come first; questions asked twice or more are suggested too.
    mine = dashboards.suggest(COLUMNS, None, {"key_questions": ["average amount by store"]},
                              [{"question": "total amount by product", "times": 3}])
    assert [s["source"] for s in mine[:2]] == ["yours", "asked"]
    assert dashboards.industry_label("44-45") == "Retail Trade"


def test_business_context_tabs_and_question_log():
    """Context is saved on the account; tabs need Data Visualization; repeated questions become suggestions."""
    me = _signup(company_name=None)
    h = _h(me["access_token"])
    ctx = client.put("/api/accounts/business-context", headers=h,
                     json={"about": "  Two bakeries in   Atlanta ", "goals": "Grow catering", "key_questions": ["top 5 product by amount", ""]})
    assert ctx.json()["context"] == {"about": "Two bakeries in Atlanta", "goals": "Grow catering",
                                     "key_questions": ["top 5 product by amount"]}
    tab = {"name": "Sales", "questions": ["total amount by store"]}
    assert client.post("/api/dashboards/tabs", headers=h, json=tab).status_code == 403       # no Data Visualization
    client.post("/api/billing/checkout", headers=h, json={"plan_ids": ["data_visualization"], "interval": "month"})
    made = client.post("/api/dashboards/tabs", headers=h, json=tab)
    assert made.status_code == 201 and [t["name"] for t in made.json()["tabs"]] == ["Sales"]
    tab_id = made.json()["tabs"][0]["id"]
    renamed = client.put(f"/api/dashboards/tabs/{tab_id}", headers=h, json={"name": "By store", "questions": ["total amount by store", "TOTAL amount by store"]})
    assert renamed.json()["tabs"][0] == {**renamed.json()["tabs"][0], "name": "By store", "questions": ["total amount by store"]}
    assert client.post("/api/dashboards/tabs", headers=h, json={"name": "Empty", "questions": [" "]}).status_code == 400
    assert client.put("/api/dashboards/tabs/not-a-tab", headers=h, json=tab).status_code == 404

    for _ in range(2):
        client.post("/api/questions", headers=h, json={"question": "average amount by customer_type"})
    client.post("/api/questions", headers=h, json={"question": "what is love", "answered": False})
    board = client.get("/api/dashboards", headers=h).json()
    assert board["frequent"] == [{"question": "average amount by customer_type", "times": 2}]
    assert board["context"]["goals"] == "Grow catering"
    sug = client.post("/api/dashboards/suggestions", headers=h, json={"columns": COLUMNS}).json()["suggestions"]
    assert [s["source"] for s in sug[:2]] == ["yours", "asked"]
    assert client.delete(f"/api/dashboards/tabs/{tab_id}", headers=h).json()["tabs"] == []


# ── Integrations ───────────────────────────────────────────────────────────

def test_cloud_source_safety_helpers():
    """Logins are encrypted (and read back); only postgres:// strings; private hosts refused."""
    secret = "postgresql://ro:p%40ss@db.abcd.supabase.co:5432/postgres"
    token = cloud_sources.encrypt(secret)
    assert "p%40ss" not in token and cloud_sources.decrypt(token) == secret
    assert cloud_sources.parse_dsn(secret)["label"] == "db.abcd.supabase.co / postgres"
    with pytest.raises(HTTPException):
        cloud_sources.parse_dsn("mysql://x@y/z")
    for host in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "localhost"):
        with pytest.raises(HTTPException, match="private"):
            cloud_sources.check_host(host)


def test_schema_summary_counts_tables_with_data():
    """Per schema: tables, and how many have rows ("don't know" counts as may-have-rows)."""
    tables = [{"schema": "public", "has_rows": True}, {"schema": "public", "has_rows": False},
              {"schema": "sales", "has_rows": None}, {"schema": "staging", "has_rows": False}]
    assert cloud_sources.summarize_schemas(tables) == [
        {"schema": "public", "tables": 2, "with_rows": 1},
        {"schema": "sales", "tables": 1, "with_rows": 1},
        {"schema": "staging", "tables": 1, "with_rows": 0},
    ]


def test_empty_upload_is_refused_before_cleaning():
    """A file with column names but no rows costs nothing: 422 straight after reading."""
    res = client.post("/api/files/analyze", files={"files": ("empty.csv", b"store,amount\n", "text/csv")})
    assert res.status_code == 422 and "no rows" in res.json()["detail"]
    # With a non-empty file alongside, the empty one is just left out.
    res = client.post("/api/files/analyze", files=[("files", ("empty.csv", b"store,amount\n", "text/csv")),
                                                   ("files", ("sales.csv", b"store,amount\nMidtown,4\n", "text/csv"))])
    assert res.status_code == 200
    assert [d["name"] for d in res.json()["categorization"]["datasets"]] == ["sales.csv"]


def test_question_log_keeps_only_the_newest(monkeypatch):
    """The question log is capped per account, so it never grows without end."""
    monkeypatch.setattr(account_store, "QUESTION_LOG_KEEP", 3)
    monkeypatch.setattr(account_store, "_questions", [])
    for i in range(5):
        asyncio.run(account_store.log_question("acct-1", f"q{i}", True))
    asyncio.run(account_store.log_question("acct-2", "other", True))
    assert [q["question"] for q in account_store._questions if q["account_id"] == "acct-1"] == ["q2", "q3", "q4"]
    assert len(account_store._questions) == 4


def test_connect_a_supabase_database_read_only(monkeypatch):
    """
    Integrations lists both kinds; connecting saves the login ENCRYPTED; tables and a
    table (as CSV) come from the client's database; disconnecting deletes the login.
    """
    me = _signup()
    h = _h(me["access_token"])
    listed = client.get("/api/connections", headers=h).json()
    kinds = {c["connector"]: c["kind"] for c in listed["connectors"]}
    assert kinds == {"quickbooks": "business_tool", "xero": "business_tool", "wave": "business_tool",
                     "supabase": "cloud_storage"}
    assert listed["kinds"]["cloud_storage"] == "Cloud storage"
    # The roadmap is listed per section, and none of it can be set up yet.
    planned = {(p["kind"], p["name"]): p["when"] for p in listed["coming_soon"]}
    assert planned[("cloud_storage", "Snowflake")] == "soon" and planned[("cloud_storage", "Google BigQuery")] == "soon"
    assert planned[("business_tool", "Square")] == "soon" and planned[("business_tool", "Gusto")] == "later"
    assert {p["when"] for p in listed["coming_soon"]} == {"soon", "later"}
    assert client.put("/api/connections/snowflake/refresh", headers=h, json={"refresh_frequency": "daily"}).status_code in (400, 403, 404)

    assert client.post("/api/connections/supabase", headers=h, json={"connection_string": "not a connection string"}).status_code == 400
    private = client.post("/api/connections/supabase", headers=h, json={"connection_string": "postgresql://u:p@127.0.0.1:5432/db"})
    assert private.status_code == 400 and "private" in private.json()["detail"]

    seen = {}

    async def fake_tables(dsn):
        seen["dsn"] = dsn
        return [{"schema": "public", "name": "orders", "kind": "table", "rows_estimate": 3, "has_rows": True},
                {"schema": "public", "name": "returns", "kind": "table", "rows_estimate": 0, "has_rows": False}]

    async def fake_read(dsn, schema, name, limit):
        csv = pd.DataFrame({"order_id": [1, 2, 3], "amount": [9.5, 4.0, 2.25]}).to_csv(index=False).encode()
        return {"csv": csv, "rows": 3, "columns": ["order_id", "amount"], "truncated": False}

    monkeypatch.setattr(cloud_sources, "check_host", lambda host: None)
    monkeypatch.setattr(cloud_sources, "list_tables", fake_tables)
    monkeypatch.setattr(cloud_sources, "read_table_csv", fake_read)
    dsn = "postgresql://ro:topsecret@db.abcd.supabase.co:5432/postgres"
    res = client.post("/api/connections/supabase", headers=h, json={"connection_string": dsn})
    assert res.status_code == 200 and res.json()["tables"][0]["name"] == "orders"
    row = next(c for c in account_store._connections.values() if c["connector"] == "supabase")
    assert row["status"] == "connected" and "topsecret" not in row["credentials_encrypted"]
    supa = next(c for c in res.json()["connectors"] if c["connector"] == "supabase")
    assert supa["display_name"] == "db.abcd.supabase.co / postgres" and "credentials_encrypted" not in supa

    listing = client.get("/api/connections/supabase/tables", headers=h).json()
    assert listing["tables"][0]["schema"] == "public"
    assert listing["schemas"] == [{"schema": "public", "tables": 2, "with_rows": 1}]   # the empty one is counted out
    assert seen["dsn"] == dsn                                                   # decrypted for the read only
    csv = client.get("/api/connections/supabase/table?schema=public&name=orders", headers=h)
    assert csv.status_code == 200 and csv.text.startswith("order_id,amount") and csv.headers["X-Data-Origin"] == "cloud"
    assert csv.headers["X-Rows"] == "3" and csv.headers["X-Truncated"] == "false"
    # Cloud sources have no refresh schedule (nothing is copied).
    assert client.put("/api/connections/supabase/refresh", headers=h, json={"refresh_frequency": "daily"}).status_code == 404

    gone = client.delete("/api/connections/supabase", headers=h).json()["connectors"]
    assert next(c for c in gone if c["connector"] == "supabase")["status"] == "not_set_up"
    assert client.get("/api/connections/supabase/tables", headers=h).status_code == 404


def test_add_another_source_combines_live_and_file_tables():
    """
    "Add another source": a QuickBooks record type (as it arrives from the live
    reader) and a customer list from a database open together — two tables side
    by side, matched by customer name although they share no ID.
    """
    invoices = "\n".join(["record_id,line_number,transaction_date,customer_name,line_amount",
                          "Invoice:1,1,2026-01-02,Blue Door Cafe LLC,40", "Invoice:2,1,2026-01-05,Corner Market,25",
                          "Invoice:3,1,2026-01-09,Blue Door Cafe LLC,15"]).encode()
    customers = "\n".join(["customer_id,customer,email", "1,Blue Door Cafe,bd@example.com",
                           "2,Corner Market Inc,cm@example.com"]).encode()
    res = client.post("/api/files/analyze", files=[("files", ("quickbooks.Invoice.csv", invoices, "text/csv")),
                                                   ("files", ("public.customers.csv", customers, "text/csv"))])
    assert res.status_code == 200
    cat = res.json()["categorization"]
    assert [g["label"] for g in cat["groups"]] == ["Transactions", "Customers"]
    assert cat["relationships"][0]["method"] == "fuzzy"
