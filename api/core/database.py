"""
Database connection pool (Supabase / PostgreSQL).

WHAT THIS FILE DOES
-------------------
Opens ONE pool of reusable database connections when the API starts and
closes it when the API shuts down. A "pool" keeps a few connections open so
each request does not pay the cost of logging in to Postgres again.

This mirrors `api/core/database.py` in the Video_Game_Stats_Tracker template
(asyncpg + Supabase's Transaction pooler on port 6543).

DATABASE IS OPTIONAL DURING DEVELOPMENT
---------------------------------------
If `DATABASE_URL` is empty, `pool` simply stays `None`. Code that needs the
database (see api/services/account_store.py) checks for that and uses an
in-memory fallback instead. The guest flow (upload → clean → preview) never
touches the database at all.
"""

import logging

import asyncpg

from api.core.config import get_settings

logger = logging.getLogger(__name__)

# The shared pool. `None` means "no database configured / not reachable".
pool: asyncpg.Pool | None = None


async def init_pool() -> None:
    """
    Create the connection pool. Called once from the FastAPI lifespan hook.

    Behavior:
    - DATABASE_URL empty: logs a warning and leaves `pool` as None, so the app
      runs on the in-memory store (development mode).
    - DATABASE_URL set: opens an asyncpg pool of 1 to 5 connections with
      prepared-statement caching off (required by Supabase's transaction
      pooler) and a 10-second connect timeout.
    - Connection fails (e.g. a paused Supabase project): logs the error and
      leaves `pool` as None instead of crashing, so guest cleaning still works.

    Returns:
        None. Side effect: sets the module-level `pool` variable.

    Example:
        Called by `lifespan` in api/main.py on startup:

            await database.init_pool()
            if database.pool is None:
                ...  # account_store falls back to its in-memory dictionaries
    """
    global pool
    dsn = get_settings().database_url
    if not dsn:
        logger.warning("DATABASE_URL not set — using the in-memory store (development mode).")
        return
    try:
        pool = await asyncpg.create_pool(
            dsn=dsn,
            min_size=1,
            max_size=5,
            # Supabase's pooler (PgBouncer, transaction mode) does not support
            # prepared-statement caching, so it must be turned off.
            statement_cache_size=0,
            # Recycle idle connections before Supabase closes them on its side.
            max_inactive_connection_lifetime=300,
            timeout=10,
        )
        logger.info("Database connected.")
    except Exception as exc:  # noqa: BLE001 — we want the app to start anyway
        # A paused Supabase project must not stop the API from starting; the
        # guest cleaning flow still works without the database.
        logger.error("Database unavailable, falling back to in-memory store: %s", exc)
        pool = None


async def close_pool() -> None:
    """
    Close every connection in the pool. Called on shutdown.

    Safe to call when no pool was ever opened (it then does nothing). After
    closing, `pool` is reset to None so nothing tries to use a closed pool.

    Returns:
        None. Side effect: closes the pool and sets `pool` to None.

    Example:
        Called by `lifespan` in api/main.py after the server stops:

            await database.close_pool()
    """
    global pool
    if pool is not None:
        await pool.close()
        pool = None
