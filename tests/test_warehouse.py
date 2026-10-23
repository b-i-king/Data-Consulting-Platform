"""
Data Engineering: preview how data will be stored in fact.account_data, then
load it only if accepted. (api/services/warehouse_loader.py, warehouse_store.py,
api/routers/warehouse.py)

Runs on the in-memory store (no DATABASE_URL), which follows the same rules as
the database: one row per metric, IDs/dates/categories kept as they are,
loading the same rows twice never duplicates them, and "Use my stored data"
gives back the original table.
"""

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, entities, warehouse_store
from api.services.unified import NO_METRIC
from api.services.warehouse_loader import EVENT_TYPES, build_plan

client = TestClient(app)
PW = "Correct-horse-9"
SAMPLES = Path(__file__).resolve().parents[1] / "samples"

# 40 order lines (order_id repeats: 2 lines per order), one negative amount
# (a refund) and one row whose date isn't a date.
SALES = (
    "order_id,order_date,customer_id,product,category,amount,payment_method\n"
    + "".join(f"{1000 + i // 2},2024-10-{(i % 28) + 1:02d},{100 + i % 7},{['Bagel', 'Scone', 'Latte'][i % 3]},"
              f"{['Bread', 'Pastry', 'Drinks'][i % 3]},{(i % 9) + 2.5 if i % 10 else -4.0},Card\n" for i in range(40))
    + "9999,not a date,101,Bagel,Bread,3.0,Cash\n"
).encode()


def _counts(result: dict) -> dict:
    """The three row counts of a load result (it also reports entities and duplicates)."""
    return {k: result[k] for k in ("rows_loaded", "rows_updated", "rows_already_stored")}


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """
    A consultant, no database, and empty in-memory stores for every test.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.

    Returns:
        Iterator[None]: yields once while the test runs.
    """
    monkeypatch.setenv("ADMIN_EMAILS", "coach@example.com")
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants, account_store._consents,
                  account_store._load_runs, account_store._subscriptions, warehouse_store._stored,
                  entities._entities, entities._links):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email, first="Ana", **extra):
    """Sign up an individual (asserting 201) and return the JSON reply."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": first,
                                                    "last_name": "Lee", "email": email, "password": PW, "accept_terms": True, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def _h(token, acting_as=None):
    """Auth headers, optionally acting for a client."""
    h = {"Authorization": f"Bearer {token}"}
    if acting_as:
        h["X-Acting-As"] = acting_as
    return h


def _files(body=SALES, name="sales.csv"):
    """An upload, as TestClient multipart files."""
    return [("files", (name, body, "text/csv"))]


def _sales_df() -> pd.DataFrame:
    """The SALES upload as a DataFrame (not cleaned: build_plan works on any table)."""
    return pd.read_csv(pd.io.common.BytesIO(SALES))


# ── The plan (pure pandas) ──────────────────────────────────────────────────

def test_plan_explains_each_step_and_proves_nothing_is_lost():
    """The plan names each column's role, explains WHY each step, previews the rows, and passes the round trip."""
    plan = build_plan(_sales_df(), "sales.csv")
    assert plan.event_type == "transaction/income"
    roles = {p["column"]: p["role"] for p in plan.column_plan}
    assert roles["amount"] == "metric" and roles["order_id"] == "id" and roles["product"] == "category"
    assert all(step["why"] for step in plan.steps)
    titles = " | ".join(s["title"] for s in plan.steps)
    assert "order_id" in titles and "lines 1, 2, 3" in titles          # order_id repeats -> order lines
    assert "Checked: rebuilding your table" in titles
    assert plan.round_trip["ok"] and plan.round_trip["cells_checked"] == 41 * 7
    assert plan.counts["rows_to_load"] == 41 and plan.counts["metrics"] == 1
    assert set(plan.tables) == {"columns", "account_data"}
    assert len(plan.tables["account_data"]["rows"]) == 8 and plan.tables["account_data"]["total"] == 41
    # Nothing is changed: the negative amount stays negative, every other column rides along.
    stored = plan.rows
    assert (stored["metric_value"] < 0).sum() == 4
    first = stored.iloc[0]
    assert first["field_name"] == "line_amount" and first["metric_type"] == "amount" and first["metric_unit"] == "usd"
    assert first["attributes"]["payment_method_name"] == "Card" and first["attributes"]["item_name"] == "Bagel"


