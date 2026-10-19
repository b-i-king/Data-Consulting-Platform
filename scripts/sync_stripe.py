"""
Create (or update) the Stripe products and prices: the tools in
api/services/pricing.py, at the prices in dim.plan_prices (the database).

WHAT IT DOES
------------
For every TOOL (and the All tools bundle) in SUBSCRIPTIONS it makes sure Stripe has:
    one PRODUCT  (e.g. "Dashboards")
    two PRICES   monthly and annual, found later by their "lookup key":
                 dcp_data_visualization_month, dcp_data_visualization_year
A client's subscription holds one item per tool they pick ("build your own
package"); the bundle is a single item.
It also makes the two products a consultant's SETUP and ONGOING HELP are sold
under (their prices are sent at checkout — api/services/consulting_billing.py).
Hourly consulting sessions are paid on Cal.com, so there's no price for those.

Normally you don't need to run this after a price change: the Admin panel
(Admin -> Prices) updates Stripe when you save. Run it once when you first
connect Stripe, and after editing dim.plan_prices by hand in Supabase.

Stripe prices can't be edited once created. When a price changed, this
script creates a NEW price, moves the lookup key to it (`transfer_lookup_key`),
and archives the old one — existing subscribers keep their old price until
you migrate them in the Stripe dashboard.

Nothing is written to the database: the app finds each price in Stripe by
its lookup key. The helpers live in api/services/stripe_catalog.py.

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

from api.core.config import get_settings  # noqa: E402

# The helpers live in the API (the Admin panel's price editor uses them too).
from api.services.stripe_catalog import _cents, ensure_price, ensure_product, sync, sync_consulting, sync_plan  # noqa: E402,F401


async def _load_db_prices() -> None:
    """
    Read the live prices from dim.plan_prices (when DATABASE_URL is set), so Stripe gets those.

    Without a database the defaults written in api/services/pricing.py are used.

    Returns:
        None.
    """
    from api.core import database
    from api.services import price_store

    await database.init_pool()
    try:
        await price_store.load()
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
    asyncio.run(_load_db_prices())   # prices come from dim.plan_prices (Admin -> Prices)
    print(f"Syncing plans to Stripe ({mode} mode){' - dry run' if args.dry_run else ''}...")
    plan_rows = sync(stripe, dry=args.dry_run)
    for row in plan_rows:
        print(f"  {row['id']}: {row['stripe_monthly_price_id']} (month), {row['stripe_annual_price_id']} (year)")
    print("Syncing the consultant products (setup, ongoing help)...")
    for row in sync_consulting(stripe, dry=args.dry_run):
        print(f"  {row['key']}: {row['stripe_product_id']}")
    print("Done.")
