"""
Tests for summarizing where the data lives (api/services/pushdown.py, warehouses.py):
the safe summary request, the SQL / SOQL per source, and Snowflake, BigQuery,
Salesforce and PostgreSQL (Supabase) end to end with each service faked.
"""

from __future__ import annotations

import asyncio
import json
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from api.core.config import get_settings
from api.services import account_store, cloud_sources, oauth, pushdown, warehouses
from tests.test_oauth_integrations import FakeProviders, _connect, _signup, _token_route, client

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                        serialization.NoEncryption()).decode()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for k in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(k, "")
    for k, v in {"SALESFORCE_CLIENT_ID": "sf-id", "SALESFORCE_CLIENT_SECRET": "sf-secret",
                 "PUBLIC_API_URL": "https://api.example.com", "SITE_URL": "https://site.example.com"}.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    account_store._accounts.clear()
    account_store._connections.clear()
    yield
    oauth.HTTP_TRANSPORT = None
    get_settings.cache_clear()


# ── The summary request and the query languages ──────────────────────────────

def test_summary_requests_are_checked_and_written_safely_for_each_source():
    cols = {"amount": "number", "store": "text", "order_date": "date"}
    s = pushdown.validate({"agg": "sum", "metric": "amount", "group_by": ["store"], "date_column": "order_date",
                           "period": "month", "limit": 99999}, "public.orders", cols)
    assert s.limit == pushdown.MAX_LIMIT
    for bad, why in [({"agg": "sum", "metric": "store"}, "isn't a number"), ({"agg": "sum", "metric": "nope"}, "number column"),
                     ({"agg": "drop", "metric": "amount"}, "Choose one of"),
                     ({"agg": "sum", "metric": "amount", "period": "month", "date_column": "store"}, "date column"),
                     ({"agg": "sum", "metric": "amount", "group_by": ["x; DROP TABLE orders"]}, "wasn't found")]:
        with pytest.raises(Exception) as exc:
            pushdown.validate(bad, "public.orders", cols)
        assert why in exc.value.detail
    # A sneaky (but real) column name can't break out of its quotes.
    evil = pushdown.Summary('public.orders', "sum", 'a"; DROP TABLE x; --', [], None, None, 5)
    assert 'SUM("a""; DROP TABLE x; --")' in pushdown.build_sql(evil, "postgres")
    assert "`order_date`" in pushdown.build_sql(s, "bigquery") and "DATE_TRUNC(DATE(" in pushdown.build_sql(s, "bigquery")
    assert "DATE_TRUNC('MONTH'" in pushdown.build_sql(s, "snowflake")
    soql = pushdown.build_soql(pushdown.Summary("Opportunity", "avg", "Amount", ["StageName"], None, None, 10))
    assert soql == "SELECT StageName, AVG(Amount) value FROM Opportunity GROUP BY StageName ORDER BY AVG(Amount) DESC LIMIT 10"
    with pytest.raises(Exception):
        pushdown.build_soql(pushdown.Summary("Opportunity", "sum", "Amount) FROM User --", [], None, None, 10))


# ── Snowflake: key-pair sign-in, SQL API ─────────────────────────────────────

