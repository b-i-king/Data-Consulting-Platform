"""
Create (or update) the Stripe products and prices from api/services/pricing.py.

WHAT IT DOES
------------
For every plan in SUBSCRIPTIONS it makes sure Stripe has:
    one PRODUCT  (e.g. "All tools")
    two PRICES   monthly and annual, found later by their "lookup key":
                 dcp_all_tools_month, dcp_all_tools_year
plus one price for prepaid consulting:  dcp_consulting_hour  (CONSULTING_RATE_PER_HOUR_USD)

Stripe prices can't be edited once created. When you change a price in
pricing.py, this script creates a NEW price, moves the lookup key to it
(`transfer_lookup_key`), and archives the old one — existing subscribers keep
their old price until you migrate them in the Stripe dashboard.

With DATABASE_URL set, it also writes the Stripe ids into the
`subscription_plans` table (db/schema.sql).

RUN IT
------
    STRIPE_SECRET_KEY=sk_test_... python scripts/sync_stripe.py            # test mode first
    STRIPE_SECRET_KEY=sk_test_... python scripts/sync_stripe.py --dry-run  # show, change nothing
Safe to run again: it only changes what differs.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.core import database  # noqa: E402
from api.core.config import get_settings  # noqa: E402
from api.services import account_store, pricing  # noqa: E402


def _cents(usd: float) -> int:
    return int(round(usd * 100))


def ensure_product(stripe, key: str, name: str, description: str, dry: bool, log) -> str | None:
    """Find the product by our metadata key (dcp_key), or create it. Returns its id."""
    found = stripe.Product.search(query=f"metadata['dcp_key']:'{key}'", limit=1).data
    if found:
        product = found[0]
        if product.name != name or (product.description or "") != description:
            log(f"update product {key}")
            if not dry:
                stripe.Product.modify(product.id, name=name, description=description or None)
        return product.id
    log(f"create product {key} ({name})")
    if dry:
        return None
    return stripe.Product.create(name=name, description=description or None, metadata={"dcp_key": key}).id


def ensure_price(stripe, product_id: str | None, lookup_key: str, cents: int, interval: str | None,
                 dry: bool, log) -> str | None:
    """Make sure the lookup key points at a price with this amount (and billing interval)."""
    current = stripe.Price.list(lookup_keys=[lookup_key], active=True, limit=1).data
    if current:
        price = current[0]
        same_interval = (price.recurring.interval if price.recurring else None) == interval
        if price.unit_amount == cents and same_interval:
            return price.id
    log(f"{'replace' if current else 'create'} price {lookup_key}: ${cents / 100:,.2f}"
        + (f" per {interval}" if interval else ""))
    if dry or product_id is None:
        return current[0].id if current else None
    new = stripe.Price.create(
        product=product_id, currency="usd", unit_amount=cents, lookup_key=lookup_key,
        transfer_lookup_key=True,   # move the key from the old price to this one
        **({"recurring": {"interval": interval}} if interval else {}),
    )
    if current:
        stripe.Price.modify(current[0].id, active=False)   # archive the old price
    return new.id


def sync(stripe, dry: bool = False, log=print) -> list[dict]:
    """Sync every plan + the consulting hour. Returns the subscription_plans rows."""
    rows = []
    for order, plan in enumerate(pricing.SUBSCRIPTIONS):
        product = ensure_product(stripe, plan.id, plan.name, plan.description, dry, log)
        rows.append({
            "id": plan.id, "name": plan.name, "description": plan.description,
            "service_ids": list(plan.service_ids), "monthly_price_usd": plan.monthly_usd,
            "annual_price_usd": plan.annual_usd, "sort_order": order, "stripe_product_id": product,
            "stripe_monthly_price_id": ensure_price(stripe, product, pricing.lookup_key(plan.id, "month"),
                                                    _cents(plan.monthly_usd), "month", dry, log),
            "stripe_annual_price_id": ensure_price(stripe, product, pricing.lookup_key(plan.id, "year"),
                                                   _cents(plan.annual_usd), "year", dry, log),
        })
    consulting = ensure_product(stripe, "consulting_hour", "Consulting (1 hour)",
                                "Prepaid consulting after your free 30-minute consultation.", dry, log)
    ensure_price(stripe, consulting, pricing.CONSULTING_LOOKUP_KEY, _cents(pricing.consulting_rate()), None, dry, log)
    return rows


async def _save(rows: list[dict]) -> None:
    await database.init_pool()
    try:
        for row in rows:
            await account_store.save_plan(row)
    finally:
        await database.close_pool()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="show what would change; change nothing")
    args = ap.parse_args()
    settings = get_settings()
    if not settings.stripe_secret_key:
        sys.exit("Set STRIPE_SECRET_KEY first (use a test key, sk_test_..., until you go live).")
    import stripe

    stripe.api_key = settings.stripe_secret_key
    mode = "LIVE" if settings.stripe_secret_key.startswith("sk_live") else "test"
    print(f"Syncing plans to Stripe ({mode} mode){' — dry run' if args.dry_run else ''}…")
    plan_rows = sync(stripe, dry=args.dry_run)
    if settings.database_url and not args.dry_run:
        asyncio.run(_save(plan_rows))
        print(f"Saved {len(plan_rows)} plans to subscription_plans.")
    print("Done.")
