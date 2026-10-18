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