def test_snowflake_connect_list_summarize_and_open():
    seen = []

    def statements(request: httpx.Request):
        token = request.headers["authorization"].removeprefix("Bearer ")
        claims = jwt.decode(token, KEY.public_key(), algorithms=["RS256"])     # signed with the client's key
        assert claims["sub"] == "MYORG-ACCT.RO_USER" and claims["iss"].startswith("MYORG-ACCT.RO_USER.SHA256:")
        body = json.loads(request.content)
        assert body["warehouse"] == "SMALL_WH" and body["database"] == "SALES"
        sql = body["statement"]
        seen.append(sql)
        if "information_schema.tables" in sql:
            return {"resultSetMetaData": {"rowType": [{"name": "TABLE_SCHEMA"}, {"name": "TABLE_NAME"}, {"name": "ROW_COUNT"}]},
                    "data": [["PUBLIC", "ORDERS", "120000"]]}
        if "information_schema.columns" in sql:
            return {"resultSetMetaData": {"rowType": [{"name": "C"}, {"name": "T"}]},
                    "data": [["AMOUNT", "NUMBER"], ["STORE", "TEXT"], ["ORDER_DATE", "DATE"]]}
        if sql.startswith("SELECT * FROM"):
            return {"resultSetMetaData": {"rowType": [{"name": "AMOUNT"}, {"name": "STORE"}]}, "data": [["5", "Midtown"]]}
        return {"resultSetMetaData": {"rowType": [{"name": "STORE"}, {"name": "value"}]},
                "data": [["Midtown", "1200.50"], ["Decatur", "900"]]}

    fake = FakeProviders({("POST", "https://myorg-acct.snowflakecomputing.com/api/v2/statements"): statements})
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()
    assert client.post("/api/connections/snowflake", headers=h, json={"account": "evil.com/x"}).status_code == 400
    bad = client.post("/api/connections/snowflake", headers=h, json={
        "account": "evil.com/x", "user": "u", "private_key": PEM, "warehouse": "w", "database": "d"})
    assert bad.status_code == 400 and "myorg-myaccount" in bad.json()["detail"]      # can't point us at another host
    ok = client.post("/api/connections/snowflake", headers=h, json={
        "account": "myorg-acct", "user": "ro_user", "private_key": PEM, "warehouse": "small_wh", "database": "sales"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["tables"] == [{"name": "PUBLIC.ORDERS", "rows": 120000}]
    cols = client.get("/api/connections/snowflake/warehouse/columns", headers=h, params={"table": "PUBLIC.ORDERS"}).json()
    assert {"name": "AMOUNT", "kind": "number"} in cols["columns"]
    res = client.post("/api/connections/snowflake/warehouse/summarize", headers=h,
                      json={"table": "PUBLIC.ORDERS", "agg": "sum", "metric": "AMOUNT", "group_by": ["STORE"]})
    assert res.status_code == 200 and res.text.splitlines() == ["STORE,value", "Midtown,1200.50", "Decatur,900"]
    assert unquote(res.headers["X-Query"]) == ('SELECT "STORE", SUM("AMOUNT") AS "value" FROM "PUBLIC"."ORDERS" '
                                               'GROUP BY 1 ORDER BY 2 DESC LIMIT 1000')
    assert client.get("/api/connections/snowflake/warehouse/columns", headers=h,
                      params={"table": "PUBLIC.SECRETS"}).status_code == 404          # only listed tables
    sample = client.get("/api/connections/snowflake/warehouse/table", headers=h, params={"table": "PUBLIC.ORDERS"})
    assert sample.headers["X-Rows"] == "1" and seen[-1].endswith("LIMIT 20000")
    stored = next(v for k, v in account_store._connections.items() if k[1] == "snowflake")
    assert "BEGIN PRIVATE KEY" not in stored["credentials_encrypted"]                   # saved encrypted


# ── BigQuery: service account, a cap on bytes scanned, free table preview ─────

def test_bigquery_summaries_have_a_cost_cap_and_previews_are_free():
    queries = []

    def query(request: httpx.Request):
        body = json.loads(request.content)
        queries.append(body)
        return {"jobComplete": True, "schema": {"fields": [{"name": "store"}, {"name": "value"}]},
                "rows": [{"f": [{"v": "Midtown"}, {"v": "1200.5"}]}]}

    def token(request: httpx.Request):
        form = parse_qs(request.content.decode())
        claims = jwt.decode(form["assertion"][0], KEY.public_key(), algorithms=["RS256"], audience="https://oauth2.googleapis.com/token")
        assert claims["iss"] == "reader@proj.iam.gserviceaccount.com"
        return {"access_token": "bq-token", "expires_in": 3600}

    base = "https://bigquery.googleapis.com/bigquery/v2/projects/proj"
    fake = FakeProviders({
        ("POST", "https://oauth2.googleapis.com/token"): token,
        ("GET", f"{base}/datasets"): {"datasets": [{"datasetReference": {"datasetId": "shop"}}]},
        ("GET", f"{base}/datasets/shop/tables"): {"tables": [{"tableReference": {"tableId": "orders"}}]},
        ("GET", f"{base}/datasets/shop/tables/orders"): {"schema": {"fields": [
            {"name": "amount", "type": "FLOAT64"}, {"name": "store", "type": "STRING"}, {"name": "day", "type": "DATE"}]}},
        ("GET", f"{base}/datasets/shop/tables/orders/data"): {"rows": [{"f": [{"v": "5"}, {"v": "Midtown"}, {"v": "2026-10-01"}]}]},
        ("POST", f"{base}/queries"): query,
    })
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()
    sa = {"type": "service_account", "project_id": "proj", "client_email": "reader@proj.iam.gserviceaccount.com",
          "private_key": PEM, "token_uri": "https://attacker.example.com/token"}
    assert client.post("/api/connections/bigquery", headers=h, json={"service_account_json": "{}"}).status_code == 400
    ok = client.post("/api/connections/bigquery", headers=h, json={"service_account_json": json.dumps(sa)})
    assert ok.status_code == 200 and ok.json()["tables"] == [{"name": "shop.orders", "rows": None}]
    assert not any("attacker" in str(c.url) for c in fake.calls)                     # Google's token address only
    res = client.post("/api/connections/bigquery/warehouse/summarize", headers=h,
                      json={"table": "shop.orders", "agg": "sum", "metric": "amount", "group_by": ["store"],
                            "date_column": "day", "period": "month"})
    assert res.status_code == 200, res.text
    assert queries[-1]["maximumBytesBilled"] == str(get_settings().bigquery_max_bytes) and not queries[-1]["useLegacySql"]
    assert "DATE_TRUNC(DATE(`day`), MONTH)" in queries[-1]["query"] and "FROM `shop.orders`" in queries[-1]["query"]
    n = len(queries)
    sample = client.get("/api/connections/bigquery/warehouse/table", headers=h, params={"table": "shop.orders"})
    assert sample.text.splitlines()[0] == "amount,store,day" and len(queries) == n   # preview = no query, no bill


# ── Salesforce: sign in, then aggregate SOQL ─────────────────────────────────

def test_salesforce_sign_in_and_summarize_an_object():
    rest = "https://acme.my.salesforce.com/services/data/v61.0/"
    soql = []

    def query(request: httpx.Request):
        soql.append(request.url.params["q"])
        return {"records": [{"attributes": {}, "StageName": "Closed Won", "value": 50000}]}

    fake = FakeProviders({
        ("POST", "https://login.salesforce.com/services/oauth2/token"): {"access_token": "sf-access", "refresh_token": "sf-refresh", "expires_in": 3600},
        ("GET", "https://login.salesforce.com/services/oauth2/userinfo"): {
            "preferred_username": "owner@acme.com", "urls": {"rest": "https://acme.my.salesforce.com/services/data/v{version}/"}},
        ("GET", f"{rest}sobjects/"): {"sobjects": [{"name": "Opportunity", "label": "Opportunity", "queryable": True},
                                                    {"name": "LoginHistory", "queryable": True}]},
        ("GET", f"{rest}sobjects/Opportunity/describe"): {"fields": [
            {"name": "Amount", "type": "currency"}, {"name": "StageName", "type": "picklist"}, {"name": "CloseDate", "type": "date"}]},
        ("GET", f"{rest}query"): query,
    })
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    h = _signup()
    assert _connect(h, "salesforce").headers["location"].endswith("connected=1")
    names = [t["name"] for t in client.get("/api/connections/salesforce/warehouse/tables", headers=h).json()["tables"]]
    assert names == ["Opportunity"]                                                     # system objects left out
    res = client.post("/api/connections/salesforce/warehouse/summarize", headers=h,
                      json={"table": "Opportunity", "agg": "sum", "metric": "Amount", "group_by": ["StageName"]})
    assert res.status_code == 200 and res.text.splitlines() == ["StageName,value", "Closed Won,50000"]
    assert soql[-1] == "SELECT StageName, SUM(Amount) value FROM Opportunity GROUP BY StageName ORDER BY SUM(Amount) DESC LIMIT 1000"


# ── PostgreSQL (Supabase): the same summary, in their database ───────────────

def test_postgres_summary_runs_in_the_clients_database(monkeypatch):
    sent = []

    async def mgmt_query(token, ref, sql):
        sent.append(sql)
        if "information_schema.columns" in sql:
            return [{"column_name": "amount", "data_type": "numeric"}, {"column_name": "store", "data_type": "text"}]
        return [{"store": "Midtown", "value": 1200.5}]

    monkeypatch.setattr(cloud_sources, "mgmt_query", mgmt_query)
    src = {"mode": "oauth", "token": "t", "ref": "abc"}
    r = asyncio.run(warehouses.summarize("supabase", src, "public.orders", {"agg": "sum", "metric": "amount",
                                                                             "group_by": ["store"]}))
    assert r["query"] == 'SELECT "store", SUM("amount") AS "value" FROM "public"."orders" GROUP BY 1 ORDER BY 2 DESC LIMIT 1000'
    assert r["csv"].splitlines() == ["store,value", "Midtown,1200.5"] and sent[-1] == r["query"]
    assert "table_schema = 'public' AND table_name = 'orders'" in sent[0]                  # looked up, quoted
