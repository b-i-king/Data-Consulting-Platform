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
    """
    Build the multipart "files" field TestClient sends, as a browser upload would.

    Args:
        *pairs: (file_name, file_bytes) tuples, e.g. ("sales.csv", b"customer,amount\\nAnn,10\\n").

    Returns:
        list[tuple]: One ("files", (name, BytesIO, "text/csv")) entry per pair, ready to pass
        as `files=` to client.post(...).

    Example:
        >>> client.post("/api/files/analyze", files=_files(("sales.csv", CSV)))
    """
    return [("files", (name, io.BytesIO(data), "text/csv")) for name, data in pairs]


def test_health():
    """Check that GET /health answers {"status": "ok"} (the uptime check Render uses)."""
    assert client.get("/health").json()["status"] == "ok"


def test_intake_options_lists_questions():
    """
    Check that GET /api/intake/options returns the signup questions: both account types,
    "flat_files" as the first data location, and Data Cleaning among the services.
    """
    body = client.get("/api/intake/options").json()
    assert {a["id"] for a in body["account_types"]} == {"individual", "business"}
    assert body["data_locations"][0]["id"] == "flat_files"
    assert any(s["id"] == "data_cleaning" for s in body["services"])


def test_guest_analyze_then_signup_then_download():
    """
    Walk the guest path end to end: analyze without an account (duplicates removed, runtime
    estimate), download refused with 401, business signup with a team member, then a
    successful CSV download whose charge never exceeds the amount the client confirmed.
    """
    # 1. Guest analysis — no token needed.
    res = client.post("/api/files/analyze", files=_files(("sales.csv", CSV)))
    assert res.status_code == 200, res.text
    body = res.json()
    group = body["groups"][0]
    assert group["report"]["duplicate_rows_removed"] == 1
    assert group["preview"]["total_rows"] == 2
    assert body["runtime"]["billable_minutes"] == 1
    est = group["download_estimate"]
    assert est["rows"] == 2 and est["columns"] == 2
    assert est["amount_usd"] == body["runtime"]["amount_usd"]
    assert est["formats"]["csv"]["label"] == "under 10 seconds"

    # 2. Download without an account is refused.
    res = client.post("/api/files/download", files=_files(("sales.csv", CSV)))
    assert res.status_code == 401

    # 3. Business signup with one team member.
    res = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "account_type": "business", "first_name": "Dana", "last_name": "Lee",
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

    # 5. The charge never goes above the total the client confirmed.
    res = client.post("/api/files/download", headers=headers, files=_files(("sales.csv", CSV)),
                      data={"file_format": "csv", "confirmed_amount_usd": "0.25"})
    assert res.status_code == 200
    assert res.headers["X-Amount-USD"] == "0.25"


def test_individual_cannot_add_team_members():
    """Check that an individual (not business) signup with team members is rejected with 422."""
    res = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "account_type": "individual", "first_name": "A", "last_name": "B",
        "email": "solo@example.com",
        "team_members": [{"first_name": "C", "last_name": "D", "email": "c@example.com"}],
    })
    assert res.status_code == 422


def test_unsupported_file_returns_friendly_400():
    """Check that uploading an unsupported file type (a .png) gives a 400 with a plain-English message."""
    res = client.post("/api/files/analyze", files=[("files", ("x.png", io.BytesIO(b"x"), "image/png"))])
    assert res.status_code == 400
    assert "not a supported file type" in res.json()["detail"]


def test_large_responses_are_gzip_compressed():
    """Check that a large analyze response is gzip-compressed when the client accepts gzip."""
    rows = "\n".join(f"Customer {i},{i}.50" for i in range(2000))
    csv = f"customer,amount\n{rows}\n".encode()
    res = client.post("/api/files/analyze", files=_files(("big.csv", csv)),
                      headers={"Accept-Encoding": "gzip"})
    assert res.status_code == 200
    assert res.headers.get("content-encoding") == "gzip"


# ── Data skills API ──────────────────────────────────────────────────────────

