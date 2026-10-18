"""
Per-IP request limits: one computer can't flood the API or guess passwords at scale.

WHAT THIS FILE DOES
-------------------
Counts each visitor's requests over the last 60 seconds, by IP address:

  * every request:              RATE_LIMIT_PER_MINUTE       (default 300)
  * sign-in / sign-up / password / two-step code endpoints:
                                AUTH_RATE_LIMIT_PER_MINUTE  (default 10)

Past the limit the reply is 429 "Too many requests" with a Retry-After header
(seconds). The per-ACCOUNT lockout (3 wrong passwords, login_guard.py) still
applies on top; this one stops a single machine trying many accounts.
Webhooks (Stripe, Cal.com) and /health are never limited: they come from a
few shared servers.

WHICH IP?
---------
On Render the API sits behind Render's proxy, so the connection always comes
from the proxy; the visitor's address is in the X-Forwarded-For header, which
each proxy appends to. TRUSTED_PROXY_HOPS says how many proxies to trust
(the visitor is that many entries from the RIGHT, so a visitor can't fake it by
sending their own header). On Render it defaults to 1; locally, 0 = the
connection's own address.

Counts live in memory, per server process (enough for one Render instance;
several instances would each count separately). Nothing is stored.
"""

from __future__ import annotations

import os
import time
from collections import deque

from starlette.requests import Request
from starlette.responses import JSONResponse

from api.core.config import get_settings

WINDOW_SECONDS = 60
AUTH_PATHS = ("/api/accounts/signin", "/api/accounts/signup", "/api/accounts/forgot-password",
              "/api/accounts/reset-password")   # /signin/mfa starts with /signin: counted too
EXEMPT_PREFIXES = ("/health", "/api/billing/webhook", "/api/consultations/calcom-webhook")
_MAX_KEYS = 50_000   # forget everyone past this many addresses (memory can't grow without end)

# (bucket, ip) -> request times (seconds) inside the window
_hits: dict[tuple[str, str], deque] = {}


def client_ip(request: Request) -> str:
    """
    The visitor's IP address, trusting TRUSTED_PROXY_HOPS proxies (1 on Render by default).

    Args:
        request: Request, the incoming request.

    Returns:
        str: the IP address ("unknown" if there's none).

    Example:
        X-Forwarded-For "1.2.3.4, 203.0.113.9" with 1 trusted hop -> "203.0.113.9": Render's
        proxy appended the address it saw (the visitor); "1.2.3.4" could be made up.
    """
    hops = get_settings().trusted_proxy_hops or (1 if os.environ.get("RENDER") else 0)
    forwarded = [p.strip() for p in request.headers.get("x-forwarded-for", "").split(",") if p.strip()]
    if hops and forwarded:
        return forwarded[-hops] if len(forwarded) >= hops else forwarded[0]
    return request.client.host if request.client else "unknown"


def _allow(bucket: str, ip: str, limit: int, now: float) -> int:
    """Count one request; return 0 if allowed, else the seconds until the oldest one leaves the window."""
    if len(_hits) > _MAX_KEYS:
        _hits.clear()
    times = _hits.setdefault((bucket, ip), deque())
    while times and times[0] <= now - WINDOW_SECONDS:
        times.popleft()
    if len(times) >= limit:
        return max(1, int(times[0] + WINDOW_SECONDS - now) + 1)
    times.append(now)
    return 0


async def middleware(request: Request, call_next):
    """
    FastAPI/Starlette HTTP middleware: refuse a request past the visitor's per-minute limit.

    Args:
        request: Request, the incoming request.
        call_next: the rest of the app.

    Returns:
        Response: the app's reply, or 429 with Retry-After.
    """
    path = request.url.path
    if request.method == "OPTIONS" or path.startswith(EXEMPT_PREFIXES):
        return await call_next(request)
    s = get_settings()
    ip, now = client_ip(request), time.monotonic()
    checks = [("all", s.rate_limit_per_minute)]
    if path.startswith(AUTH_PATHS):
        checks.append(("auth", s.auth_rate_limit_per_minute))
    for bucket, limit in checks:
        if limit and limit > 0:
            wait = _allow(bucket, ip, limit, now)
            if wait:
                message = ("Too many sign-in attempts from this network. Please wait a minute and try again."
                           if bucket == "auth" else "Too many requests. Please slow down and try again in a minute.")
                return JSONResponse({"detail": message}, status_code=429, headers={"Retry-After": str(wait)})
    return await call_next(request)
