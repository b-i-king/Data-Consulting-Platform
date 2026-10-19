"""
Live tool prices: THE one place to change what a subscription costs.

WHAT THIS FILE DOES
-------------------
The tools on sale (names, what they unlock, which need which) are defined in
api/services/pricing.py. Their PRICES live in the database table
dim.plan_prices (db/schema.sql), so you can change them without touching code:

  load()                       at API startup: read dim.plan_prices and swap
                               the prices into pricing.SUBSCRIPTIONS
                               (pricing.apply_prices), so every page, quote and
                               checkout uses them
  list_prices()                every plan with its live and default prices
                               (the Admin panel's "Prices" card)
  set_price(plan, m, a, admin) change one plan's monthly and annual price (and
                               its free-trial days): Stripe first (when it's on),
                               then the table, then the running site
  consulting_prices()          a consultant's help: setup fee, hourly rate, hours
                               rule, platform fee (dim.consulting_prices, ONE row)
  set_consulting_prices(v, a)  change them. No Stripe step: each consultant
                               checkout sends Stripe the price it works out
                               (api/services/consulting_billing.py)

WHY STRIPE FIRST
----------------
Checkout charges whatever Stripe has under the plan's lookup key
(dcp_<plan>_month / _year). If the site showed $24 while Stripe still had $19,
clients would be charged a price they never saw. So Stripe is updated first
(api/services/stripe_catalog.py: a new Stripe price, the key moved onto it, the
old one archived) and the table is saved only when that worked. Subscribers
keep the price they signed up at; new checkouts use the new one.

Without a database (local development), prices are kept in memory and reset
on restart, like the rest of the memory store.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import replace
from datetime import datetime, timezone

from fastapi import HTTPException, status

from api.core import database
from api.services import billing, pricing, stripe_catalog

log = logging.getLogger(__name__)

# In-memory fallback (development only): plan_id -> {"monthly_usd", "annual_usd", "updated_at", "updated_by"}.
_prices: dict[str, dict] = {}
_consulting: dict = {}            # dim.consulting_prices (memory path)

MIN_PRICE_USD = 0.50          # Stripe's smallest card charge in US dollars
MAX_MONTHLY_USD = 10_000.0    # a typo guard ("1900" for "19.00"), not a business rule
MAX_ANNUAL_USD = 100_000.0


async def load() -> int:
    """
    Read dim.plan_prices and make those the prices everywhere on the site.

    Called once at startup (api/main.py) and by scripts/sync_stripe.py. A plan
    without a row keeps its default price from pricing.py. If the table doesn't
    exist yet (db/schema.sql not re-run since this feature), a warning is
    logged and the defaults are used, so the API still starts.

    Returns:
        int: how many plans got a price from the table.

    Example:
        >>> await load()
        7
    """
    if database.pool is None:
        rows = {pid: (r["monthly_usd"], r["annual_usd"]) for pid, r in _prices.items()}
        trials = {pid: int(r.get("trial_days") or 0) for pid, r in _prices.items()}
    else:
        try:
            found = await database.pool.fetch("SELECT plan_id, monthly_usd, annual_usd, trial_days FROM dim.plan_prices")
        except Exception as exc:   # e.g. asyncpg UndefinedTableError before the schema is updated
            log.warning("Couldn't read dim.plan_prices (%s) — using the default prices in pricing.py. "
                        "Run python scripts/setup_database.py to add the table.", exc)
            return 0
        rows = {r["plan_id"]: (float(r["monthly_usd"]), float(r["annual_usd"])) for r in found}
        trials = {r["plan_id"]: int(r["trial_days"] or 0) for r in found}
    await _load_consulting()
    pricing.apply_prices({pid: p for pid, p in rows.items() if pid in pricing.SUBSCRIPTIONS_BY_ID})
    pricing.TRIAL_DAYS.clear()
    pricing.TRIAL_DAYS.update({pid: d for pid, d in trials.items() if d})
    return len(rows)


async def _stored() -> dict[str, dict]:
    """
    The saved price rows, by plan id ({"updated_at", "updated_by", ...}); {} if none.

    Returns:
        dict[str, dict]: plan_id -> row.
    """
    if database.pool is None:
        return dict(_prices)
    try:
        rows = await database.pool.fetch(
            """SELECT p.plan_id, p.monthly_usd, p.annual_usd, p.updated_at, p.updated_by,
                      a.first_name || ' ' || a.last_name AS updated_by_name
               FROM dim.plan_prices p LEFT JOIN dim.accounts a ON a.id = p.updated_by""")
    except Exception:   # table missing: nothing saved yet
        return {}
    return {r["plan_id"]: dict(r) for r in rows}


async def list_prices() -> list[dict]:
    """
    Every plan with its live price, its default, and who changed it last (Admin -> Prices).

    Returns:
        list[dict]: one per plan, in site order (bundle first): {"id", "name",
        "kind", "monthly_usd", "annual_usd", "default_monthly_usd",
        "default_annual_usd", "trial_days", "billable", "updated_at", "updated_by"}.
        updated_at / updated_by are None for a price never changed here.

    Example:
        >>> [(p["id"], p["monthly_usd"]) for p in await list_prices()][:2]
        [('all_tools', 99.0), ('data_cleaning', 19.0)]
    """
    saved = await _stored()
    out = []
    for s in pricing.SUBSCRIPTIONS:
        row = saved.get(s.id) or {}
        changed = row.get("updated_at")
        out.append({
            "id": s.id, "name": s.name, "kind": s.kind,
            "monthly_usd": s.monthly_usd, "annual_usd": s.annual_usd,
            "default_monthly_usd": pricing.DEFAULT_PRICES[s.id][0],
            "default_annual_usd": pricing.DEFAULT_PRICES[s.id][1],
            "trial_days": pricing.TRIAL_DAYS.get(s.id, 0),   # free trial on a first package (0 = none)
            "billable": pricing.billable(s.id),   # False = not charged yet (AI before launch)
            # NULL updated_by = the starting price from db/schema.sql, never changed.
            "updated_at": changed.isoformat() if changed and row.get("updated_by") else None,
            "updated_by": row.get("updated_by_name") if row.get("updated_by") else None,
        })
    return out


def check_price(plan_id: str, monthly_usd: float, annual_usd: float) -> tuple[float, float]:
    """
    Check a new price before anything is changed; return it rounded to cents.

    Rules: the plan must exist; each price is at least $0.50 (Stripe's minimum
    charge) and at most a typo guard; the annual price can't be more than 12
    months at the monthly price (annual must never cost more than monthly).

    Args:
        plan_id: str, a pricing.SUBSCRIPTIONS id.
        monthly_usd: float, the monthly price in US dollars.
        annual_usd: float, the yearly price in US dollars.

    Returns:
        tuple[float, float]: (monthly, annual) rounded to cents.

    Raises:
        HTTPException: 404 for an unknown plan; 400 for a price outside the rules.

    Example:
        >>> check_price("data_visualization", 24, 240.004)
        (24.0, 240.0)
    """
    if plan_id not in pricing.SUBSCRIPTIONS_BY_ID:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="There's no plan with that id.")
    monthly, annual = round(float(monthly_usd), 2), round(float(annual_usd), 2)
    if monthly < MIN_PRICE_USD or annual < MIN_PRICE_USD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Each price must be at least $0.50 (Stripe's minimum).")
    if monthly > MAX_MONTHLY_USD or annual > MAX_ANNUAL_USD:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="That price looks too high — check the decimal point ($10,000 a month at most).")
    if annual > round(monthly * 12, 2):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"The annual price can't be more than 12 months at ${monthly:,.2f} "
                                   f"(${monthly * 12:,.2f}).")
    return monthly, annual


async def set_price(plan_id: str, monthly_usd: float, annual_usd: float, admin: dict,
                    trial_days: int | None = None) -> dict:
    """
    Change one plan's prices (and trial): Stripe (if on), then dim.plan_prices, then the running site.

    A trial needs nothing in Stripe ahead of time: checkout asks Stripe for it
    (api/services/offers.py).

    Args:
        plan_id: str, a pricing.SUBSCRIPTIONS id, e.g. "data_visualization".
        monthly_usd: float, new monthly price in US dollars.
        annual_usd: float, new annual price in US dollars.
        admin: dict, the admin's accounts row (saved as updated_by).
        trial_days: int | None, free-trial days for a first package (0-90; None = keep).

    Returns:
        dict: {"prices": list_prices(), "stripe": {"enabled": bool, "changes": [str, ...]}}
        — "changes" lists what was done in Stripe ("replace price dcp_..._month: $24.00 per month").

    Raises:
        HTTPException: 404 / 400 from check_price; 502 when Stripe refused the
            change (nothing is saved, so the site and Stripe still agree).

    Example:
        >>> (await set_price("data_visualization", 24, 240, admin))["stripe"]["changes"]
        ['replace price dcp_data_visualization_month: $24.00 per month', ...]
    """
    monthly, annual = check_price(plan_id, monthly_usd, annual_usd)
    trial = pricing.TRIAL_DAYS.get(plan_id, 0) if trial_days is None else int(trial_days)
    if not 0 <= trial <= 90:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="A free trial can be 0 to 90 days.")
    changes: list[str] = []
    if billing.stripe_enabled():
        plan = replace(pricing.SUBSCRIPTIONS_BY_ID[plan_id], monthly_usd=monthly, annual_usd=annual)
        try:
            stripe_catalog.sync_plan(billing._stripe(), plan, log=changes.append)
        except Exception as exc:   # network error, bad key, Stripe rule — keep the old price everywhere
            log.exception("Stripe price update failed for %s", plan_id)
            raise HTTPException(status.HTTP_502_BAD_GATEWAY,
                                detail=f"Stripe didn't accept the new price, so nothing changed: {exc}") from exc
    if database.pool is None:
        _prices[plan_id] = {"plan_id": plan_id, "monthly_usd": monthly, "annual_usd": annual, "trial_days": trial,
                            "updated_at": datetime.now(timezone.utc), "updated_by": str(admin["id"]),
                            "updated_by_name": f"{admin['first_name']} {admin['last_name']}"}
    else:
        await database.pool.execute(
            """INSERT INTO dim.plan_prices (plan_id, monthly_usd, annual_usd, trial_days, updated_at, updated_by)
               VALUES ($1, $2, $3, $5, now(), $4)
               ON CONFLICT (plan_id) DO UPDATE SET monthly_usd = EXCLUDED.monthly_usd,
                   annual_usd = EXCLUDED.annual_usd, trial_days = EXCLUDED.trial_days,
                   updated_at = now(), updated_by = EXCLUDED.updated_by""",
            plan_id, monthly, annual, uuid.UUID(str(admin["id"])), trial)
    pricing.apply_prices({plan_id: (monthly, annual)})
    if trial:
        pricing.TRIAL_DAYS[plan_id] = trial
    else:
        pricing.TRIAL_DAYS.pop(plan_id, None)
    return {"prices": await list_prices(), "stripe": {"enabled": billing.stripe_enabled(), "changes": changes}}


# ── A consultant's help (dim.consulting_prices) ───────────────────────────────

async def _load_consulting() -> None:
    """Read dim.consulting_prices into pricing.CONSULTING (defaults if the table isn't there yet)."""
    if database.pool is None:
        pricing.apply_consulting_prices(_consulting)
        return
    try:
        row = await database.pool.fetchrow("SELECT * FROM dim.consulting_prices WHERE id")
    except Exception as exc:   # table missing before the schema is re-run
        log.warning("Couldn't read dim.consulting_prices (%s) — using pricing.CONSULTING_DEFAULTS.", exc)
        return
    if row:
        pricing.apply_consulting_prices(dict(row))


async def consulting_prices() -> dict:
    """
    The live consultant-help numbers, with the defaults and when they were last changed.

    Returns:
        dict: {"values": pricing.CONSULTING, "defaults": pricing.CONSULTING_DEFAULTS,
        "updated_at": ISO text | None, "example": {"hours", "monthly_usd"} for Data
        Cleaning + Data Engineering + one more service}.
    """
    await _load_consulting()
    updated = None
    if database.pool is None:
        updated = _consulting.get("updated_at")
    else:
        try:
            row = await database.pool.fetchrow("SELECT updated_at FROM dim.consulting_prices WHERE id")
            updated = row["updated_at"] if row else None
        except Exception:
            updated = None
    hours = pricing.help_hours(["data_cleaning", "data_engineering", "data_visualization"])
    return {"values": dict(pricing.CONSULTING), "defaults": dict(pricing.CONSULTING_DEFAULTS),
            "updated_at": updated.isoformat() if updated else None,
            "example": {"hours": hours, "monthly_usd": pricing.retainer_price_usd(hours, "month")}}


async def set_consulting_prices(values: dict, admin: dict) -> dict:
    """
    Change what a consultant's help costs (Admin -> Prices -> "Consultant help").

    Takes effect for NEW purchases at once (each checkout sends Stripe its own
    price); running ongoing help keeps the price it started at.

    Args:
        values: dict, the CONSULTING_DEFAULTS keys to change.
        admin: dict, the admin's accounts row.

    Returns:
        dict: consulting_prices().

    Raises:
        HTTPException: 400 when max_hours is below core_hours.
    """
    merged = {**pricing.CONSULTING, **{k: float(v) for k, v in values.items() if v is not None}}
    if merged["max_hours"] < merged["core_hours"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="The weekly maximum can't be less than the Data Cleaning / Engineering hours.")
    keys = list(pricing.CONSULTING_DEFAULTS)
    if database.pool is None:
        _consulting.update({k: merged[k] for k in keys}, updated_at=datetime.now(timezone.utc),
                           updated_by=str(admin["id"]))
    else:
        await database.pool.execute(
            f"""INSERT INTO dim.consulting_prices (id, {", ".join(keys)}, updated_at, updated_by)
                VALUES (true, {", ".join(f"${i + 1}" for i in range(len(keys)))}, now(), ${len(keys) + 1})
                ON CONFLICT (id) DO UPDATE SET {", ".join(f"{k} = EXCLUDED.{k}" for k in keys)},
                    updated_at = now(), updated_by = EXCLUDED.updated_by""",
            *[merged[k] for k in keys], uuid.UUID(str(admin["id"])))
    pricing.apply_consulting_prices(merged)
    return await consulting_prices()
