"""
Run every stored sync that is due (QuickBooks / Xero -> managed storage).

WHAT THIS SCRIPT DOES
---------------------
Finds each connection whose next_sync_at has come (its client chose daily,
weekly, hourly... in Settings -> Integrations) and runs the stored sync for it
(api/services/sync.py run_due): only what changed since the last run, within
the tool's free-plan limits. One client's failure never stops the others.

HOW TO RUN IT
-------------
    python scripts/run_syncs.py            # needs DATABASE_URL (and the tools' keys)

On Render: New -> Cron Job, same repository and environment variables as the
API, command `python scripts/run_syncs.py`, schedule `0 * * * *` (hourly).
"Live" (every 15 minutes) needs `*/15 * * * *`. See docs/DEPLOYMENT.md.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))   # so "api" can be imported when run from anywhere

from api.core import database  # noqa: E402
from api.core.config import get_settings  # noqa: E402
from api.services import sync  # noqa: E402


async def main() -> int:
    """Run the due syncs and print one line per connection. Returns the exit code (1 = any failed)."""
    if not get_settings().database_url:
        print("DATABASE_URL is not set: there is no stored data to sync into.")
        return 1
    await database.init_pool()
    try:
        report = await sync.run_due()
    finally:
        await database.close_pool()
    for r in report:
        if r["ok"]:
            res = r["result"]
            print(f"ok    {r['connector']:<10} {r['account_id']}  {res['records']} records, "
                  f"{res['rows_loaded']} new rows{'' if res['complete'] else ' (continues tomorrow: daily limit)'}")
        else:
            print(f"error {r['connector']:<10} {r['account_id']}  {r['error']}")
    print(f"{len(report)} connection(s) due.")
    return 1 if any(not r["ok"] for r in report) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
