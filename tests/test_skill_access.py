"""
Paid tools stay paid (api/services/skill_access.py): the SERVER decides who gets
a skill's full result. Data Cleaning is free; any other tool needs it in the
package — without it, ONE small preview per tool per visit, then 402.
"""

import io
import time
from pathlib import Path

from fastapi.testclient import TestClient

from api.main import app
from api.services import skill_access

client = TestClient(app)
PW = "Correct-horse-9"
SALES = (Path(__file__).resolve().parents[1] / "samples" / "sales.csv").read_bytes()
SMALL = b"customer,amount\nAnn,10\nBob,12\n"   # too few rows for analysis


def _files(name="sales.csv", data=SALES):
    """One uploaded file, as the browser sends it."""
    return [("files", (name, io.BytesIO(data), "text/csv"))]


def _run(skill, headers=None, **data):
    """POST /api/skills/<skill>/run on the sample sales file."""
    return client.post(f"/api/skills/{skill}/run", files=_files(), headers=headers or {}, data=data)


def _client(email, plan_ids=()):
    """Sign up a client and (test mode, no Stripe) switch on a package; returns their headers."""
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Ana",
                                                    "last_name": "Lee", "email": email, "password": PW,
                                                    "accept_terms": True})
    assert res.status_code == 201, res.text
    h = {"Authorization": f"Bearer {res.json()['access_token']}"}
    if plan_ids:
        done = client.post("/api/billing/checkout", headers=h, json={"plan_ids": list(plan_ids), "interval": "month"})
        assert done.json()["activated"], done.text
    return h


def test_data_cleaning_is_free_for_everyone_every_time():
    for _ in range(3):
        res = _run("data-cleaning")
        assert res.status_code == 200 and res.json()["preview"] is False


def test_a_guest_gets_one_small_dashboard_preview_per_visit():
    res = _run("build-dashboard")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["preview"] is True and body["preview_service"] == "data_visualization"
    # The headline KPIs only — no charts, no data behind them, nothing to download.
    [(key, kpis)] = body["tables"].items()
    assert key.startswith("kpis_") and 1 <= len(kpis) <= skill_access.PREVIEW_KPIS
    assert body["charts"] == [] and body["data"] == {} and body["artifacts"] == {}
    assert len(body["findings"]) <= skill_access.PREVIEW_FINDINGS
    assert body["preview_hidden"]["charts"] >= 1 and "Add Data Visualization" in body["preview_note"]
    # Once per tool per visit: the dashboard again, or another Data Visualization skill, is refused.
    again = _run("build-dashboard")
    assert again.status_code == 402 and "Data Visualization" in again.json()["detail"]
    assert _run("create-viz").status_code == 402
    # A DIFFERENT tool still has its own preview.
    other = _run("explore-data")
    assert other.status_code == 200 and other.json()["preview"] is True
    assert other.json()["tables"] == {} and other.json()["charts"] == []


def test_a_failed_run_does_not_use_up_the_preview():
    small = client.post("/api/skills/build-dashboard/run", files=_files("s.csv", SMALL))
    assert small.status_code == 400                       # too few rows: nothing ran
    assert _run("build-dashboard").json()["preview"] is True


def test_a_new_visit_gets_a_new_preview():
    assert _run("build-dashboard").status_code == 200
    assert _run("build-dashboard").status_code == 402
    # SKILL_PREVIEW_HOURS later (the visit is over):
    for key in skill_access._previews:
        skill_access._previews[key] = time.time() - 1
    assert _run("build-dashboard").status_code == 200


def test_the_package_unlocks_the_full_result():
    without = _client("no-package@example.com")
    assert _run("build-dashboard", without).json()["preview"] is True      # signed in, but not in the package
    with_viz = _client("viz@example.com", ["data_visualization"])
    for _ in range(2):                                                      # no visit limit for paying clients
        full = _run("build-dashboard", with_viz).json()
        assert full["preview"] is False and full["charts"] and full["data"]["ranges"]
    # ask-your-data is also unlocked by Data Visualization (the dashboard's own tabs use it).
    asked = _run("ask-your-data", with_viz, params='{"question": "total amount by store"}')
    assert asked.status_code == 200 and asked.json()["preview"] is False
    # ...but a tool that isn't in the package is still a preview.
    assert _run("explore-data", with_viz).json()["preview"] is True


def test_a_bad_sign_in_is_refused_not_treated_as_a_guest():
    res = _run("build-dashboard", {"Authorization": "Bearer not-a-real-token"})
    assert res.status_code == 401