SALES = (__import__("pathlib").Path(__file__).resolve().parents[1] / "samples" / "sales.csv").read_bytes()


def test_list_skills():
    """Check that GET /api/skills lists all 31 skills, spread across the service categories."""
    skills = client.get("/api/skills").json()
    assert len(skills) == 31
    assert {s["category"] for s in skills} >= {"data_cleaning", "data_visualization", "ai"}


def test_run_dashboard_skill_on_upload():
    """
    Check that running build-dashboard on the sample sales upload returns every date range
    (last week through all time) and an HTML dashboard artifact.
    """
    res = client.post("/api/skills/build-dashboard/run", files=_files(("sales.csv", SALES)),
                      data={"params": '{"value_columns": ["amount"]}'})
    assert res.status_code == 200, res.text
    body = res.json()
    assert set(body["data"]["ranges"]) == {"last_1_week", "last_4_weeks", "last_3_months", "last_1_year", "month_to_date", "year_to_date", "all"}
    assert "html_dashboard" in body["artifacts"]


def test_raw_skill_sees_duplicates_and_cleaned_skill_does_not():
    """
    Check that a raw-data skill (data-cleaning) sees the sample's duplicate rows, while a
    skill that runs on cleaned data (validate-data) finds none.
    """
    raw = client.post("/api/skills/data-cleaning/run", files=_files(("sales.csv", SALES))).json()
    assert raw["data"]["report"]["duplicate_rows_removed"] > 0
    cleaned = client.post("/api/skills/validate-data/run", files=_files(("sales.csv", SALES)),
                          data={"params": '{"metric": "amount"}'}).json()
    dup_check = next(c for c in cleaned["tables"]["checks"] if c["check"] == "duplicate rows")
    assert dup_check["status"] == "pass"


# 40 rows: enough for analysis (the minimum is 30).
BIG = b"customer,amount\n" + b"".join(f"C{i},{i}\n".encode() for i in range(40))


def test_skill_errors_are_friendly():
    """
    Check skill run errors: an unknown skill is 404, a missing required option is 400 naming
    it, and a command-line-only option (db_path) sent through the API is 400.
    """
    assert client.post("/api/skills/nope/run", files=_files(("s.csv", BIG))).status_code == 404
    res = client.post("/api/skills/hypothesis-testing/run", files=_files(("s.csv", BIG)), data={"params": "{}"})
    assert res.status_code == 400 and "outcome" in res.json()["detail"]
    res = client.post("/api/skills/setup-data-workspace/run", files=_files(("s.csv", BIG)),
                      data={"params": '{"db_path": "/tmp/evil.db"}'})
    assert res.status_code == 400 and "command line" in res.json()["detail"]


GAPS = b"order_date,qty,city,notes\n2026-01-01,1,A,\n2026-01-02,2,A,\n2026-01-03,,,\n2026-01-04,4,B,x\n" + \
       b"".join(f"2026-02-{d:02d},{d},A,\n".encode() for d in range(1, 29))   # 32 rows in all


def test_analyze_applies_column_actions_and_previews_first_rows():
    """
    Check that per-column actions (fill qty with the median, drop notes) are applied by
    /api/files/analyze, and that the preview shows only the first 5 of 32 rows.
    """
    actions = '{"0": {"qty": "median", "notes": "drop"}}'
    res = client.post("/api/files/analyze", files=_files(("gaps.csv", GAPS)), data={"column_actions": actions})
    assert res.status_code == 200, res.text
    g = res.json()["groups"][0]
    assert g["report"]["columns_dropped"] == ["notes"] and g["report"]["columns"] == 3
    assert g["report"]["cells_filled"] == 1
    assert g["preview"]["columns"] == ["order_date", "qty", "city"]
    assert len(g["preview"]["rows"]) == 5 and g["preview"]["total_rows"] == 32 and g["preview"]["truncated"]
    notes = next(c for c in g["report"]["column_reports"] if c["column"] == "notes")
    assert notes["missing_level"] == "remove"


