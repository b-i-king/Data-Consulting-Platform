"""
Shared test setup (pytest loads this file before every test module).

Two-step sign-in is REQUIRED for admins and consultants in the real app
(MFA_REQUIRED_FOR_STAFF, api/services/mfa.py). Most tests sign in as admins
and consultants without an authenticator app, so it's off by default here;
tests/test_security.py switches it on to check the rule itself.
"""

import os

os.environ.setdefault("MFA_REQUIRED_FOR_STAFF", "false")
# Every test request comes from the same "IP" (TestClient), so the per-IP limits
# (api/core/rate_limit.py) are off here; tests/test_security.py switches them on.
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "0")
os.environ.setdefault("AUTH_RATE_LIMIT_PER_MINUTE", "0")

# Tests must NEVER reach real services with the keys in your .env file (Stripe,
# Supabase, Gemini, Sentry): these environment values win over .env, so every
# test starts offline. A test that needs one sets it itself (monkeypatch.setenv),
# always with a fake value and a fake client.
for _key in ("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET", "DATABASE_URL", "ADMIN_DATABASE_URL", "SUPABASE_URL",
             "SUPABASE_KEY", "GEMINI_API_KEY", "SENTRY_DSN"):
    os.environ[_key] = ""


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_skill_previews():
    """
    Every test starts with no paid-tool previews used (api/services/skill_access.py).

    Every TestClient request comes from the same "IP", so without this one test's
    preview would count against the next one's.
    """
    from api.services import skill_access

    skill_access._previews.clear()
    yield
    skill_access._previews.clear()



@pytest.fixture(autouse=True)
def sales_open():
    """
    Purchases are OPEN in tests (api/services/sales.py defaults to closed for the beta),
    so checkout tests test checkout; tests/test_sales_switch.py closes them on purpose.
    """
    from api.services import account_store, sales

    account_store._settings["sales_open"] = True
    sales.forget()
    yield
    account_store._settings.pop("sales_open", None)
    sales.forget()