def test_choices_change_the_plan_and_the_fingerprint():
    """Changing a choice (or the data) gives a new fingerprint; an unknown column or choice is refused."""
    df = _sales_df()
    a = build_plan(df, "sales.csv")
    b = build_plan(df, "sales.csv", {"metric_map": {"amount": {"type": "amount", "subtype": "sale", "unit": "usd"}}})
    assert a.fingerprint != b.fingerprint
    assert b.rows["metric_subtype"].iloc[0] == "sale"
    assert build_plan(df, "sales.csv").fingerprint == a.fingerprint     # same input, same fingerprint
    c = build_plan(df, "sales.csv", {"id_columns": ["customer_id"]}, "transaction/expense")
    assert c.event_type == "transaction/expense" and c.rows["metric_type"].iloc[0] == "cost"
    with pytest.raises(ValueError):
        build_plan(df, "sales.csv", {"metric_columns": ["nope"]})
    with pytest.raises(ValueError):
        build_plan(df, "sales.csv", {"mapping": {}})
    with pytest.raises(ValueError):
        build_plan(df, "sales.csv", event_type="made/up")


def test_a_list_without_numbers_can_be_stored():
    """A customer list has no metrics: one row per record keeps all its details."""
    plan = build_plan(pd.DataFrame({"name": ["A", "B"], "city": ["Atlanta", "Decatur"]}), "list.csv")
    assert plan.event_type == "list/records" and plan.problem is None
    assert set(plan.rows["field_name"]) == {NO_METRIC} and plan.rows["attributes"].iloc[1]["city"] == "Decatur"


def test_empty_table_means_nothing_to_load():
    """An empty table can't be loaded, and says why."""
    plan = build_plan(pd.DataFrame({"amount": []}), "empty.csv")
    assert plan.problem and plan.to_dict()["ready"] is False


def test_sample_files_get_sensible_guesses():
    """The bakery samples: sales -> income; expenses -> expenses (amount = cost, to check); feedback -> CSAT."""
    expenses = build_plan(pd.read_csv(SAMPLES / "expenses.csv"), "expenses.csv")
    assert expenses.event_type == "transaction/expense"
    assert expenses.counts["needs_review"] == 1 and expenses.rows["metric_type"].iloc[0] == "cost"
    feedback = build_plan(pd.read_csv(SAMPLES / "feedback.csv"), "feedback.csv")
    assert feedback.event_type == "feedback/csat"
    assert set(feedback.rows["metric_subtype"]) == {"csat", "nps"}


def test_every_event_type_is_a_type_and_subtype():
    """Each event type id splits into the two account_data columns."""
    assert all(len(k.split("/")) == 2 for k in EVENT_TYPES)


# ── The flow through the API ────────────────────────────────────────────────

def test_preview_then_accept_then_reload_is_safe():
    """Full flow: preview stores nothing; load needs consent + the same fingerprint; reloading adds nothing."""
    coach = _signup("coach@example.com", "Coach")["access_token"]
    client_id = _signup("ana@example.com", wants_consultant=True)["account"]["id"]
    h = _h(coach, client_id)

    preview = client.post("/api/warehouse/preview", headers=h, files=_files()).json()
    assert preview["ready"] and preview["storage"] == {"mode": "memory", "consented": False,
                                                        "terms_version": preview["storage"]["terms_version"]}
    assert preview["round_trip"]["ok"] and preview["column_plan"][0]["column"] == "order_id"
    assert warehouse_store._stored == {}                              # preview stores nothing

    no_consent = client.post("/api/warehouse/load", headers=h, files=_files(),
                             data={"fingerprint": preview["fingerprint"]})
    assert no_consent.status_code == 400
    stale = client.post("/api/warehouse/load", headers=h, files=_files(),
                        data={"fingerprint": "old-preview", "consent": "true"})
    assert stale.status_code == 409                                  # not what was accepted

    ok = client.post("/api/warehouse/load", headers=h, files=_files(),
                     data={"fingerprint": preview["fingerprint"], "consent": "true"}).json()
    rows = preview["counts"]["rows_to_load"]
    assert _counts(ok["result"]) == {"rows_loaded": rows, "rows_updated": 0, "rows_already_stored": 0}
    assert ok["load_run"]["status"] == "succeeded"
    assert ok["summary"]["rows"] == rows and ok["summary"]["sources"][0]["source_name"] == "sales.csv"

    again = client.post("/api/warehouse/load", headers=h, files=_files(),
                        data={"fingerprint": preview["fingerprint"]}).json()   # consent already on file
    assert _counts(again["result"]) == {"rows_loaded": 0, "rows_updated": 0, "rows_already_stored": rows}

    summary = client.get("/api/warehouse/summary", headers=h).json()
    assert summary["storage"]["consented"] is True and len(summary["loads"]) == 2
    assert summary["warehouse"]["first_date"] == "2024-10-01"