def test_bad_column_actions_are_friendly_errors():
    """Check that invalid column_actions JSON or an unknown fill method gives a 400 that names the problem."""
    bad_json = client.post("/api/files/analyze", files=_files(("gaps.csv", GAPS)), data={"column_actions": "nope"})
    bad_method = client.post("/api/files/analyze", files=_files(("gaps.csv", GAPS)),
                             data={"column_actions": '{"0": {"qty": "average"}}'})
    assert bad_json.status_code == 400 and bad_method.status_code == 400
    assert "average" in bad_method.json()["detail"]


def test_skills_see_the_same_cleaning_choices():
    """Check that skills apply the same column actions as analyze (a dropped column is not in the output)."""
    res = client.post("/api/skills/explore-data/run", files=_files(("gaps.csv", GAPS)),
                      data={"column_actions": '{"0": {"notes": "drop"}}'})
    assert res.status_code == 200, res.text
    assert '"notes"' not in res.text


def test_analysis_needs_30_rows_before_and_after_cleaning():
    """
    Check the 30-row minimum for analysis: 3 rows can be cleaned but not analyzed; 35 rows with
    10 duplicates (25 after cleaning) are still refused; 40 rows are ready.
    """
    # 3 rows: cleaning works, analysis is refused with a clear reason.
    small = client.post("/api/files/analyze", files=_files(("s.csv", CSV))).json()["groups"][0]["report"]
    assert small["analysis_ready"] is False and "at least 30" in small["analysis_note"]
    assert client.post("/api/skills/data-cleaning/run", files=_files(("s.csv", CSV))).status_code == 200
    res = client.post("/api/skills/explore-data/run", files=_files(("s.csv", CSV)))
    assert res.status_code == 400 and "at least 30" in res.json()["detail"]
    # 35 rows uploaded, but 10 are duplicates: 25 left AFTER cleaning -> still refused.
    dupes = b"customer,amount\n" + b"".join(f"C{i},{i}\n".encode() for i in range(25)) + b"C1,1\n" * 10
    rep = client.post("/api/files/analyze", files=_files(("d.csv", dupes))).json()["groups"][0]["report"]
    assert rep["rows_before"] == 35 and rep["rows_after"] == 25
    assert rep["analysis_ready"] is False and "After cleaning" in rep["analysis_note"]
    assert client.post("/api/skills/build-dashboard/run", files=_files(("d.csv", dupes))).status_code == 400
    # 40 rows: ready.
    big = client.post("/api/files/analyze", files=_files(("b.csv", BIG))).json()["groups"][0]["report"]
    assert big["analysis_ready"] is True and big["analysis_note"] == ""


def test_settings_tolerate_pasted_urls_and_preview_origins(monkeypatch):
    """
    Check that ALLOWED_ORIGINS tolerates trailing slashes and spaces, and that the default JWT
    secret is detected (and a real 40-character secret is not).

    Args:
        monkeypatch: pytest fixture (requested but not used by this test).
    """
    from api.core.config import Settings
    s = Settings(ALLOWED_ORIGINS="https://site.vercel.app/, http://localhost:3000")
    assert s.origins_list == ["https://site.vercel.app", "http://localhost:3000"]
    assert Settings().using_default_jwt_secret and not Settings(JWT_SECRET_KEY="x" * 40).using_default_jwt_secret


