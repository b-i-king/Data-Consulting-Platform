"""
End-to-end API tests using FastAPI's TestClient (in-memory store, no DB).

These walk the same path a guest takes in the browser:
    options -> analyze (guest) -> download blocked -> sign up -> download.
"""

import io

import pandas as pd
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

CSV = b"customer,amount\nAnn,10\nAnn,10\nBob,\n"


def _files(*pairs):
    return [("files", (name, io.BytesIO(data), "text/csv")) for name, data in pairs]


def test_health():
    assert client.get("/health").json()["status"] == "ok"


def test_intake_options_lists_questions():
    body = client.get("/api/intake/options").json()
    assert {a["id"] for a in body["account_types"]} == {"individual", "business"}
    assert body["data_locations"][0]["id"] == "flat_files"
    assert any(s["id"] == "data_cleaning" for s in body["services"])


def test_guest_analyze_then_signup_then_download():
    # 1. Guest analysis — no token needed.
    res = client.post("/api/files/analyze", files=_files(("sales.csv", CSV)))
    assert res.status_code == 200, res.text
    body = res.json()
    group = body["groups"][0]
    assert group["report"]["duplicate_rows_removed"] == 1
    assert group["preview"]["total_rows"] == 2
    assert body["runtime"]["billable_minutes"] == 1

    # 2. Download without an account is refused.
    res = client.post("/api/files/download", files=_files(("sales.csv", CSV)))
    assert res.status_code == 401

    # 3. Business signup with one team member.
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Dana", "last_name": "Lee",
        "email": "dana@example.com", "company_name": "Lee Bakery",
        "team_members": [{"first_name": "Sam", "last_name": "Fox", "email": "sam@example.com"}],
    })
    assert res.status_code == 201, res.text
    token = res.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    team = client.get("/api/accounts/team", headers=headers).json()
    assert len(team["members"]) == 2

    # 4. Download works now and contains the cleaned rows.
    res = client.post("/api/files/download", headers=headers,
                      files=_files(("sales.csv", CSV)), data={"file_format": "csv"})
    assert res.status_code == 200
    df = pd.read_csv(io.BytesIO(res.content), encoding="utf-8-sig")
    assert len(df) == 2
    assert "X-Amount-USD" in res.headers


def test_individual_cannot_add_team_members():
    res = client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "A", "last_name": "B",
        "email": "solo@example.com",
        "team_members": [{"first_name": "C", "last_name": "D", "email": "c@example.com"}],
    })
    assert res.status_code == 422


def test_unsupported_file_returns_friendly_400():
    res = client.post("/api/files/analyze", files=[("files", ("x.png", io.BytesIO(b"x"), "image/png"))])
    assert res.status_code == 400
    assert "not a supported file type" in res.json()["detail"]


def test_large_responses_are_gzip_compressed():
    rows = "\n".join(f"Customer {i},{i}.50" for i in range(2000))
    csv = f"customer,amount\n{rows}\n".encode()
    res = client.post("/api/files/analyze", files=_files(("big.csv", csv)),
                      headers={"Accept-Encoding": "gzip"})
    assert res.status_code == 200
    assert res.headers.get("content-encoding") == "gzip"


# ── Data skills API ──────────────────────────────────────────────────────────

SALES = (__import__("pathlib").Path(__file__).resolve().parents[1] / "samples" / "sales.csv").read_bytes()


def test_list_skills():
    skills = client.get("/api/skills").json()
    assert len(skills) == 30
    assert {s["category"] for s in skills} >= {"data_cleaning", "data_visualization", "ai"}


def test_run_dashboard_skill_on_upload():
    res = client.post("/api/skills/build-dashboard/run", files=_files(("sales.csv", SALES)),
                      data={"params": '{"value_columns": ["amount"]}'})
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body["data"]["ranges"]) == {"last_4_weeks", "last_3_months", "last_1_year", "month_to_date", "year_to_date", "all"}
    assert "html_dashboard" in body["artifacts"]


def test_raw_skill_sees_duplicates_and_cleaned_skill_does_not():
    raw = client.post("/api/skills/data-cleaning/run", files=_files(("sales.csv", SALES))).json()
    assert raw["data"]["report"]["duplicate_rows_removed"] > 0
    cleaned = client.post("/api/skills/validate-data/run", files=_files(("sales.csv", SALES)),
                          data={"params": '{"metric": "amount"}'}).json()
    dup_check = next(c for c in cleaned["tables"]["checks"] if c["check"] == "duplicate rows")
    assert dup_check["status"] == "pass"


def test_skill_errors_are_friendly():
    assert client.post("/api/skills/nope/run", files=_files(("s.csv", CSV))).status_code == 404
    res = client.post("/api/skills/hypothesis-testing/run", files=_files(("s.csv", CSV)), data={"params": "{}"})
    assert res.status_code == 400 and "outcome" in res.json()["detail"]
    res = client.post("/api/skills/setup-data-workspace/run", files=_files(("s.csv", CSV)),
                      data={"params": '{"db_path": "/tmp/evil.db"}'})
    assert res.status_code == 400 and "command line" in res.json()["detail"]