def test_a_changed_value_is_updated_not_duplicated():
    """Loading the file again with one amount changed updates that one row and adds nothing."""
    coach = _signup("coach@example.com", "Coach")["access_token"]
    h = _h(coach, _signup("ana@example.com", wants_consultant=True)["account"]["id"])

    def load(body):
        fp = client.post("/api/warehouse/preview", headers=h, files=_files(body)).json()["fingerprint"]
        return client.post("/api/warehouse/load", headers=h, files=_files(body),
                           data={"fingerprint": fp, "consent": "true"}).json()["result"]

    first = load(SALES)
    changed = load(SALES.replace(b"1000,2024-10-01,100,Bagel,Bread,-4.0,Card", b"1000,2024-10-01,100,Bagel,Bread,-5.0,Card"))
    assert _counts(changed) == {"rows_loaded": 0, "rows_updated": 1, "rows_already_stored": first["rows_loaded"] - 1}


def test_needs_data_engineering():
    """A do-it-yourself client without Data Engineering can't preview or load (403), but can see their summary."""
    diy = _signup("sam@example.com", "Sam")["access_token"]
    assert client.post("/api/warehouse/preview", headers=_h(diy), files=_files()).status_code == 403
    assert client.get("/api/warehouse/summary", headers=_h(diy)).json()["warehouse"]["rows"] == 0
    assert client.get("/api/warehouse/export", headers=_h(diy)).status_code == 404   # nothing stored yet


def _load_for_client():
    """Consultant loads the sales file for a done-for-you client; returns (consultant headers, client token)."""
    coach = _signup("coach@example.com", "Coach")["access_token"]
    ana = _signup("ana@example.com", wants_consultant=True)
    h = _h(coach, ana["account"]["id"])
    fp = client.post("/api/warehouse/preview", headers=h, files=_files()).json()["fingerprint"]
    assert client.post("/api/warehouse/load", headers=h, files=_files(),
                       data={"fingerprint": fp, "consent": "true"}).status_code == 200
    return h, ana["access_token"]


def test_client_and_consultant_get_back_the_same_original_table():
    """The export is the stored data pivoted back: the client's own columns, in order, identical for both."""
    h, client_token = _load_for_client()
    mine = client.get("/api/warehouse/export?source=sales.csv", headers=_h(client_token))
    theirs = client.get("/api/warehouse/export?source=sales.csv", headers=h)
    assert mine.status_code == 200 and mine.text == theirs.text
    assert 'filename="ana_lee_sales_stored.csv"' in mine.headers["content-disposition"]
    df = pd.read_csv(pd.io.common.StringIO(mine.content.decode("utf-8-sig")))
    original = _sales_df()
    assert list(df.columns) == list(original.columns) and len(df) == len(original)
    assert df["amount"].sum() == pytest.approx(original["amount"].sum())
    # All sources at once: stacked with a "source" column first.
    everything = client.get("/api/warehouse/export", headers=h)
    assert everything.status_code == 200 and everything.text.lstrip("﻿").startswith("source,")


def test_delete_my_stored_data():
    """Delete needs the word DELETE, wipes the stored rows, and withdraws consent (a new load needs it again)."""
    h, client_token = _load_for_client()
    assert client.post("/api/warehouse/delete", headers=_h(client_token), json={"confirm": "yes"}).status_code == 400
    res = client.post("/api/warehouse/delete", headers=_h(client_token), json={"confirm": "DELETE"}).json()
    assert res["deleted"] == {"rows": 41, "sources": 1} and res["consents_withdrawn"] == 1
    assert res["summary"]["rows"] == 0 and warehouse_store._stored == {}
    summary = client.get("/api/warehouse/summary", headers=h).json()
    assert summary["storage"]["consented"] is False
    fp = client.post("/api/warehouse/preview", headers=h, files=_files()).json()["fingerprint"]
    assert client.post("/api/warehouse/load", headers=h, files=_files(), data={"fingerprint": fp}).status_code == 400


def test_team_member_cannot_delete():
    """Only the team owner (plan holder) may delete a business's stored data."""
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ann", "last_name": "Ko", "email": "ann@example.com",
        "company_name": "Ko Bakery", "password": PW, "accept_terms": True,
        "team_members": [{"first_name": "Bo", "last_name": "Ko", "email": "bo@example.com"}]})
    assert res.status_code == 201
    bo = client.post("/api/accounts/signin", json={"email": "bo@example.com", "password": PW}).json()["access_token"]
    assert client.post("/api/warehouse/delete", headers=_h(bo), json={"confirm": "DELETE"}).status_code == 403


