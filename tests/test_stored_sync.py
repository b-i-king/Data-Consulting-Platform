"""
Tests for storing QuickBooks / Xero, entities, and duplicates across sources
(api/services/sync.py, entities.py, dedupe.py, warehouse_store.py; the
endpoints in api/routers/connections.py, entities.py, warehouse.py).

No real provider is called (httpx.MockTransport, as in test_oauth_integrations.py)
and no database is needed (the in-memory stores).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, business_tools, entities, entitlements, oauth, sync, unified, warehouse_store
from api.services.warehouse_loader import build_plan

client = TestClient(app)


async def _always_paid(_account):
    return True


@pytest.fixture(autouse=True)
def paid_plan(monkeypatch):
    """Business tools need a paid plan; these tests are about other rules, so every test account counts as paid."""
    monkeypatch.setattr(entitlements, "has_paid_plan", _always_paid)
PW = "Correct-horse-9"
QB = "https://sandbox-quickbooks.api.intuit.com/v3/company/9130"
XERO = business_tools.XERO_API


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL", "STRIPE_SECRET_KEY"):
        monkeypatch.setenv(key, "")
    for key, value in {"QUICKBOOKS_CLIENT_ID": "qb-id", "QUICKBOOKS_CLIENT_SECRET": "qb-secret",
                       "XERO_CLIENT_ID": "xero-id", "XERO_CLIENT_SECRET": "xero-secret",
                       "PUBLIC_API_URL": "https://api.example.com", "SITE_URL": "https://site.example.com"}.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants, account_store._connections,
                  account_store._subscriptions, account_store._load_runs, account_store._consents,
                  warehouse_store._stored, entities._entities, entities._links, business_tools._minute_calls):
        store.clear()
    monkeypatch.setattr(account_store, "_api_usage", {})
    yield
    oauth.HTTP_TRANSPORT = None
    get_settings.cache_clear()


def _signup(email="ana@example.com", engineering=True):
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Ana", "last_name": "Lee",
                                                    "email": email, "password": PW, "accept_terms": True})
    assert res.status_code == 201, res.text
    body = res.json()
    h = {"Authorization": f"Bearer {body['access_token']}"}
    if engineering:   # test mode (no Stripe key): the package switches on at once
        assert client.post("/api/billing/checkout", headers=h,
                           json={"plan_ids": ["data_engineering"], "interval": "month"}).status_code == 200
    return h, body["account"]["id"]


class Fake:
    """Answers like the providers do; records calls."""

    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def __call__(self, request):
        self.calls.append(request)
        answer = self.routes.get((request.method, str(request.url).split("?")[0]))
        if answer is None:
            return httpx.Response(404, json={})
        answer = answer(request) if callable(answer) else answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)


def _tokens(prefix):
    return lambda r: {"access_token": f"{prefix}-a", "refresh_token": f"{prefix}-r", "expires_in": 1800}


def _connect(h, provider, **params):
    url = client.get(f"/api/connections/{provider}/authorize", headers=h).json()["url"]
    state = parse_qs(urlparse(url).query)["state"][0]
    res = client.get("/api/connections/oauth/callback", params={"state": state, "code": "c", **params},
                     follow_redirects=False)
    assert "connected=1" in res.headers["location"], res.headers["location"]


def _invoice(i, customer, amount, when="2026-03-0{}T10:00:00-07:00"):
    return {"Id": str(i), "DocNumber": f"10{i}", "TxnDate": f"2026-03-0{i}", "TotalAmt": amount,
            "CustomerRef": {"value": str(50 + i), "name": customer},
            "MetaData": {"LastUpdatedTime": when.format(i)},
            "Line": [{"Amount": amount, "DetailType": "SalesItemLineDetail"}]}


def _qb_fake(invoices):
    seen = []

    def query(request):
        q = request.url.params["query"]
        seen.append(q)
        if q.startswith("SELECT * FROM Invoice WHERE"):
            return {"QueryResponse": {"Invoice": invoices}}
        return {"QueryResponse": {}}
    fake = Fake({("POST", "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer"): _tokens("qb"),
                 ("GET", f"{QB}/companyinfo/9130"): {"CompanyInfo": {"CompanyName": "Sam's Bakery"}},
                 ("GET", f"{QB}/query"): query})
    return fake, seen


# ── Entities: each customer once, linked from every source ──────────────────

def _load_file(aid, name, df):
    plan = build_plan(df, name, event_type="transaction/income")
    return asyncio.run(warehouse_store.load({"id": aid}, plan))


def test_customers_are_stored_once_and_come_back_on_export():
    aid = "acct-1"
    first = pd.DataFrame({"order_id": [1, 2, 3], "order_date": ["2026-01-02", "2026-01-03", "2026-01-04"],
                          "customer": ["Blue Door Cafe", "Corner Market", "Blue Door Cafe"],
                          "customer_email": ["bd@example.com", None, "bd@example.com"], "amount": [10.0, 20.0, 30.0]})
    res = _load_file(aid, "jan.csv", first)
    assert res["entities_added"] == 2 and res["matches_to_review"] == 0
    stored = warehouse_store._stored[(aid, "file_upload", "jan.csv")]
    attrs = stored["attributes"].iloc[0]
    assert "customer_link" in attrs and "customer_name" not in attrs and "customer_email" not in attrs
    customers = [e for e in entities._entities.values() if e["entity_type"] == "customer"]
    assert sorted(e["display_name"] for e in customers) == ["Blue Door Cafe", "Corner Market"]
    stored_email = next(e for e in customers if e["display_name"] == "Blue Door Cafe")["details"]["customer_email"]
    assert stored_email.startswith("enc1:") and "bd@example.com" not in stored_email   # encrypted (api/services/pii.py)

    # Use my stored data: the same table back, names and emails in place.
    # (Loaded here without the API, so no column plan is saved: the shared names come back.)
    back = asyncio.run(warehouse_store.export({"id": aid}, "jan.csv"))
    assert list(back["customer_name"]) == ["Blue Door Cafe", "Corner Market", "Blue Door Cafe"]
    assert back["customer_email"].tolist()[0] == "bd@example.com"

    # Loading the same file again writes nothing (the links are stable).
    again = _load_file(aid, "jan.csv", first)
    assert again["rows_loaded"] == 0 and again["rows_updated"] == 0 and again["entities_added"] == 0

    # Another file names the same customers differently: no new entities, one waits for review.
    feb = pd.DataFrame({"order_id": [7, 8], "order_date": ["2026-02-02", "2026-02-03"],
                        "customer": ["BLUE DOOR CAFE, LLC", "Corner Market ATL"], "amount": [5.0, 6.0]})
    res = _load_file(aid, "feb.csv", feb)
    assert res["entities_added"] == 1 and res["matches_to_review"] == 1     # "Corner Market ATL" ~ "Corner Market" (86)
    links = list(entities._links.values())
    blue = [lk for lk in links if lk["source_label"] and "blue door" in lk["source_label"].lower()]
    assert len({lk["entity_id"] for lk in blue}) == 1                        # same customer across files


def test_review_merges_two_customers_without_rewriting_rows():
    aid = "acct-2"
    _load_file(aid, "a.csv", pd.DataFrame({"order_id": [1], "order_date": ["2026-01-02"],
                                           "customer": ["Sunrise Bakery Atlanta"], "amount": [10.0]}))
    _load_file(aid, "b.csv", pd.DataFrame({"order_id": [2], "order_date": ["2026-01-03"],
                                           "customer": ["Sunrise Bakery ATL"], "amount": [11.0]}))
    pending = asyncio.run(entities.pending_reviews(aid))
    assert len(pending) == 1 and pending[0]["candidate"]["name"] == "Sunrise Bakery Atlanta"
    before = warehouse_store._stored[(aid, "file_upload", "b.csv")]["row_hash"].tolist()
    assert asyncio.run(entities.decide(aid, pending[0]["link_id"], same=True))
    assert len([e for e in entities._entities.values() if e["account_id"] == aid]) == 1   # merged
    assert warehouse_store._stored[(aid, "file_upload", "b.csv")]["row_hash"].tolist() == before   # rows untouched
    assert asyncio.run(entities.pending_reviews(aid)) == []
    # Each file still gets its own spelling back.
    assert asyncio.run(warehouse_store.export({"id": aid}, "b.csv"))["customer_name"].tolist() == ["Sunrise Bakery ATL"]


def test_backfill_links_data_stored_before_entities():
    aid = "acct-3"
    plan = build_plan(pd.DataFrame({"order_id": [1, 2], "order_date": ["2026-01-02", "2026-01-03"],
                                    "customer": ["Blue Door Cafe", "Blue Door Cafe"], "amount": [10.0, 20.0]}),
                      "old.csv", event_type="transaction/income")
    warehouse_store._stored[(aid, "file_upload", "old.csv")] = plan.rows.copy()       # stored the old way
    first = asyncio.run(warehouse_store.relink_stored({"id": aid}))
    assert first["rows_relinked"] == len(plan.rows) and first["entities_added"] == 1
    assert "customer_link" in warehouse_store._stored[(aid, "file_upload", "old.csv")]["attributes"].iloc[0]
    assert asyncio.run(warehouse_store.relink_stored({"id": aid}))["rows_relinked"] == 0   # twice is safe
    assert asyncio.run(warehouse_store.export({"id": aid}, "old.csv"))["customer_name"].tolist() == ["Blue Door Cafe"] * 2


# ── The stored sync ─────────────────────────────────────────────────────────

def test_quickbooks_stored_sync_stores_what_changed_and_reports_file_duplicates():
    h, aid = _signup()
    # An uploaded file that has invoice 101 already (same date, amount, number).
    up = pd.DataFrame({"invoice_number": ["101"], "invoice_date": ["2026-03-01"], "total": [96.0]})
    _load_file(aid, "invoices.csv", up)

    invoices = [_invoice(1, "Blue Door Cafe", 96.0), _invoice(2, "Corner Market", 40.0)]
    fake, seen = _qb_fake(invoices)
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    _connect(h, "quickbooks", realmId="9130")

    no_consent = client.post("/api/connections/quickbooks/sync", headers=h, json={"consent": False})
    assert no_consent.status_code == 400
    res = client.post("/api/connections/quickbooks/sync", headers=h, json={"consent": True})
    assert res.status_code == 200, res.text
    r = res.json()["sync"]
    assert r["complete"] and r["records"] == 2 and r["rows_loaded"] > 0
    assert r["duplicates_elsewhere"] == 1 and r["entities_added"] >= 2
    stored = warehouse_store._stored[(aid, "quickbooks", "QuickBooks Online")]
    assert set(stored["record_id"]) == {"Invoice:1", "Invoice:2"}
    assert "customer_link" in stored["attributes"].iloc[0]
    row = next(c for c in account_store._connections.values() if c["connector"] == "quickbooks")
    assert row["sync_state"]["Invoice"].startswith("2026-03-02T17:00:00")      # newest change, in UTC
    assert row["last_synced_at"] is not None and row["next_sync_at"] > datetime.now(timezone.utc)

    # "Sync now" again right away: the daily schedule allows it only after 4 hours.
    assert client.post("/api/connections/quickbooks/sync", headers=h, json={}).status_code == 429

    # The scheduler's next run asks only for what changed since then; nothing new is written.
    row["next_sync_at"] = datetime.now(timezone.utc) - timedelta(minutes=1)
    report = asyncio.run(sync.run_due())
    assert report[0]["ok"] and report[0]["result"]["rows_loaded"] == 0
    assert "MetaData.LastUpdatedTime >= '2026-03-02T17:00:00" in seen[-len(unified.QUICKBOOKS_ENTITIES):][0] or \
        any("2026-03-02T17:00:00" in q for q in seen[-len(unified.QUICKBOOKS_ENTITIES):])


def test_stored_sync_needs_data_engineering():
    h, _ = _signup(engineering=False)
    fake, _ = _qb_fake([])
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    _connect(h, "quickbooks", realmId="9130")
    res = client.post("/api/connections/quickbooks/sync", headers=h, json={"consent": True})
    assert res.status_code == 403 and "Data Engineering" in res.json()["detail"]


def _xero_invoice(i, status="AUTHORISED", total=110.0, contact="Blue Door Cafe", kind="ACCREC"):
    return {"InvoiceID": f"inv-{i}", "InvoiceNumber": f"INV-{i}", "Type": kind, "Status": status,
            "DateString": f"2026-04-0{i}T00:00:00", "Date": "/Date(1775000000000+0000)/", "SubTotal": 100.0,
            "TotalTax": 10.0, "Total": total, "CurrencyCode": "USD",
            "UpdatedDateUTC": f"/Date({1775000000000 + i * 1000}+0000)/",
            "Contact": {"ContactID": f"c-{i}", "Name": contact, "Addresses": [{"City": "Atlanta"}]},
            "LineItems": [{"LineItemID": f"li-{i}", "Description": "Bread", "Quantity": 2, "UnitAmount": 50.0,
                           "LineAmount": 100.0}]}


def test_xero_stored_sync_and_voided_invoices_are_removed():
    h, aid = _signup()
    pages = {"Invoices": [_xero_invoice(1), _xero_invoice(2, contact="Flour Co", kind="ACCPAY")]}

    def endpoint(name):
        def handle(request):
            assert request.headers["xero-tenant-id"] == "tenant-1"
            return {name: pages.get(name, []), "pagination": {"page": 1, "pageCount": 1}}
        return handle
    fake = Fake({("POST", "https://identity.xero.com/connect/token"): _tokens("xero"),
                 ("GET", "https://api.xero.com/connections"): [{"id": "conn-1", "tenantId": "tenant-1",
                                                                 "tenantType": "ORGANISATION", "tenantName": "Sam's"}],
                 **{("GET", f"{XERO}/{n}"): endpoint(n) for n in ("Invoices", "BankTransactions", "Payments")}})
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    _connect(h, "xero")
    r = client.post("/api/connections/xero/sync", headers=h, json={"consent": True}).json()["sync"]
    assert r["complete"] and r["records"] == 2
    stored = warehouse_store._stored[(aid, "xero", "Xero")]
    kinds = {(rid, et, es) for rid, et, es in stored[["record_id", "event_type", "event_subtype"]].itertuples(index=False)}
    assert ("Invoice:inv-1", "invoice", "sent") in kinds and ("Invoice:inv-2", "invoice", "received") in kinds
    vendor = [e for e in entities._entities.values() if e["entity_type"] == "vendor"]
    assert [v["display_name"] for v in vendor] == ["Flour Co"]                  # a bill's contact is a supplier

    # Invoice 1 is voided in Xero: the next sync removes it from storage.
    pages["Invoices"] = [_xero_invoice(1, status="VOIDED")]
    row = next(c for c in account_store._connections.values() if c["connector"] == "xero")
    row["last_synced_at"] = datetime.now(timezone.utc) - timedelta(days=1)     # past the "Sync now" gap
    r = client.post("/api/connections/xero/sync", headers=h, json={}).json()["sync"]
    stored = warehouse_store._stored[(aid, "xero", "Xero")]
    assert set(stored["record_id"]) == {"Invoice:inv-2"}
    since = [c for c in fake.calls if c.url.path.endswith("/Invoices")][-1]
    assert "If-Modified-Since" in since.headers                                 # only what changed


def test_xero_sync_stops_cleanly_at_the_daily_allowance(monkeypatch):
    monkeypatch.setenv("XERO_DAILY_CALL_LIMIT", "2")                          # budget 1 call
    get_settings.cache_clear()
    h, aid = _signup()
    fake = Fake({("POST", "https://identity.xero.com/connect/token"): _tokens("xero"),
                 ("GET", "https://api.xero.com/connections"): [{"id": "conn-1", "tenantId": "tenant-1",
                                                                 "tenantType": "ORGANISATION", "tenantName": "Sam's"}],
                 ("GET", f"{XERO}/Invoices"): {"Invoices": [_xero_invoice(1)], "pagination": {"pageCount": 1}},
                 ("GET", f"{XERO}/BankTransactions"): {"BankTransactions": []},
                 ("GET", f"{XERO}/Payments"): {"Payments": []}})
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    _connect(h, "xero")
    r = client.post("/api/connections/xero/sync", headers=h, json={"consent": True}).json()["sync"]
    assert r["complete"] is False and r["records"] == 1                        # invoices stored, then stopped
    row = next(c for c in account_store._connections.values() if c["connector"] == "xero")
    assert "Invoices" in row["sync_state"] and "BankTransactions" not in row["sync_state"]
    assert row["next_sync_at"].hour == 0                                        # just after the allowance resets


# ── Duplicates across sources at the load preview ───────────────────────────

def test_file_preview_finds_records_already_stored_from_quickbooks():
    h, aid = _signup()
    fake, _ = _qb_fake([_invoice(1, "Blue Door Cafe", 96.0), _invoice(2, "Corner Market", 40.0)])
    oauth.HTTP_TRANSPORT = httpx.MockTransport(fake)
    _connect(h, "quickbooks", realmId="9130")
    client.post("/api/connections/quickbooks/sync", headers=h, json={"consent": True})

    csv = "invoice_number,invoice_date,customer,total\n101,2026-03-01,Blue Door Cafe,96\n102,2026-03-02,Corner Market,40\n" \
          "103,2026-03-05,New Client,12\n"
    files = {"files": ("export.csv", csv.encode(), "text/csv")}
    preview = client.post("/api/warehouse/preview", headers=h, files=files, data={"event_type": "invoice/sent"}).json()
    assert preview["duplicates"]["records"] == 2 and preview["duplicates"]["of"] == 3
    assert preview["duplicates"]["sources"] == ["QuickBooks Online"]
    loaded = client.post("/api/warehouse/load", headers=h, files=files,
                         data={"event_type": "invoice/sent", "fingerprint": preview["fingerprint"],
                               "skip_duplicates": "true"}).json()
    assert loaded["result"]["records_skipped_as_duplicates"] == 2
    assert warehouse_store._stored[(aid, "file_upload", "export.csv")]["record_id"].nunique() == 1


def test_review_endpoint():
    h, aid = _signup()
    _load_file(aid, "a.csv", pd.DataFrame({"order_id": [1], "order_date": ["2026-01-02"],
                                           "customer": ["Sunrise Bakery Atlanta"], "amount": [10.0]}))
    _load_file(aid, "b.csv", pd.DataFrame({"order_id": [2], "order_date": ["2026-01-03"],
                                           "customer": ["Sunrise Bakery ATL"], "amount": [11.0]}))
    matches = client.get("/api/entities/review", headers=h).json()["matches"]
    assert len(matches) == 1 and matches[0]["entity_type"] == "customer"
    left = client.post("/api/entities/review", headers=h, json={"link_id": matches[0]["link_id"], "same": False}).json()
    assert left["matches"] == []
    assert client.post("/api/entities/review", headers=h, json={"link_id": 999, "same": True}).status_code == 404
