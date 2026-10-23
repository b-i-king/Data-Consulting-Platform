"""
FastAPI application entry point.

HOW TO RUN LOCALLY
------------------
    pip install -r requirements.txt
    uvicorn api.main:app --reload          # http://localhost:8000
    open http://localhost:8000/docs        # interactive API documentation

WHAT HAPPENS HERE
-----------------
1. `lifespan` opens the database pool on startup (and loads the live tool
   prices from dim.plan_prices) and closes the pool on shutdown.
2. Middleware: per-IP request limits (core/rate_limit.py), CORS (only the
   website at ALLOWED_ORIGINS may call us) and gzip. Error monitoring with
   personal data stripped starts first (core/monitoring.py, SENTRY_DSN).
3. Each router (a group of related endpoints) is attached under `/api`.

Structure mirrors the Video_Game_Stats_Tracker template:
    api/core/      settings, database, security
    api/models/    request/response shapes (Pydantic)
    api/routers/   HTTP endpoints — thin; they call services
    api/services/  the real logic (pandas cleaning, categorizing, pricing)
"""

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from api.core import database, monitoring, rate_limit
from api.core.config import get_settings
from api.routers import (accounts, admin, ai, billing, compare, connections, consultants, consultations, dashboards, entities,
                         enterprise, files, intake, partners, skills, warehouse)
from api.services import auth, price_store

logger = logging.getLogger("uvicorn.error")   # shows up in Render's logs


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Run startup work before the API serves requests, and cleanup after it stops.

    On startup (when running on Render, which sets RENDER=true) it logs a
    warning if JWT_SECRET_KEY or ALLOWED_ORIGINS were left at their
    development defaults, then opens the database pool. The `yield` is the
    point where the server runs; code after it closes the pool on shutdown.

    Args:
        _app: FastAPI, the application instance (required by FastAPI; unused).

    Returns:
        AsyncIterator[None]: an async context manager (via @asynccontextmanager)
        that yields once, while the server is running.

    Example:
        Passed to FastAPI rather than called directly:

            app = FastAPI(lifespan=lifespan)
    """
    settings = get_settings()
    if settings.using_default_jwt_secret and os.environ.get("RENDER"):
        # Render sets RENDER=true. A missing secret means anyone could forge sign-ins.
        logger.warning("JWT_SECRET_KEY is not set — using the development secret. Set it in Render > Environment.")
    if settings.origins_list == ["http://localhost:3000"] and os.environ.get("RENDER"):
        logger.warning("ALLOWED_ORIGINS is still localhost — set it to your Vercel URL or the site can't reach the API.")
    await database.init_pool()   # startup
    await price_store.load()     # tool prices from dim.plan_prices (Admin -> Prices)
    yield
    await database.close_pool()  # shutdown


monitoring.init()   # error reports with personal data stripped (only when SENTRY_DSN is set)

_docs = get_settings().show_api_docs   # hidden on the live server (config.py, API_DOCS)
app = FastAPI(
    title="Data Consulting Platform API",
    description="Guest-friendly data intake, automatic cleaning, and consultant hand-off.",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if _docs else None,
    redoc_url="/redoc" if _docs else None,
    openapi_url="/openapi.json" if _docs else None,
)

# Per-IP request limits (api/core/rate_limit.py). Added BEFORE CORS so CORS
# wraps it: a 429 still carries the CORS headers and the website can show it.
app.add_middleware(BaseHTTPMiddleware, dispatch=rate_limit.middleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().origins_list,
    allow_origin_regex=get_settings().allowed_origin_regex or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Runtime-Seconds", "X-Amount-USD", "X-Rows", "X-Truncated", "Retry-After",
                    "X-Query"],
)

# Compress responses over 1 KB. The data preview (JSON) and CSV downloads
# shrink several-fold, which keeps outbound bandwidth — billed by the host
# per GB — low. Browsers decompress automatically. See docs/PRICING.md.
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(intake.router, prefix="/api", tags=["intake"])
app.include_router(files.router, prefix="/api", tags=["files"])
app.include_router(accounts.router, prefix="/api", tags=["accounts"])
app.include_router(skills.router, prefix="/api", tags=["skills"])
app.include_router(consultants.router, prefix="/api", tags=["consultants"])
app.include_router(admin.router, prefix="/api", tags=["admin"])
app.include_router(dashboards.router, prefix="/api", tags=["dashboards"])
app.include_router(entities.router, prefix="/api", tags=["entities"])
app.include_router(ai.router, prefix="/api", tags=["ai"])
app.include_router(compare.router, prefix="/api", tags=["compare"])
app.include_router(billing.router, prefix="/api", tags=["billing"])
app.include_router(consultations.router, prefix="/api", tags=["consultations"])
app.include_router(warehouse.router, prefix="/api", tags=["warehouse"])
app.include_router(connections.router, prefix="/api", tags=["connections"])
app.include_router(partners.router, prefix="/api", tags=["partners"])
app.include_router(enterprise.router, prefix="/api", tags=["enterprise"])


@app.get("/health", tags=["health"])
async def health():
    """
    Used by Render's health check and the frontend's warm-up ping.

    Needs no sign-in and touches no data. Also reports whether the database
    pool is open (a quick way to see if DATABASE_URL works) and which password
    checker is on (Supabase Auth, or local hashes when SUPABASE_URL is empty).

    Returns:
        dict: {"status": "ok", "database": "connected" or "in-memory",
        "auth": "supabase" or "local"}.

    Example:
        GET /health
        -> 200 {"status": "ok", "database": "in-memory", "auth": "local"}
    """
    return {"status": "ok", "database": "connected" if database.pool else "in-memory", "auth": auth.mode()}
