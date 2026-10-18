"""
Offers on a FIRST package: a free trial, or a seasonal sale that comes back every year.

WHAT THIS FILE DOES
-------------------
  FREE TRIALS   dim.plan_prices.trial_days (set per tool in Admin -> Prices;
                0 = none). A first package with a trial tool starts with the
                longest trial among the tools picked; Stripe charges nothing
                until it ends (Checkout's trial_period_days). ONE trial per
                account (dim.accounts.trial_used_at), and only for someone who
                has never had a package, so nobody cancels and starts again.
                Testers and admins don't get one (they're free anyway).

  SEASONAL SALES  dim.seasonal_sales: calendar windows ("11-24" to "12-31")
                that repeat every year by themselves, each with a percent off
                the FIRST payment (the first month, or the first year on
                annual billing). Switch them on once in Admin -> Seasonal sales
                and they come back each year with no edits. The Stripe coupon
                is made by the API the first time it's needed
                (stripe_catalog.ensure_coupon), so you never set up a sale in Stripe.

  ONE OFFER AT A TIME: a trial when they qualify for one, otherwise the sale.
  (A "first payment" discount on a trial would land on the $0 trial invoice and
  be wasted.) Promotion codes typed at checkout still work when no sale is on.

  current_sale(today)        the switched-on sale whose window includes today (or None)
  offer_for(account, plans)  {"trial_days", "trial_eligible", "sale", "applies"}
  list_sales() / set_sale()  Admin -> Seasonal sales
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from fastapi import HTTPException, status

from api.core import database
from api.services import account_store, pricing

# The starting rows (also seeded by db/schema.sql; tests compare them). Switched OFF.
# Why these three (US small businesses): January is when budgets and plans for
# the year are made; mid-April to mid-May comes after tax season (the books are
# fresh, and the SBA's National Small Business Week is in early May); late
# November to December is year-end buying (Black Friday / Small Business
# Saturday, and owners buy tools before Dec 31 to deduct them this tax year).
DEFAULT_SALES: tuple[dict, ...] = (
    {"id": "new_year", "name": "New Year planning sale", "starts_on": "01-01", "ends_on": "01-31", "percent_off": 20},
    {"id": "after_taxes", "name": "After-tax-season sale", "starts_on": "04-16", "ends_on": "05-15", "percent_off": 20},
    {"id": "year_end", "name": "Year-end sale", "starts_on": "11-24", "ends_on": "12-31", "percent_off": 25},
)

# In-memory fallback (development only): sale id -> row.
_sales: dict[str, dict] = {}


def _memory_sales() -> dict[str, dict]:
    """The memory store's sales, seeded with DEFAULT_SALES (switched off) the first time."""
    if not _sales:
        for s in DEFAULT_SALES:
            _sales[s["id"]] = {**s, "active": False, "updated_at": None, "updated_by": None}
    return _sales


def check_month_day(value: str) -> str:
    """
    Check a calendar day written "MM-DD" (Feb 29 allowed); return it.

    Args:
        value: str, e.g. "11-24".

    Returns:
        str: the same text.

    Raises:
        HTTPException: 400 when it isn't a real month and day.

    Example:
        >>> check_month_day("12-31")
        '12-31'
    """
    try:
        month, day = (int(x) for x in value.split("-"))
        date(2024, month, day)   # 2024 is a leap year, so 02-29 is accepted
    except (ValueError, TypeError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"{value!r} isn't a date like 11-24 (MM-DD).") from None
    if len(value) != 5:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Write dates as MM-DD, e.g. 01-05.")
    return value


