"""
Tests for the home page's Partners carousel (api/services/partners.py, api/routers/partners.py).

Covers: the requirements shown up front, who may apply (business, paid 30+
days), the own-domain rule, the logo turned into a small WebP (SVG and junk
refused), admin approval with a required reason to turn down, and that a
partner is shown only while its plan is paid.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from api.core.config import get_settings
from api.main import app
from api.services import account_store, partners

client = TestClient(app)
PW = "Correct-horse-9"


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """One admin (boss@example.com), Stripe off, empty stores, and no real website check."""
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    for key in ("SUPABASE_URL", "SUPABASE_KEY", "DATABASE_URL"):
        monkeypatch.setenv(key, "")
    get_settings.cache_clear()

    async def reachable(_url):
        return True

    monkeypatch.setattr(partners, "check_website", reachable)
    for store in (account_store._accounts, account_store._teams, account_store._subscriptions,
                  account_store._partners):
        store.clear()
    yield
    get_settings.cache_clear()


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _business(email="ana@sunrisebakery.com", company="Sunrise Bakery"):
    res = client.post("/api/accounts/signup", json={
        "account_type": "business", "first_name": "Ana", "last_name": "Lee", "email": email,
        "company_name": company, "password": PW, "accept_terms": True})
    assert res.status_code == 201, res.text
    return res.json()["access_token"], res.json()["account"]["id"]


def _pay(account_id: str, days_ago: int) -> dict:
    """A live Starter subscription that started `days_ago` days ago (memory store)."""
    import asyncio
    sub = asyncio.run(account_store.upsert_subscription(account_id=account_id, billing_interval="month",
                                                        status="active", items=[{"plan_id": "starter"}]))
    sub["created_at"] = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return sub


def _png(width=600, height=300, color=(200, 172, 68, 255)) -> bytes:
    """A gold logo on a transparent background (a margin all round), as PNG bytes."""
    out = io.BytesIO()
    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    img.paste(Image.new("RGBA", (max(1, width - 20), max(1, height - 20)), color), (10, 10))
    img.save(out, "PNG")
    return out.getvalue()


def _apply(token, website="https://www.sunrisebakery.com", logo=None, agreed=True, linkedin=""):
    return client.post("/api/partners/me", headers=_h(token),
                       data={"website_url": website, "agreed": str(agreed).lower(), "linkedin_url": linkedin},
                       files={"logo": ("logo.png", logo if logo is not None else _png(), "image/png")})


def test_requirements_are_shown_and_only_businesses_paid_30_days_may_apply():
    """The rules come first; a new plan must wait 30 days; an individual can't apply."""
    token, aid = _business()
    me = client.get("/api/partners/me", headers=_h(token)).json()
    assert len(me["requirements"]) == 5 and not me["eligible"] and me["application"] is None
    assert any("paid plan" in m for m in me["missing"])
    _pay(aid, days_ago=10)
    me = client.get("/api/partners/me", headers=_h(token)).json()
    assert not me["eligible"] and "20 more" in " ".join(me["missing"])
    assert _apply(token).status_code == 403
    _pay(aid, days_ago=31)
    assert client.get("/api/partners/me", headers=_h(token)).json()["eligible"]

    person = client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "Bo", "last_name": "Ko", "email": "bo@example.com",
        "password": PW, "accept_terms": True}).json()
    _pay(person["account"]["id"], days_ago=60)
    assert any("business account" in m for m in
               client.get("/api/partners/me", headers=_h(person["access_token"])).json()["missing"])


