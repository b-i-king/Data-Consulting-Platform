"""
FastAPI application entry point.

HOW TO RUN LOCALLY
------------------
    pip install -r requirements.txt
    uvicorn api.main:app --reload          # http://localhost:8000
    open http://localhost:8000/docs        # interactive API documentation

WHAT HAPPENS HERE
-----------------
1. `lifespan` opens the database pool on startup and closes it on shutdown.
2. CORS middleware allows the Next.js website (ALLOWED_ORIGINS) to call us.
3. Each router (a group of related endpoints) is attached under `/api`.

Structure mirrors the Video_Game_Stats_Tracker template:
    api/core/      settings, database, security
    api/models/    request/response shapes (Pydantic)
    api/routers/   HTTP endpoints — thin; they call services
    api/services/  the real logic (pandas cleaning, categorizing, pricing)
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from api.core import database
from api.core.config import get_settings
from api.routers import accounts, files, intake, skills


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await database.init_pool()   # startup
    yield
    await database.close_pool()  # shutdown


app = FastAPI(
    title="Data Consulting Platform API",
    description="Guest-friendly data intake, automatic cleaning, and consultant hand-off.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Disposition", "X-Runtime-Seconds", "X-Amount-USD"],
)

# Compress responses over 1 KB. The data preview (JSON) and CSV downloads
# shrink several-fold, which keeps outbound bandwidth — billed by the host
# per GB — low. Browsers decompress automatically. See docs/PRICING.md.
app.add_middleware(GZipMiddleware, minimum_size=1000)

app.include_router(intake.router, prefix="/api", tags=["intake"])
app.include_router(files.router, prefix="/api", tags=["files"])
app.include_router(accounts.router, prefix="/api", tags=["accounts"])
app.include_router(skills.router, prefix="/api", tags=["skills"])


@app.get("/health", tags=["health"])
async def health():
    """Used by Render's health check and the frontend's warm-up ping."""
    return {"status": "ok", "database": "connected" if database.pool else "in-memory"}