def in_window(today: date, starts_on: str, ends_on: str) -> bool:
    """
    Tell whether a calendar window (MM-DD to MM-DD, both days included) includes today, in any year.

    "MM-DD" text sorts like the calendar, so plain text comparison works. A
    window that wraps the new year ("12-15" to "01-05") is today >= start OR
    today <= end.

    Args:
        today: date, the day to check.
        starts_on: str, "MM-DD".
        ends_on: str, "MM-DD".

    Returns:
        bool: True inside the window.

    Example:
        >>> in_window(date(2027, 1, 3), "12-15", "01-05")
        True
    """
    md = today.strftime("%m-%d")
    if starts_on <= ends_on:
        return starts_on <= md <= ends_on
    return md >= starts_on or md <= ends_on


def _end_date(today: date, sale: dict) -> date:
    """The date this year's (or this season's) window ends: shown as "until 12/31/2026"."""
    month, day = (int(x) for x in sale["ends_on"].split("-"))
    year = today.year + (1 if sale["starts_on"] > sale["ends_on"] and today.strftime("%m-%d") >= sale["starts_on"] else 0)
    try:
        return date(year, month, day)
    except ValueError:   # 02-29 in a year without one
        return date(year, 2, 28)


async def list_sales() -> list[dict]:
    """
    Every seasonal sale (switched on or off), in calendar order.

    Returns:
        list[dict]: {"id", "name", "starts_on", "ends_on", "percent_off", "active",
        "updated_at" (ISO or None), "updated_by" (name or None)}.

    Example:
        >>> [s["id"] for s in await list_sales()]
        ['new_year', 'after_taxes', 'year_end']
    """
    if database.pool is None:
        rows = [dict(r) for r in _memory_sales().values()]
    else:
        try:
            rows = [dict(r) for r in await database.pool.fetch(
                """SELECT s.*, a.first_name || ' ' || a.last_name AS updated_by_name
                   FROM dim.seasonal_sales s LEFT JOIN dim.accounts a ON a.id = s.updated_by""")]
        except Exception:   # table missing until db/schema.sql is re-run: no sales
            return []
    out = []
    for r in sorted(rows, key=lambda r: r["starts_on"]):
        changed = r.get("updated_at")
        out.append({"id": r["id"], "name": r["name"], "starts_on": r["starts_on"], "ends_on": r["ends_on"],
                    "percent_off": int(r["percent_off"]), "active": bool(r["active"]),
                    "updated_at": changed.isoformat() if changed and r.get("updated_by") else None,
                    "updated_by": r.get("updated_by_name") if r.get("updated_by") else None})
    return out


async def set_sale(sale_id: str, *, name: str, starts_on: str, ends_on: str, percent_off: int, active: bool,
                   admin: dict) -> list[dict]:
    """
    Change one seasonal sale (Admin -> Seasonal sales). It repeats every year as written.

    Args:
        sale_id: str, e.g. "year_end".
        name: str, shown to clients ("Year-end sale").
        starts_on: str, "MM-DD".
        ends_on: str, "MM-DD" (included; may be before starts_on to wrap the new year).
        percent_off: int, 1 to 90, off the first payment.
        active: bool, switched on.
        admin: dict, the admin's accounts row.

    Returns:
        list[dict]: list_sales() after the change.

    Raises:
        HTTPException: 404 unknown sale; 400 bad dates, percent or name.

    Example:
        >>> await set_sale("year_end", name="Year-end sale", starts_on="11-24", ends_on="12-31",
        ...                percent_off=30, active=True, admin=me)
    """
    name = name.strip()
    if not 1 <= len(name) <= 60:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Give the sale a name (60 characters at most).")
    if not 1 <= percent_off <= 90:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The discount must be between 1% and 90%.")
    starts_on, ends_on = check_month_day(starts_on), check_month_day(ends_on)
    if database.pool is None:
        row = _memory_sales().get(sale_id)
        if row is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="There's no sale with that id.")
        row.update(name=name, starts_on=starts_on, ends_on=ends_on, percent_off=percent_off, active=active,
                   updated_at=datetime.now(timezone.utc), updated_by=str(admin["id"]),
                   updated_by_name=f"{admin['first_name']} {admin['last_name']}")
        return await list_sales()
    done = await database.pool.execute(
        """UPDATE dim.seasonal_sales SET name = $2, starts_on = $3, ends_on = $4, percent_off = $5, active = $6,
                  updated_at = now(), updated_by = $7
           WHERE id = $1""",
        sale_id, name, starts_on, ends_on, percent_off, active, uuid.UUID(str(admin["id"])))
    if done.endswith(" 0"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="There's no sale with that id.")
    return await list_sales()


