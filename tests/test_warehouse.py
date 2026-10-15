"""
Data Engineering: preview how data will be transformed, then load it only if accepted.
(api/services/warehouse_loader.py, warehouse_store.py, api/routers/warehouse.py)

Runs on the in-memory warehouse (no DATABASE_URL), which follows the same
rules as the database: one customer per customer, one product per product,
and loading the same rows twice never duplicates them.
"""

from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, warehouse_store
from api.services.warehouse_loader import build_plan, suggest_mapping

client = TestClient(app)
PW = "Correct-horse-9"
SAMPLES = Path(__file__).resolve().parents[1] / "samples"

SALES = (
    "order_id,order_date,customer_id,product,category,amount,payment_method\n"
    + "".join(f"{1000 + i},2024-10-{(i % 28) + 1:02d},{100 + i % 7},{['Bagel', 'Scone', 'Latte'][i % 3]},"
              f"{['Bread', 'Pastry', 'Drinks'][i % 3]},{(i % 9) + 2.5 if i % 10 else -4.0},Card\n" for i in range(40))
    + "9999,not a date,101,Bagel,Bread,3.0,Cash\n"
).encode()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """
    A consultant, no database, and empty in-memory stores for every test.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.

    Returns:
        Iterator[None]: yields once while the test runs.
    """
    monkeypatch.setenv("CONSULTANT_EMAILS", "coach@example.com")
    monkeypatch.setenv("CONSULTANT_ACCESS_CODE", "code-123")
    monkeypatch.setenv("DATABASE_URL", "")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants, account_store._consents,
                  account_store._load_runs, warehouse_store._clients, warehouse_store._sources,
                  warehouse_store._customers, warehouse_store._products, warehouse_store._facts):
        store.clear()
    yield
    get_settings.cache_clear()


def _signup(email, first="Ana", **extra):
    """Sign up an individual (asserting 201) and return the JSON reply."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": first,
                                                    "last_name": "Lee", "email": email, "password": PW, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def _h(token, acting_as=None):
    """Auth headers, optionally acting for a client."""
    h = {"Authorization": f"Bearer {token}"}
    if acting_as:
        h["X-Acting-As"] = acting_as
    return h


def _files():
    """The sales upload, as TestClient multipart files."""
    return [("files", ("sales.csv", SALES, "text/csv"))]


def test_plan_explains_each_transformation():
    """The plan guesses the mapping, explains WHY for each step, and previews every table."""
    df = pd.read_csv(pd.io.common.BytesIO(SALES))
    plan = build_plan(df, "sales.csv")
    assert plan.event_type == "transaction/income"
    assert plan.mapping["date"] == "order_date" and plan.mapping["customer_id"] == "customer_id"
    assert plan.counts == {"rows_in": 41, "rows_to_load": 40, "rows_skipped": 1, "customers": 7, "products": 3}
    assert plan.skipped == {"no readable date": 1}
    assert all(step["why"] for step in plan.steps)
    titles = " | ".join(s["title"] for s in plan.steps)
    assert "negative amount" in titles and "customers into their own table" in titles
    assert set(plan.tables) == {"fact_event", "dim_customer", "dim_product"}
    assert len(plan.tables["fact_event"]["rows"]) == 5 and plan.tables["fact_event"]["total"] == 40
    # Negative sales became positive refunds.
    refunds = [f for f in plan.facts if f["event_type"] == "transaction/refund"]
    assert refunds and all(f["amount"] > 0 for f in refunds)
    # Unused columns are kept as extras.
    assert plan.facts[0]["attributes"] == {"payment_method": "Card"}


def test_mapping_changes_change_the_fingerprint():
    """Changing a choice (or the data) gives a new fingerprint; an unknown column is refused."""
    df = pd.read_csv(pd.io.common.BytesIO(SALES))
    a = build_plan(df, "sales.csv")
    b = build_plan(df, "sales.csv", mapping={"product": ""})          # switch products off
    assert a.fingerprint != b.fingerprint and b.counts["products"] == 0
    assert build_plan(df, "sales.csv").fingerprint == a.fingerprint   # same input, same fingerprint
    with pytest.raises(ValueError):
        build_plan(df, "sales.csv", mapping={"date": "nope"})


def test_no_date_means_nothing_to_load():
    """A table without a usable date column can't be loaded, and says why."""
    plan = build_plan(pd.DataFrame({"name": ["A", "B"], "amount": [1, 2]}), "list.csv")
    assert plan.problem and "date" in plan.problem
    assert plan.to_dict()["ready"] is False


def test_sample_files_get_sensible_guesses():
    """The bakery samples: sales -> income with products; expenses -> expenses; feedback -> CSAT."""
    expenses = build_plan(pd.read_csv(SAMPLES / "expenses.csv"), "expenses.csv")
    assert expenses.event_type == "transaction/expense"
    assert expenses.mapping["product_category"] is None            # category stays in the extras
    feedback = build_plan(pd.read_csv(SAMPLES / "feedback.csv"), "feedback.csv")
    assert feedback.event_type == "feedback/csat"
    sales = suggest_mapping(pd.read_csv(SAMPLES / "sales.csv"))
    assert sales["customer_name"] is None                           # "customer_type" isn't a name


def test_preview_then_accept_then_reload_is_safe():
    """Full flow: preview stores nothing; load needs consent + the same fingerprint; reloading adds nothing."""
    coach = _signup("coach@example.com", "Coach", access_code="code-123")["access_token"]
    client_id = _signup("ana@example.com", wants_consultant=True)["account"]["id"]
    h = _h(coach, client_id)

    preview = client.post("/api/warehouse/preview", headers=h, files=_files()).json()
    assert preview["ready"] and preview["storage"] == {"mode": "memory", "consented": False,
                                                        "terms_version": preview["storage"]["terms_version"]}
    assert warehouse_store._facts == {}                              # preview stores nothing

    no_consent = client.post("/api/warehouse/load", headers=h, files=_files(),
                             data={"fingerprint": preview["fingerprint"]})
    assert no_consent.status_code == 400
    stale = client.post("/api/warehouse/load", headers=h, files=_files(),
                        data={"fingerprint": "old-preview", "consent": "true"})
    assert stale.status_code == 409                                  # not what was accepted

    ok = client.post("/api/warehouse/load", headers=h, files=_files(),
                     data={"fingerprint": preview["fingerprint"], "consent": "true"}).json()
    assert ok["result"]["rows_loaded"] == 40 and ok["load_run"]["status"] == "succeeded"
    assert ok["summary"]["events"] == 40 and ok["summary"]["customers"] == 7 and ok["summary"]["products"] == 3

    again = client.post("/api/warehouse/load", headers=h, files=_files(),
                        data={"fingerprint": preview["fingerprint"]}).json()   # consent already on file
    assert again["result"] == {"rows_loaded": 0, "rows_already_stored": 40, "customers": 7, "products": 3}

    summary = client.get("/api/warehouse/summary", headers=h).json()
    assert summary["storage"]["consented"] is True and len(summary["loads"]) == 2
    assert summary["warehouse"]["first_date"] == "2024-10-01"


def test_needs_data_engineering():
    """A do-it-yourself client without Data Engineering can't preview or load (403)."""
    diy = _signup("sam@example.com", "Sam")["access_token"]
    assert client.post("/api/warehouse/preview", headers=_h(diy), files=_files()).status_code == 403
    assert client.get("/api/warehouse/summary", headers=_h(diy)).status_code == 403