def test_a_forgotten_account_signs_out_with_a_clear_message():
    """
    Check that a valid token for an account the server no longer has gets a 401 asking the user
    to create their account again.
    """
    # A valid token for an account the (database-less) server no longer has.
    from api.core.security import create_access_token
    token = create_access_token("00000000-0000-0000-0000-000000000000", "gone@example.com", "individual")
    res = client.get("/api/accounts/services", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 401 and "create your account again" in res.json()["detail"]


def test_excel_download_lists_rows_removed_for_a_missing_id():
    """
    Check that dropping rows with a blank ID removes them from the Excel download's
    "cleaned_data" sheet and lists them on a separate "rows_missing_id" sheet.
    """
    token = client.post("/api/accounts/signup", json={
        "password": "Correct-horse-9", "account_type": "individual", "first_name": "Ida", "last_name": "Ng", "email": "ida@example.com",
    }).json()["access_token"]
    rows = "".join(f"{'' if i % 10 == 0 else i},{i}\n" for i in range(40))   # 4 blank IDs
    csv = f"Order ID,Total Amount\n{rows}".encode()
    body = client.post("/api/files/analyze", files=_files(("orders.csv", csv))).json()
    report = body["groups"][0]["report"]
    assert [c["column"] for c in report["column_reports"]] == ["order_id", "total_amount"]
    assert report["column_reports"][0]["suggested_fill"] == "drop_rows"
    res = client.post("/api/files/download", headers={"Authorization": f"Bearer {token}"},
                      files=_files(("orders.csv", csv)),
                      data={"file_format": "xlsx", "column_actions": '{"0": {"order_id": "drop_rows"}}'})
    assert res.status_code == 200, res.text
    sheets = pd.read_excel(io.BytesIO(res.content), sheet_name=None)
    assert len(sheets["cleaned_data"]) == 36
    assert sheets["rows_missing_id"]["total_amount"].tolist() == [0, 10, 20, 30]


def test_client_column_names_apply_to_analyze_and_skills():
    """
    Check that duplicate and unnamed columns are reported, that client-chosen column names fix
    them in analyze and skills, and that renaming two columns to the same name gives a 400.
    """
    rows = "".join(f"{i},{i * 2},{i * 3},\n" for i in range(40))
    csv = f"order_id,amount,amount,\n{rows}".encode()
    body = client.post("/api/files/analyze", files=_files(("o.csv", csv))).json()
    report = body["groups"][0]["report"]
    assert report["duplicate_columns"] == [["amount", "amount_2"]]
    assert report["unnamed_columns"] == ["unnamed_column_4"]

    names = '{"0": {"amount": "Amount Paid", "amount_2": "Amount Refunded"}}'
    actions = '{"0": {"unnamed_column_4": "drop"}}'
    body = client.post("/api/files/analyze", files=_files(("o.csv", csv)),
                       data={"column_names": names, "column_actions": actions}).json()
    g = body["groups"][0]
    assert g["preview"]["columns"] == ["order_id", "amount_paid", "amount_refunded"]
    assert g["report"]["duplicate_columns"] == [] and g["report"]["unnamed_columns"] == []

    res = client.post("/api/skills/statistical-analysis/run", files=_files(("o.csv", csv)),
                      data={"column_names": names, "column_actions": actions})
    assert res.status_code == 200, res.text
    assert "amount_refunded" in res.text

    bad = client.post("/api/files/analyze", files=_files(("o.csv", csv)),
                      data={"column_names": '{"0": {"amount_2": "amount"}}'})
    assert bad.status_code == 400 and "share a name" in bad.json()["detail"]


def test_transform_steps_apply_to_analyze_and_skills():
    """
    Check that a transform step (cap duration outliers at 120) is applied by analyze and skills,
    and that an unknown step gives a 400.
    """
    rows = "".join(f"{i},{60 if i != 7 else 450}\n" for i in range(40))
    csv = f"order_id,duration\n{rows}".encode()
    body = client.post("/api/files/analyze", files=_files(("w.csv", csv))).json()
    assert body["groups"][0]["report"]["outlier_columns"] == []     # all 60s: no spread, nothing to compare
    steps = '{"0": [{"op": "outliers", "column": "duration", "method": "cap", "high": 120}]}'
    body = client.post("/api/files/analyze", files=_files(("w.csv", csv)), data={"transforms": steps}).json()
    report = body["groups"][0]["report"]
    assert report["transforms_applied"][0]["count"] == 1
    res = client.post("/api/skills/statistical-analysis/run", files=_files(("w.csv", csv)), data={"transforms": steps})
    assert res.status_code == 200, res.text
    bad = client.post("/api/files/analyze", files=_files(("w.csv", csv)), data={"transforms": '{"0": [{"op": "x"}]}'})
    assert bad.status_code == 400 and "unknown step" in bad.json()["detail"]
