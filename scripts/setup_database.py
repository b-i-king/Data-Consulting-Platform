"""
Create (or update) the database tables, then check that everything is there.

WHAT THIS SCRIPT DOES
---------------------
1. Connects to DATABASE_URL (read from your .env file, like the API does).
2. Runs db/schema.sql      -> the platform tables (accounts, consultants, ...).
3. Runs db/warehouse.sql   -> the client-data warehouse (Data Engineering),
                              unless you pass --no-warehouse.
4. Prints every expected table with its row count, so you can see at a
   glance that the database is set up and what's in it.

Both SQL files are safe to re-run (CREATE ... IF NOT EXISTS), so run this
again after every pull that changes them.

HOW TO RUN
----------
    python scripts/setup_database.py              # create/update + check
    python scripts/setup_database.py --check      # only check (changes nothing)
    python scripts/setup_database.py --no-warehouse

You can do the same by hand: Supabase -> SQL Editor -> paste db/schema.sql ->
Run, then db/warehouse.sql -> Run.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import asyncpg

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # so "api" can be imported when run from anywhere

from api.core.config import get_settings  # noqa: E402

PLATFORM_TABLES = ["accounts", "teams", "consultants", "account_services", "subscription_plans", "subscriptions",
                   "subscription_items", "consulting_purchases", "consultations", "storage_consents", "load_runs",
                   "usage_events", "intake_responses"]
WAREHOUSE_TABLES = ["dim_client", "dim_date", "dim_source", "dim_event_type", "dim_customer", "dim_employee",
                    "dim_product", "fact_event"]


async def main(check_only: bool, warehouse: bool) -> int:
    """
    Apply the SQL files (unless check_only) and print each table's row count.

    Args:
        check_only: bool, True to only look, never change anything.
        warehouse: bool, True to also apply / check db/warehouse.sql.

    Returns:
        int: 0 when every expected table exists, 1 otherwise (the exit code).
    """
    dsn = get_settings().database_url
    if not dsn:
        print("DATABASE_URL is empty. Put your Supabase connection string in .env first "
              "(Supabase -> Connect -> Transaction pooler, port 6543).")
        return 1
    # statement_cache_size=0: required by Supabase's transaction pooler.
    conn = await asyncpg.connect(dsn, statement_cache_size=0, timeout=15)
    try:
        if not check_only:
            files = [ROOT / "db" / "schema.sql"] + ([ROOT / "db" / "warehouse.sql"] if warehouse else [])
            for path in files:
                print(f"Running {path.relative_to(ROOT)} ...")
                await conn.execute(path.read_text(encoding="utf-8"))   # a whole script, no parameters
            print("Done.\n")

        missing = 0
        groups = [("public", PLATFORM_TABLES)] + ([("warehouse", WAREHOUSE_TABLES)] if warehouse else [])
        for schema, tables in groups:
            print(f"{schema} schema")
            for table in tables:
                exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"{schema}.{table}")
                if not exists:
                    missing += 1
                    print(f"  MISSING  {table}")
                    continue
                # Counting through the API's own connection: warehouse rows hidden by
                # Row Level Security show as 0 unless the role bypasses RLS.
                count = await conn.fetchval(f'SELECT count(*) FROM "{schema}"."{table}"')
                print(f"  ok       {table:22} {count:>10,} rows")
        consultants = await conn.fetch("SELECT email, is_default FROM consultants ORDER BY created_at") \
            if await conn.fetchval("SELECT to_regclass('public.consultants') IS NOT NULL") else []
        print("\nConsultants:", ", ".join(f"{c['email']}{' (default)' if c['is_default'] else ''}" for c in consultants)
              or "none yet - sign up in the app with an email from CONSULTANT_EMAILS + your access code.")
        if missing:
            print(f"\n{missing} table(s) missing. Run without --check to create them.")
        return 1 if missing else 0
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create the database tables and check them.")
    parser.add_argument("--check", action="store_true", help="only check; change nothing")
    parser.add_argument("--no-warehouse", action="store_true", help="skip db/warehouse.sql")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.check, not args.no_warehouse)))