async def current_sale(today: date | None = None) -> dict | None:
    """
    The switched-on sale running today (the biggest discount if two overlap), or None.

    Args:
        today: date | None, the day to check (default: today, UTC).

    Returns:
        dict | None: {"id", "name", "percent_off", "ends_on" ("MM-DD"), "ends" (ISO date of this window's last day)}.

    Example:
        >>> await current_sale(date(2026, 12, 1))   # year_end switched on
        {'id': 'year_end', 'name': 'Year-end sale', 'percent_off': 25, 'ends_on': '12-31', 'ends': '2026-12-31'}
    """
    today = today or datetime.now(timezone.utc).date()
    running = [s for s in await list_sales() if s["active"] and in_window(today, s["starts_on"], s["ends_on"])]
    if not running:
        return None
    best = max(running, key=lambda s: s["percent_off"])
    return {"id": best["id"], "name": best["name"], "percent_off": best["percent_off"], "ends_on": best["ends_on"],
            "ends": _end_date(today, best).isoformat()}


def trial_days_for(plan_ids: list[str] | None = None) -> int:
    """
    The trial a first package gets: the longest trial among the tools picked (all tools when None).

    Args:
        plan_ids: list[str] | None, Subscription ids picked (None = any plan, for "up to N days").

    Returns:
        int: days (0 = no trial).

    Example:
        >>> trial_days_for(["data_cleaning", "data_visualization"])   # Dashboards has 14
        14
    """
    ids = plan_ids if plan_ids is not None else list(pricing.SUBSCRIPTIONS_BY_ID)
    return max((pricing.TRIAL_DAYS.get(p, 0) for p in ids if pricing.billable(p)), default=0)


async def trial_eligible(account: dict) -> bool:
    """
    Tell whether this plan holder may still have a free trial.

    No: a tester or admin (free already), anyone who had a trial
    (trial_used_at), or anyone who ever had a package (cancel-and-restart).

    Args:
        account: dict, the plan holder's accounts row.

    Returns:
        bool: True when a first package may start with a trial.
    """
    from api.services.entitlements import is_admin   # (entitlements imports billing, which imports this)

    if account.get("is_tester") or is_admin(account) or account.get("trial_used_at"):
        return False
    return not await account_store.list_subscriptions(str(account["id"]), live_only=False)


async def offer_for(account: dict, plan_ids: list[str] | None = None, today: date | None = None) -> dict:
    """
    What a FIRST package gets right now: a trial, else the running sale, else nothing.

    Args:
        account: dict, the plan holder's accounts row.
        plan_ids: list[str] | None, the tools picked (None = "any", for the billing page).
        today: date | None, for tests (default today).

    Returns:
        dict: {"trial_days": int (0 when not eligible), "trial_eligible": bool,
        "sale": current_sale() or None, "applies": "trial" | "sale" | None}.

    Example:
        >>> await offer_for(new_client, ["all_tools"])
        {'trial_days': 14, 'trial_eligible': True, 'sale': None, 'applies': 'trial'}
    """
    eligible = await trial_eligible(account)
    days = trial_days_for(plan_ids) if eligible else 0
    sale = await current_sale(today)
    from api.services.entitlements import is_admin

    if account.get("is_tester") or is_admin(account):
        sale = None   # nothing to discount: they don't pay
    applies = "trial" if days else ("sale" if sale else None)
    return {"trial_days": days, "trial_eligible": eligible, "sale": sale, "applies": applies}