# ── AI needs stored data ────────────────────────────────────────────────────

def test_ai_can_be_added_only_after_data_is_stored():
    """Checkout refuses AI until the account has stored data; then it goes through."""
    h, client_token = None, _signup("ana@example.com", wants_consultant=True)["access_token"]
    before = client.post("/api/billing/checkout", headers=_h(client_token),
                         json={"plan_ids": ["data_engineering", "ai"], "interval": "month"})
    assert before.status_code == 400 and "load your data first" in before.json()["detail"]
    assert client.get("/api/accounts/services", headers=_h(client_token)).json()["ai_ready"] is False

    coach = _signup("coach@example.com", "Coach")["access_token"]
    h = _h(coach, client.get("/api/accounts/me", headers=_h(client_token)).json()["id"])
    fp = client.post("/api/warehouse/preview", headers=h, files=_files()).json()["fingerprint"]
    client.post("/api/warehouse/load", headers=h, files=_files(), data={"fingerprint": fp, "consent": "true"})

    assert client.get("/api/accounts/services", headers=_h(client_token)).json()["ai_ready"] is True
    after = client.post("/api/billing/checkout", headers=_h(client_token),
                        json={"plan_ids": ["data_engineering", "ai"], "interval": "month"})
    assert after.status_code == 200, after.text


CUSTOMERS = ("customer_id,customer_name,city\n" + "".join(f"{100 + i},Customer {i},{['Atlanta', 'Macon'][i % 2]}\n"
                                                         for i in range(7))).encode()


def test_combine_join_and_union_before_loading():
    """
    Two different tables in one upload: JOIN them on customer_id (each sales row gets the
    customer's name and city), or STACK them; a key that repeats on both sides is refused.
    """
    from api.services import combine

    sales = _sales_df()
    customers = pd.read_csv(pd.io.common.BytesIO(CUSTOMERS))
    joined, name = combine.combine(sales, "sales.csv", [("customers.csv", customers)],
                                   {"mode": "join", "left_on": "customer_id", "right_on": "customer_id"})
    assert name == "sales.csv + customers.csv" and len(joined) == len(sales)
    assert joined.loc[0, "customer_name"] == "Customer 0" and "city" in joined.columns
    stacked, _ = combine.combine(sales, "sales.csv", [("customers.csv", customers)], {"mode": "union"})
    assert len(stacked) == len(sales) + len(customers) and set(stacked["source_table"]) == {"sales.csv", "customers.csv"}
    with pytest.raises(ValueError, match="repeats rows"):
        combine.combine(sales, "a", [("b", sales)], {"mode": "join", "left_on": "customer_id"})
    with pytest.raises(ValueError, match="isn't a column"):
        combine.combine(sales, "a", [("b", customers)], {"mode": "join", "left_on": "nope"})

    # Through the API: the preview and the load use the joined table.
    coach = _signup("coach@example.com", "Coach")["access_token"]
    h = _h(coach, _signup("ana@example.com", wants_consultant=True)["account"]["id"])
    files = _files() + _files(CUSTOMERS, "customers.csv")
    spec = '{"mode": "join", "groups": [1], "left_on": "customer_id", "right_on": "customer_id"}'
    preview = client.post("/api/warehouse/preview", headers=h, files=files, data={"group_id": "0", "combine": spec}).json()
    assert preview["source_name"] == "sales.csv + customers.csv", preview
    assert "customer_name" in [c["column"] for c in preview["column_plan"]]
    ok = client.post("/api/warehouse/load", headers=h, files=files,
                     data={"group_id": "0", "combine": spec, "fingerprint": preview["fingerprint"], "consent": "true"})
    assert ok.status_code == 200 and ok.json()["summary"]["sources"][0]["source_name"] == "sales.csv + customers.csv"


def test_update_the_most_recent_upload_replaces_it():
    """
    "Update my most recent upload": the new file REPLACES the latest stored file (rows that
    are gone disappear too) under the same name; any other source is refused.
    """
    coach = _signup("coach@example.com", "Coach")["access_token"]
    h = _h(coach, _signup("ana@example.com", wants_consultant=True)["account"]["id"])

    def load(body, name="sales.csv", replace=""):
        data = {"replace_source": replace} if replace else {}
        fp = client.post("/api/warehouse/preview", headers=h, files=_files(body, name), data=data).json()["fingerprint"]
        return client.post("/api/warehouse/load", headers=h, files=_files(body, name),
                           data={**data, "fingerprint": fp, "consent": "true"})

    load(SALES)
    summary = client.get("/api/warehouse/summary", headers=h).json()["warehouse"]
    assert summary["latest_upload"] == "sales.csv"
    before = summary["records"]
    smaller = b"\n".join(SALES.split(b"\n")[:36]) + b"\n"          # header + 35 lines
    res = load(smaller, "sales_fixed.csv", replace="sales.csv")
    assert res.status_code == 200, res.text
    after = res.json()["summary"]
    assert [s["source_name"] for s in after["sources"]] == ["sales.csv"]   # same name, replaced
    assert after["records"] < before
    bad = client.post("/api/warehouse/preview", headers=h, files=_files(), data={"replace_source": "other.csv"})
    assert bad.status_code == 400 and "most recent" in bad.json()["detail"]


