"""
Tests for "Compare over time" (api/services/compare.py, api/routers/compare.py):
two tables with nothing in common but dates, added up by month or week,
lined up, with a correlation and its cautions.
"""

from __future__ import annotations

import json

import pandas as pd
from fastapi.testclient import TestClient

from api.main import app
from api.services import compare

client = TestClient(app)


def _viz_client(email="compare@example.com"):
    """A signed-in client with Data Visualization (test mode: switched on with no charge); their headers."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Ana", "last_name": "Lee",
                                                    "email": email, "password": "Correct-horse-9", "accept_terms": True})
    if res.status_code != 201:   # signed up by an earlier test
        res = client.post("/api/accounts/signin", json={"email": email, "password": "Correct-horse-9"})
    h = {"Authorization": f"Bearer {res.json()['access_token']}"}
    client.post("/api/billing/checkout", headers=h, json={"plan_ids": ["data_visualization"], "interval": "month"})
    return h

# A game tracker (hours played) and invoices (sales): no shared IDs or names.
GAMES = "\n".join(["played_at,game,hours"] + [
    f"2026-{m:02d}-{d:02d},Halo,{h}" for m, h in zip(range(1, 13), [10, 9, 8, 7, 6, 5, 4, 3, 2, 1, 1, 1])
    for d in (3, 17)]).encode()
INVOICES = "\n".join(["invoice_date,customer_name,amount"] + [
    f"2026-{m:02d}-{d:02d},Client {d},{a}" for m, a in zip(range(1, 13), [100, 120, 140, 160, 180, 200, 220,
                                                                           240, 260, 280, 290, 300])
    for d in (5, 20)]).encode()


def _files():
    return [("files", ("games.csv", GAMES, "text/csv")), ("files", ("invoices.csv", INVOICES, "text/csv"))]


def _groups():
    res = client.post("/api/files/analyze", files=_files()).json()
    return {g["label"]: g["group_id"] for g in res["categorization"]["groups"]}


def test_two_unrelated_tables_line_up_by_month():
    ids = list(_groups().values())
    assert len(ids) == 2
    spec = {"period": "month", "left": {"group_id": ids[0], "date": "played_at", "value": "hours", "agg": "sum"},
            "right": {"group_id": ids[1], "date": "invoice_date", "value": "amount", "agg": "sum"}}
    # Part of the Dashboard page: sign-in and Data Visualization are required.
    assert client.post("/api/compare/over-time", files=_files(), data={"spec": json.dumps(spec)}).status_code == 401
    res = client.post("/api/compare/over-time", files=_files(), data={"spec": json.dumps(spec)}, headers=_viz_client())
    assert res.status_code == 200, res.text
    r = res.json()
    assert r["periods"][0] == "2026-01-01" and r["overlap"] == 12
    assert r["left"]["values"][0] == 20.0 and r["right"]["values"][0] == 200.0
    assert r["correlation"] < -0.9 and "strong negative" in r["summary"]
    assert any("doesn't mean one causes" in c for c in r["cautions"])


def test_few_periods_get_a_caution_or_no_correlation():
    a = pd.Series({"2026-01-01": 1.0, "2026-02-01": 2.0, "2026-03-01": 3.0, "2026-04-01": 5.0})
    b = pd.Series({"2026-01-01": 2.0, "2026-02-01": 4.0, "2026-03-01": 6.0, "2026-04-01": 9.0})
    r = compare.compare(a, b, "A", "B", "month")
    assert r["correlation"] > 0.9 and any("Only 4 shared months" in c for c in r["cautions"])
    short = compare.compare(a.iloc[:2], b.iloc[:2], "A", "B", "month")
    assert short["correlation"] is None and "too few" in short["summary"]


def test_by_week_counting_rows():
    df = pd.DataFrame({"d": ["2026-01-05", "2026-01-06", "2026-01-13"]})
    s = compare.series(df, "d", None, "count", "week")
    assert s.to_dict() == {"2026-01-05": 2.0, "2026-01-12": 1.0}    # weeks start on Monday


def test_a_wrong_column_is_explained():
    ids = list(_groups().values())
    spec = {"period": "month", "left": {"group_id": ids[0], "date": "nope", "value": "hours"},
            "right": {"group_id": ids[1], "date": "invoice_date", "value": "amount"}}
    res = client.post("/api/compare/over-time", files=_files(), data={"spec": json.dumps(spec)}, headers=_viz_client())
    assert res.status_code == 400 and "'nope' isn't a column" in res.json()["detail"]


def test_compare_needs_data_visualization_in_the_package():
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Bo", "last_name": "Lee",
                                                    "email": "no-viz@example.com", "password": "Correct-horse-9",
                                                    "accept_terms": True})
    h = {"Authorization": f"Bearer {res.json()['access_token']}"}
    spec = {"period": "month", "left": {"group_id": 0, "date": "played_at"}, "right": {"group_id": 1, "date": "invoice_date"}}
    refused = client.post("/api/compare/over-time", files=_files(), data={"spec": json.dumps(spec)}, headers=h)
    assert refused.status_code == 403 and "Data Visualization" in refused.json()["detail"]
