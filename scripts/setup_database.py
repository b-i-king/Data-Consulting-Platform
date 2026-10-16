"""
Create (or update) the database tables, then check that everything is there.

WHAT THIS SCRIPT DOES
---------------------
1. Connects to DATABASE_URL (read from your .env file, like the API does).
2. Runs db/schema.sql, which creates the two schemas:
     dim   the platform: accounts, consultants, teams, subscriptions, ...
     fact  client data: account_data (stored data) and field_map (shared names)
3. Prints every expected table with its row count, so you can see at a
   glance that the database is set up and what's in it, and lists the
   consultants (created by the app when a consultant first signs in).

db/schema.sql is safe to re-run (CREATE ... IF NOT EXISTS), so run this
again after every pull that changes it.

HOW TO RUN
----------
    python scripts/setup_database.py              # create/update + check
    python scripts/setup_database.py --check      # only check (changes nothing)

You can do the same by hand: Supabase -> SQL Editor -> paste db/schema.sql -> Run.
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

# Every table db/schema.sql creates, by schema.
TABLES = {
    "dim": ["accounts", "consultants", "teams", "subscriptions", "consultations", "usage_events",
            "storage_consents", "load_runs", "data_connections",
            "dashboard_tabs", "question_log", "api_usage"],
    "fact": ["account_data", "field_map"],
}


async def main(check_only: bool) -> int:
    """
    Apply db/schema.sql (unless check_only) and print each table's row count.

    Args:
        check_only: bool, True to only look, never change anything.

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
            path = ROOT / "db" / "schema.sql"
            print(f"Running {path.relative_to(ROOT)} ...")
            await conn.execute(path.read_text(encoding="utf-8"))   # a whole script, no parameters
            print("Done.\n")

        missing = 0
        for schema, tables in TABLES.items():
            print(f"{schema} schema")
            for table in tables:
                exists = await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"{schema}.{table}")
                if not exists:
                    missing += 1
                    print(f"  MISSING  {table}")
                    continue
                # fact.account_data uses Row Level Security: unless this login
                # bypasses it (Supabase's postgres role does), its count shows 0.
                count = await conn.fetchval(f'SELECT count(*) FROM "{schema}"."{table}"')
                print(f"  ok       {table:18} {count:>10,} rows")
        consultants = await conn.fetch("SELECT email, is_default FROM dim.consultants ORDER BY created_at") \
            if await conn.fetchval("SELECT to_regclass('dim.consultants') IS NOT NULL") else []
        print("\nConsultants:", ", ".join(f"{c['email']}{' (default)' if c['is_default'] else ''}" for c in consultants)
              or "none yet - sign up in the app with your ADMIN_EMAILS address.")
        if missing:
            print(f"\n{missing} table(s) missing. Run without --check to create them.")
        return 1 if missing else 0
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create the database tables and check them.")
    parser.add_argument("--check", action="store_true", help="only check; change nothing")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.check)))
