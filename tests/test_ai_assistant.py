"""
Tests for Beacon, the AI assistant (api/services/ai_assistant.py, api/routers/ai.py):
free and premium tiers, admins unlimited (and simulating), a consultant using
the client's allowance, and the optional Google Gemini planner — which must
never see rows of data.
"""

from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, ai_assistant, entitlements
from tests.consultant_helpers import apply_as_consultant

client = TestClient(app)
PW = "Correct-horse-9"
CSV = ("store,customer,amount\n" + "\n".join(
    f"{'Midtown' if i % 2 else 'Decatur'},Customer {i},{i}" for i in range(1, 41))).encode()


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL", "STRIPE_SECRET_KEY", "GEMINI_API_KEY"):
        monkeypatch.setenv(key, "")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("AI_FREE_MONTHLY", "2")
    monkeypatch.setenv("AI_PREMIUM_MONTHLY", "3")
    get_settings.cache_clear()
    for store in (account_store._accounts, account_store._teams, account_store._consultants,
                  account_store._subscriptions, account_store._questions):
        store.clear()
    monkeypatch.setattr(account_store, "_ai_usage", {})
    yield
    ai_assistant.HTTP_TRANSPORT = None
    get_settings.cache_clear()


def _signup(email, **extra):
    res = client.post("/api/accounts/signup", json={"account_type": "individual", "first_name": "Ana", "last_name": "Lee",
                                                    "email": email, "password": PW, "accept_terms": True, **extra})
    assert res.status_code == 201, res.text
    return res.json()


def _h(token, acting=None):
    h = {"Authorization": f"Bearer {token}"}
    if acting:
        h["X-Acting-As"] = acting
    return h


def _ask(h, question="total amount by store", **data):
    return client.post("/api/ai/ask", headers=h, files={"files": ("sales.csv", CSV, "text/csv")},
                       data={"question": question, **data})


def test_free_tier_has_a_monthly_allowance():
    h = _h(_signup("ana@example.com")["access_token"])
    u = client.get("/api/ai/usage", headers=h).json()
    assert (u["tier"], u["used"], u["limit"], u["engine"]) == ("free", 0, 2, "built-in")
    first = _ask(h).json()
    assert first["answered"] and "Midtown" in first["result"]["summary"] and first["usage"]["used"] == 1
    cant = _ask(h, "why is the sky blue").json()                   # not answerable: still a question
    assert cant["answered"] is False and cant["message"] and cant["usage"]["left"] == 0
    over = _ask(h)
    assert over.status_code == 429 and "Beacon questions are used up" in over.json()["detail"]
    assert "Add the AI package" in over.json()["detail"]
    assert account_store._questions[-1]["question"] == "why is the sky blue"   # the text is logged


def test_premium_tier_gets_more(monkeypatch):
    real = entitlements.active_services

    async def with_ai(account, **kw):
        return [*await real(account, **kw), "ai"]
    monkeypatch.setattr(entitlements, "active_services", with_ai)
    h = _h(_signup("ana@example.com")["access_token"])
    for _ in range(3):
        assert _ask(h).status_code == 200
    u = client.get("/api/ai/usage", headers=h).json()
    assert (u["tier"], u["used"], u["limit"]) == ("premium", 3, 3)
    assert _ask(h).status_code == 429


def test_admins_have_no_limit_and_can_simulate():
    h = _h(_signup("boss@example.com")["access_token"])
    for _ in range(4):
        assert _ask(h).status_code == 200
    u = client.get("/api/ai/usage", headers=h).json()
    assert u["tier"] == "admin" and u["is_unlimited"] and u["limit"] is None
    sim = client.get("/api/ai/usage?simulate=free", headers=h).json()
    assert sim["simulating"] == "free" and sim["limit"] == 2 and sim["used"] == 0     # admins' questions aren't counted


