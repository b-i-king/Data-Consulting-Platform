"""
sales — the "sales open / closed" switch: can customers buy anything yet?

During the beta the owner sets Stripe up completely (products, prices,
webhooks, test purchases) but keeps PURCHASES CLOSED until the hosting is
upgraded (Render paid, Vercel Pro). While closed:

  * customers can't start a checkout for tools / bundles (billing.start_checkout,
    which also handles package changes) or for consultant setup / ongoing help
    (consulting_billing.start_checkout) — they get 403 with SALES_CLOSED;
  * ADMINS and TESTERS (dim.accounts.is_tester, the plan holder's flag) can still
    check out, so the whole flow can be tried end to end;
  * free things keep working: guest cleaning, free AI questions, free consultations.

It also closes a gap: with no STRIPE_SECRET_KEY, checkout runs in test mode and
switches plans on for FREE — while sales are closed, a customer can't do that.

The switch lives in dim.app_settings (key "sales_open") and is flipped in
Settings -> Administration (no redeploy). Until it has ever been set, the
SALES_OPEN environment variable decides (default: closed). The value is read
from the database at most every CACHE_SECONDS.
"""

from __future__ import annotations

import time

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store, entitlements

KEY = "sales_open"
CACHE_SECONDS = 30
SALES_CLOSED = ("Paid plans open soon — we're in beta. Everything free keeps working; we'll let you know as soon "
                "as plans are available.")

_cache: dict = {"value": None, "at": 0.0}   # the last value read, and when


async def is_open() -> bool:
    """
    True when customers may buy (the saved switch, else SALES_OPEN; default closed).

    Example:
        >>> await is_open()      # a fresh install, SALES_OPEN not set
        False
    """
    now = time.monotonic()
    if _cache["value"] is None or now - _cache["at"] > CACHE_SECONDS:
        saved = await account_store.get_setting(KEY)
        _cache.update(value=bool(saved) if saved is not None else get_settings().sales_open, at=now)
    return bool(_cache["value"])


async def set_open(value: bool, admin_id: str | None) -> dict:
    """Flip the switch (admins only — checked by the router); takes effect at once."""
    await account_store.set_setting(KEY, bool(value), admin_id)
    _cache.update(value=bool(value), at=time.monotonic())
    return {"open": bool(value)}


async def can_buy(account: dict | None) -> bool:
    """Sales open, or the person is an admin or (their plan holder is) a tester."""
    if await is_open():
        return True
    if not account:
        return False
    return entitlements.is_admin(account) or bool((await entitlements.plan_holder(account)).get("is_tester"))


async def require_open(account: dict) -> None:
    """
    Stop a checkout while sales are closed (admins and testers pass).

    Raises:
        HTTPException: 403 with SALES_CLOSED.
    """
    if not await can_buy(account):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=SALES_CLOSED)


def forget() -> None:
    """Drop the cached value (tests, or right after a change on another server)."""
    _cache.update(value=None, at=0.0)
