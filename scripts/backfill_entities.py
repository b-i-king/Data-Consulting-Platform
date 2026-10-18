"""
One-time BACKFILL: link data stored before entities existed.

WHAT THIS SCRIPT DOES
---------------------
Before fact.entities, a customer's name, ID and details were copied onto every
stored row. For each account with stored data, this links every stored
source's customers, vendors, products, payees and locations to fact.entities
and rewrites the rows' attributes to hold only the link
(api/services/warehouse_store.py relink_stored). Rows keep their added_at /
modified_at; "Use my stored data" gives the same table back. Safe to run
twice: rows already linked are left alone.

HOW TO RUN IT
-------------
    python scripts/setup_database.py       # first: creates fact.entities / fact.entity_sources
    python scripts/backfill_entities.py
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from api.core import database  # noqa: E402
from api.core.config import get_settings  # noqa: E402
from api.services import account_store, warehouse_store  # noqa: E402


async def main() -> int:
    """Relink every account with stored data; print one line each."""
    if not get_settings().database_url:
        print("DATABASE_URL is not set: there is no stored data to backfill.")
        return 1
    await database.init_pool()
    try:
        # Every plan holder, from dim.accounts: the API's least-privilege login (dcp_api) sees
        # fact.account_data one account at a time only, so it can't list "who has data" there.
        ids = [str(r["id"]) for r in await database.pool.fetch("SELECT id FROM dim.accounts")]
        for aid in ids:
            holder = await account_store.get_account(aid)
            if not holder or not await warehouse_store.has_data(holder):
                continue
            res = await warehouse_store.relink_stored(holder)
            print(f"{aid}  {res['rows_relinked']} rows linked, {res['entities_added']} entities, "
                  f"{res['matches_to_review']} to review")
        print(f"{len(ids)} account(s) checked.")
    finally:
        await database.close_pool()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
