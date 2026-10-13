"""
Application settings (configuration).

WHAT THIS FILE DOES
-------------------
Reads every setting the backend needs from environment variables (or a local
`.env` file during development) and exposes them as one typed `Settings`
object. This is the same pattern used in the Video_Game_Stats_Tracker
template: `pydantic-settings` validates the values when the app starts, so a
typo in a number (for example `PRICE_PER_MINUTE=abc`) fails loudly at startup
instead of silently at request time.

HOW TO USE IT
-------------
    from api.core.config import get_settings
    settings = get_settings()
    settings.max_upload_mb   # -> 50

`get_settings()` is wrapped in `@lru_cache`, so the environment is read only
once per process (cheap to call anywhere).

WHERE VALUES COME FROM (in priority order)
------------------------------------------
1. Real environment variables (Render / Vercel dashboards in production).
2. A `.env` file in the project root (local development only — never commit it).
3. The defaults written below.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Database (Supabase / PostgreSQL) ──────────────────────────────────────
    # Full connection string, e.g.
    #   postgresql://postgres.<project>:<password>@aws-0-us-east-1.pooler.supabase.com:6543/postgres
    # Leave it EMPTY for local development: the app then falls back to an
    # in-memory store (see api/services/account_store.py) so you can click
    # through the whole flow without a database.
    database_url: str = Field(default="", alias="DATABASE_URL")

    # ── Auth (JSON Web Tokens) ────────────────────────────────────────────────
    # The secret used to sign login tokens. MUST be a long random string in
    # production. Generate one with:  python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret_key: str = Field(default="dev-only-secret-change-me-in-production-0000", alias="JWT_SECRET_KEY")
    jwt_algorithm: str = "HS256"
    # How long a login token stays valid (minutes). 7 days by default.
    access_token_expire_minutes: int = Field(default=60 * 24 * 7, alias="ACCESS_TOKEN_EXPIRE_MINUTES")

    # ── CORS (which websites may call this API) ───────────────────────────────
    # Comma-separated list, e.g. "http://localhost:3000,https://yourdomain.com"
    allowed_origins: str = Field(default="http://localhost:3000", alias="ALLOWED_ORIGINS")

    # ── Upload limits ─────────────────────────────────────────────────────────
    # Largest single file accepted, in megabytes. Files are processed in
    # memory (never written to disk), so this protects the server's RAM.
    max_upload_mb: int = Field(default=50, alias="MAX_UPLOAD_MB")
    # Most files accepted in one multi-file upload.
    max_files_per_upload: int = Field(default=20, alias="MAX_FILES_PER_UPLOAD")
    # Largest size a compressed upload (.zip, .gz, ...) may expand to, in MB.
    # Protects the server from "zip bombs" (tiny archives that expand hugely).
    max_decompressed_mb: int = Field(default=200, alias="MAX_DECOMPRESSED_MB")
    # Safety cap on how many cleaned rows are sent back to the browser for the
    # on-screen preview. The goal is to preview 100% of the data; this cap only
    # kicks in for very large files, so the browser tab does not freeze.
    # The downloaded file ALWAYS contains every row regardless of this cap.
    preview_max_rows: int = Field(default=50_000, alias="PREVIEW_MAX_ROWS")

    # ── Pricing (runtime-based billing) ───────────────────────────────────────
    # Price in US dollars for each started minute of processing runtime.
    # Placeholder value — set your real rate in the environment.
    price_per_runtime_minute_usd: float = Field(default=0.50, alias="PRICE_PER_RUNTIME_MINUTE_USD")
    # Billing increment in seconds. 60 = "charge by the minute, rounded up".
    billing_increment_seconds: int = Field(default=60, alias="BILLING_INCREMENT_SECONDS")
    # Minimum charge for any paid download, in US dollars (0 = no minimum).
    minimum_charge_usd: float = Field(default=0.0, alias="MINIMUM_CHARGE_USD")

    # ── Consultant hand-off ───────────────────────────────────────────────────
    # Your scheduling link (Calendly, Cal.com, Google Calendar booking page…).
    # Shown whenever a guest picks a service that needs a human consultant.
    booking_url: str = Field(default="https://calendly.com/your-link", alias="BOOKING_URL")

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
        "populate_by_name": True,
    }

    # ── Convenience helpers (computed, not read from the environment) ─────────
    @property
    def origins_list(self) -> list[str]:
        """ALLOWED_ORIGINS split into a clean Python list."""
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def max_upload_bytes(self) -> int:
        """MAX_UPLOAD_MB converted to bytes (1 MB = 1,048,576 bytes)."""
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """Return the one shared Settings object (created on first call)."""
    return Settings()