def test_a_consultant_uses_the_clients_allowance():
    _signup("boss@example.com")
    asked = _signup("kim@example.com", first_name="Kim")
    admin = client.post("/api/accounts/signin", json={"email": "boss@example.com", "password": PW}).json()["access_token"]
    assert apply_as_consultant(client, _h(admin), asked["access_token"], "kim@example.com").status_code == 200
    assert client.post(f"/api/admin/consultant-requests/{asked['account']['id']}/approve", headers=_h(admin)).status_code == 200
    kim = client.post("/api/accounts/signin", json={"email": "kim@example.com", "password": PW}).json()["access_token"]
    kim_id = next(c["id"] for c in client.get("/api/accounts/consultants").json()["consultants"] if c["name"] == "Kim")
    bo = _signup("bo@example.com", wants_consultant=True, consultant_id=kim_id)
    as_kim = _h(kim, bo["account"]["id"])
    assert _ask(as_kim).status_code == 200
    u = client.get("/api/ai/usage", headers=as_kim).json()
    assert u["tier"] == "free" and u["used"] == 1 and u["acting_for"] == "Ana Lee"
    # The client sees the same count: it is their allowance.
    assert client.get("/api/ai/usage", headers=_h(bo["access_token"])).json()["used"] == 1


def _gemini_reply(plan):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(plan)}]}}]})


def test_gemini_plans_the_question_without_seeing_the_data(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    get_settings.cache_clear()
    sent = []

    def gemini(request: httpx.Request):
        body = json.loads(request.content)
        sent.append((request, body))
        assert request.headers["x-goog-api-key"] == "g-test"
        assert request.url.path.endswith("/models/gemini-2.5-flash-lite:generateContent")   # free -> Flash-Lite
        assert body["generationConfig"]["thinkingConfig"] == {"thinkingBudget": 0}             # cheapest: no thinking
        return _gemini_reply({"metric": "amount", "agg": "sum", "group_by": "store"})
    ai_assistant.HTTP_TRANSPORT = httpx.MockTransport(gemini)
    h = _h(_signup("ana@example.com")["access_token"])
    res = _ask(h, "which store sells the most?").json()            # the built-in parser can't read this one
    assert res["answered"] and res["engine"] == "gemini" and "Midtown" in res["result"]["summary"]
    usage = client.get("/api/ai/usage", headers=h).json()
    assert usage["engine"] == "gemini" and usage["model"] == "gemini-2.5-flash-lite"
    body = sent[0][1]
    schema = json.loads(body["contents"][0]["parts"][0]["text"])["schema"]
    store = next(c for c in schema if c["column"] == "store")
    customer = next(c for c in schema if c["column"] == "customer")
    assert store["values"] == ["Decatur", "Midtown"]                # few values: the model may use them
    assert "values" not in customer                                  # every row different: never sent
    assert "Customer 7" not in json.dumps(body)                      # no rows, ever

    # A plan naming a column that doesn't exist is thrown away; the built-in parser tries instead.
    ai_assistant.HTTP_TRANSPORT = httpx.MockTransport(lambda r: _gemini_reply({"metric": "profit", "agg": "sum"}))
    res = _ask(h, "total amount by store").json()
    assert res["answered"] and res["engine"] == "built-in"


def test_premium_uses_gemini_flash(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    get_settings.cache_clear()
    real = entitlements.active_services

    async def with_ai(account, **kw):
        return [*await real(account, **kw), "ai"]
    monkeypatch.setattr(entitlements, "active_services", with_ai)
    models = []

    def gemini(request: httpx.Request):
        models.append(request.url.path.split("/models/")[1].split(":")[0])
        return _gemini_reply({"metric": "amount", "agg": "sum", "group_by": "store"})
    ai_assistant.HTTP_TRANSPORT = httpx.MockTransport(gemini)
    h = _h(_signup("ana@example.com")["access_token"])
    assert _ask(h).json()["answered"]
    assert models == ["gemini-2.5-flash"]