def test_records_storage_packs_and_unpacks_without_losing_anything():
    """
    The database stores ONE row per record line (fact.records: its numbers together in
    `metrics`) and each field ONCE (fact.fields). Packing a plan and unpacking it gives
    back every number, detail and fingerprint (warehouse_store._pack / _unpack).
    """
    import json

    import pandas as pd

    from api.services import unified, warehouse_store
    from api.services.warehouse_loader import build_plan

    sales = pd.read_csv("samples/sales.csv").head(300)
    rows = build_plan(sales, "sales.csv").rows
    records, fields, numbers = warehouse_store._pack(rows)
    assert len(records) == rows[["record_id", "line_number"]].drop_duplicates().shape[0] < len(rows)
    assert [f["field_name"] for f in fields] == ["quantity", "unit_price", "line_amount"]   # described once each
    assert sum(numbers.values()) == len(rows)
    # Through JSON, as the database stores it.
    stored = [{**r, "metrics": json.loads(json.dumps(r["metrics"]))} for r in records]
    back = pd.DataFrame(warehouse_store._unpack(stored, {f["field_name"]: f for f in fields}),
                        columns=unified.UNIFIED_COLUMNS)
    key = ["record_id", "line_number", "field_name"]
    a = rows.sort_values(key).reset_index(drop=True)
    b = back.sort_values(key).reset_index(drop=True)
    cols = key + ["metric_type", "metric_unit", "source_field"]
    assert a[cols].astype(str).values.tolist() == b[cols].astype(str).values.tolist()
    assert (a["metric_value"].astype(float) - b["metric_value"].astype(float)).abs().max() < 1e-9
    assert a["row_hash"].tolist() == b["row_hash"].tolist()                      # same fingerprints
    # A line with no numbers comes back as its one NO_METRIC row.
    empty = warehouse_store._unpack([{**stored[0], "metrics": {}}], {})
    assert [r["field_name"] for r in empty] == [unified.NO_METRIC]


def test_uploading_the_same_spreadsheet_again_shows_whats_new_and_updates_it():
    """Same file name, rows and a column added, one amount changed: the preview says what's new; the load updates."""
    coach = _signup("coach@example.com", "Coach")["access_token"]
    client_id = _signup("ana@example.com", wants_consultant=True)["account"]["id"]
    h = _h(coach, client_id)
    first = client.post("/api/warehouse/preview", headers=h, files=_files()).json()
    assert first["changes"] is None                                   # nothing stored under this name yet
    client.post("/api/warehouse/load", headers=h, files=_files(),
                data={"fingerprint": first["fingerprint"], "consent": "true"})

    lines = SALES.decode().splitlines()
    header = lines[0] + ",store"
    body = [ln + ",Midtown" for ln in lines[1:]]
    body[0] = body[0].replace(",Card,", ",Cash,")                   # one existing row edited
    body += ["2001,2024-11-01,101,Bagel,Bread,5.5,Card,Midtown", "2002,2024-11-02,102,Latte,Drinks,4.0,Card,Decatur"]
    grown = ("\n".join([header, *body]) + "\n").encode()
    again = client.post("/api/warehouse/preview", headers=h, files=_files(grown)).json()
    ch = again["changes"]
    assert ch["source"] == "sales.csv" and ch["new_rows"] == 2 and ch["new_columns"] == ["store"]
    assert ch["changed_rows"] >= 1 and ch["removed_columns"] == []   # the new column changes every old row too
    loaded = client.post("/api/warehouse/load", headers=h, files=_files(grown),
                         data={"fingerprint": again["fingerprint"]}).json()["result"]
    assert loaded["rows_loaded"] > 0 and loaded["rows_updated"] > 0  # consent remembered: one click
    after = client.post("/api/warehouse/preview", headers=h, files=_files(grown)).json()["changes"]
    assert after["new_rows"] == 0 and after["changed_rows"] == 0     # stored copy is now up to date