def _logo_on(background, size=(1200, 600)) -> bytes:
    """A gold "logo" (a ring with a hole the same color as the background) on a background color (None = transparent)."""
    img = Image.new("RGBA", size, background or (0, 0, 0, 0))
    w, h = size
    for x in range(w // 4, 3 * w // 4):
        for y in range(h // 4, 3 * h // 4):
            inner = w * 3 // 8 <= x < w * 5 // 8 and h * 3 // 8 <= y < h * 5 // 8
            img.putpixel((x, y), (background or (255, 255, 255, 255)) if inner else (200, 172, 68, 255))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def test_the_logo_becomes_a_small_transparent_webp_and_bad_files_are_refused():
    """Only the logo is kept: transparent stays, a plain background is removed (holes too), margins trimmed."""
    small = (400, 200)
    webp, w, h, how = partners.process_logo(_logo_on(None, small))
    assert how == "transparent" and (w, h) == (200, 100)          # trimmed to the logo, then fits 320 x 160
    assert webp[:4] == b"RIFF" and webp[8:12] == b"WEBP" and len(webp) < 20_000

    webp, w, h, how = partners.process_logo(_logo_on((255, 255, 255, 255), small))
    assert how == "removed" and (w, h) == (200, 100)
    img = Image.open(io.BytesIO(webp)).convert("RGBA")
    corner = img.getpixel((2, 2))                                  # the corner is now the logo itself (trimmed)
    assert corner[3] == 255 and all(abs(c - g) <= 4 for c, g in zip(corner[:3], (200, 172, 68)))   # WebP: ~exact
    assert img.getpixel((w // 2, h // 2))[3] == 0                  # the hole inside the ring is see-through too

    noisy = Image.effect_noise((400, 200), 80).convert("RGBA")     # a busy background: refused, not guessed
    out = io.BytesIO()
    noisy.save(out, "PNG")
    with pytest.raises(Exception) as exc:
        partners.process_logo(out.getvalue())
    assert exc.value.status_code == 400 and "transparent background" in exc.value.detail

    for bad in (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>", b"not an image"):
        with pytest.raises(Exception) as exc:
            partners.process_logo(bad)
        assert exc.value.status_code == 400
    with pytest.raises(Exception) as exc:
        partners.process_logo(_png(40, 20))
    assert "too small" in exc.value.detail


def test_apply_needs_agreement_own_domain_and_a_company_linkedin():
    token, aid = _business()
    _pay(aid, days_ago=40)
    assert _apply(token, agreed=False).status_code == 400
    assert "own website domain" in _apply(token, website="sunrise.wixsite.com/bakery").json()["detail"]
    assert "company page" in _apply(token, linkedin="https://www.linkedin.com/in/ana").json()["detail"]
    ok = _apply(token, website="sunrisebakery.com", linkedin="https://www.linkedin.com/company/sunrise-bakery")
    assert ok.status_code == 200, ok.text
    app_ = ok.json()["application"]
    assert app_["status"] == "pending" and app_["website_url"] == "https://sunrisebakery.com"
    assert app_["checks"]["email_matches_website"] and app_["checks"]["own_domain"] and app_["checks"]["linkedin_given"]
    # The business sees its own logo while it waits; the public doesn't.
    assert client.get(app_["logo_url"], headers=_h(token)).headers["content-type"] == "image/webp"
    assert client.get(app_["logo_url"]).status_code == 404


def test_admin_approves_and_the_carousel_shows_it_only_while_paid():
    token, aid = _business()
    sub = _pay(aid, days_ago=40)
    assert _apply(token).status_code == 200
    assert client.get("/api/partners").json()["partners"] == []          # pending: not shown

    admin = client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "Boss", "last_name": "Lee", "email": "boss@example.com",
        "password": PW, "accept_terms": True}).json()["access_token"]
    assert client.get("/api/partners/admin/list", headers=_h(token)).status_code == 403    # not an admin
    listed = client.get("/api/partners/admin/list?status_filter=pending", headers=_h(admin)).json()["partners"]
    assert [p["business_name"] for p in listed] == ["Sunrise Bakery"]
    assert client.get(listed[0]["logo_url"], headers=_h(admin)).status_code == 200          # admin reviews it
    pid = listed[0]["id"]
    assert client.post(f"/api/partners/admin/{pid}", headers=_h(admin), json={"approve": False}).status_code == 400
    assert client.post(f"/api/partners/admin/{pid}", headers=_h(admin), json={"approve": True}).json()["status"] == "approved"

    shown = client.get("/api/partners").json()["partners"]
    assert [(p["name"], p["url"]) for p in shown] == [("Sunrise Bakery", "https://www.sunrisebakery.com")]
    logo = client.get(shown[0]["logo_url"])
    assert logo.status_code == 200 and "max-age=86400" in logo.headers["cache-control"]

    sub["status"] = "canceled"                                          # the plan ends: off the home page
    assert client.get("/api/partners").json()["partners"] == []
    assert client.get(shown[0]["logo_url"]).status_code == 404


def test_turned_down_with_a_reason_and_withdraw():
    token, aid = _business()
    _pay(aid, days_ago=40)
    _apply(token)
    admin = client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "Boss", "last_name": "Lee", "email": "boss@example.com",
        "password": PW, "accept_terms": True}).json()["access_token"]
    pid = client.get("/api/partners/admin/list", headers=_h(admin)).json()["partners"][0]["id"]
    client.post(f"/api/partners/admin/{pid}", headers=_h(admin),
                json={"approve": False, "reason": "Please send the logo without the slogan."})
    me = client.get("/api/partners/me", headers=_h(token)).json()["application"]
    assert me["status"] == "rejected" and "slogan" in me["reason"]
    assert _apply(token).json()["application"]["status"] == "pending"   # resubmitting starts over
    assert client.delete("/api/partners/me", headers=_h(token)).json()["application"]["status"] == "withdrawn"


def test_admin_adds_a_company_they_work_with_directly():
    """No account, no plan: the admin enters the name, website and logo; it's live at once until removed."""
    admin = client.post("/api/accounts/signup", json={
        "account_type": "individual", "first_name": "Boss", "last_name": "Lee", "email": "boss@example.com",
        "password": PW, "accept_terms": True}).json()["access_token"]
    token, _ = _business()
    form = {"business_name": "Corner Cafe", "website_url": "cornercafe.com"}
    files = {"logo": ("logo.png", _png(), "image/png")}
    assert client.post("/api/partners/admin/add", headers=_h(token), data=form, files=files).status_code == 403
    added = client.post("/api/partners/admin/add", headers=_h(admin), data=form, files=files).json()
    assert added["source"] == "admin" and added["status"] == "approved" and added["website_url"] == "https://cornercafe.com"
    shown = client.get("/api/partners").json()["partners"]
    assert [(p["name"], p["id"]) for p in shown] == [("Corner Cafe", added["id"])]   # no paid plan needed
    assert client.get(shown[0]["logo_url"]).status_code == 200
    assert client.post("/api/partners/admin/add", headers=_h(admin), data={**form, "business_name": " "},
                       files=files).status_code == 400
    assert client.delete(f"/api/partners/admin/{added['id']}", headers=_h(admin)).json() == {"removed": added["id"]}
    assert client.get("/api/partners").json()["partners"] == []
    assert client.delete(f"/api/partners/admin/{added['id']}", headers=_h(admin)).status_code == 404
