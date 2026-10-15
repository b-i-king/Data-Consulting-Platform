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
    # (a trailing "/" is ignored, so a pasted "https://x.vercel.app/" still works).
    allowed_origins: str = Field(default="http://localhost:3000", alias="ALLOWED_ORIGINS")
    # Optional pattern for extra origins — e.g. Vercel's preview links, which get
    # a new address per deploy:  https://data-consulting-platform-.*\.vercel\.app
    # Leave empty to allow only ALLOWED_ORIGINS.
    allowed_origin_regex: str = Field(default="", alias="ALLOWED_ORIGIN_REGEX")

    # ── Upload limits ─────────────────────────────────────────────────────────
    # Largest single file accepted, in megabytes. Files are processed in
    # memory (never written to disk), so this protects the server's RAM.
    max_upload_mb: int = Field(default=50, alias="MAX_UPLOAD_MB")
    # Most files accepted in one multi-file upload.
    max_files_per_upload: int = Field(default=20, alias="MAX_FILES_PER_UPLOAD")
    # Largest size a compressed upload (.zip, .gz, ...) may expand to, in MB.
    # Protects the server from "zip bombs" (tiny archives that expand hugely).
    max_decompressed_mb: int = Field(default=200, alias="MAX_DECOMPRESSED_MB")
    # How many cleaned rows are sent back to the browser for the on-screen
    # preview: the first 5, like pandas' df.head(). Small on purpose — the
    # browser re-runs the cleaning each time the user changes a missing-value
    # choice, and a short preview keeps that instant. Raise it (e.g. 50000)
    # to page through more rows on screen. The downloaded file ALWAYS
    # contains every row regardless of this setting.
    preview_max_rows: int = Field(default=5, alias="PREVIEW_MAX_ROWS")

    # ── Pricing (runtime-based billing) ───────────────────────────────────────
    # Price in US dollars for each started minute of processing runtime.
    # Placeholder value — set your real rate in the environment.
    price_per_runtime_minute_usd: float = Field(default=0.50, alias="PRICE_PER_RUNTIME_MINUTE_USD")
    # Billing increment in seconds. 60 = "charge by the minute, rounded up".
    billing_increment_seconds: int = Field(default=60, alias="BILLING_INCREMENT_SECONDS")
    # Minimum charge for any paid download, in US dollars (0 = no minimum).
    minimum_charge_usd: float = Field(default=0.0, alias="MINIMUM_CHARGE_USD")

    # ── Fair use (api/services/fair_use.py) ───────────────────────────────────
    # Most cleaned-data downloads one client account can make in a rolling
    # window (DOWNLOAD_WINDOW_HOURS). Stops anyone splitting work into dozens of
    # tiny downloads a day; normal use is a handful. 0 = no limit.
    daily_download_limit: int = Field(default=20, alias="DAILY_DOWNLOAD_LIMIT")
    download_window_hours: int = Field(default=24, alias="DOWNLOAD_WINDOW_HOURS")

    # ── Consultant hand-off ───────────────────────────────────────────────────
    # Your scheduling link (Calendly, Cal.com, Google Calendar booking page…).
    # Shown whenever a guest picks a service that needs a human consultant.
    booking_url: str = Field(default="https://calendly.com/your-link", alias="BOOKING_URL")

    # Hourly consulting rate after the free 30-minute first consultation (and
    # from the first hour for Data Strategy & Discovery). Placeholder: $80.
    consulting_rate_per_hour_usd: float = Field(default=80.0, alias="CONSULTING_RATE_PER_HOUR_USD")

    # ── Payments (Stripe) ─────────────────────────────────────────────────────
    # Empty = PLACEHOLDER mode: choosing a plan turns it on right away, with no
    # charge (fine for testing). Set both to take real payments (see
    # docs/PAYMENTS.md). Use test keys (sk_test_...) until you go live.
    stripe_secret_key: str = Field(default="", alias="STRIPE_SECRET_KEY")
    stripe_webhook_secret: str = Field(default="", alias="STRIPE_WEBHOOK_SECRET")
    # The website's address, for "back to the site" links after paying at Stripe.
    site_url: str = Field(default="http://localhost:3000", alias="SITE_URL")

    # ── Consultants ───────────────────────────────────────────────────────────
    # Email addresses that sign in as CONSULTANTS (comma-separated). The FIRST
    # one is the default consultant: clients who tick "I'd like a consultant to
    # do this for me" are assigned to them. Empty = no consultants.
    consultant_emails: str = Field(default="", alias="CONSULTANT_EMAILS")
    # A secret code a consultant must enter to sign in (or sign up). Sign-in is
    # by email only for now, so without this anyone who knows a consultant's
    # email could see every client. Empty = consultant sign-in is switched off.
    consultant_access_code: str = Field(default="", alias="CONSULTANT_ACCESS_CODE")

    # ── Big-data engine (PySpark, optional) ───────────────────────────────────
    # pandas is the data engine. PySpark is an OPTIONAL second engine for data
    # too big for one machine's memory or living in a client's cloud warehouse.
    # It needs `pip install -r requirements-spark.txt` and Java 17, so it is
    # OFF by default (see docs/SPARK.md and api/services/spark_engine.py).
    spark_enabled: bool = Field(default=False, alias="SPARK_ENABLED")
    # Where Spark runs: "local[*]" = on this machine, using every CPU core;
    # or a cluster address such as "spark://host:7077".
    spark_master: str = Field(default="local[*]", alias="SPARK_MASTER")
    # Name shown in the Spark web UI and cluster logs.
    spark_app_name: str = Field(default="data-consulting-platform", alias="SPARK_APP_NAME")
    # Table size (rows × columns) at which a job switches from pandas to Spark.
    # 50,000,000 cells is roughly 1–4 GB in pandas memory.
    spark_min_cells: int = Field(default=50_000_000, alias="SPARK_MIN_CELLS")

    model_config = {
        "env_file": ".env",
        "extra": "ignore",
        "populate_by_name": True,
    }

    # ── Convenience helpers (computed, not read from the environment) ─────────
    @property
    def consultant_email_list(self) -> list[str]:
        """
        CONSULTANT_EMAILS as a clean lowercase list, default consultant first.

        Splits the comma-separated setting, trims spaces around each address,
        lowercases it (so sign-in matching ignores case) and drops empty pieces
        left by stray or trailing commas. Order is kept, so index 0 is the
        default consultant that new "do it for me" clients are assigned to.

        Returns:
            list[str]: consultant email addresses, lowercase, in the order written;
            an empty list when CONSULTANT_EMAILS is not set.

        Example:
            >>> Settings(consultant_emails=" Ana@Example.com, ,bo@example.com,").consultant_email_list
            ['ana@example.com', 'bo@example.com']
        """
        return [e.strip().lower() for e in self.consultant_emails.split(",") if e.strip()]

    @property
    def origins_list(self) -> list[str]:
        """
        ALLOWED_ORIGINS split into a clean Python list.

        Each comma-separated entry is trimmed of spaces and of a trailing "/",
        because browsers send the Origin header without one (a pasted
        "https://x.vercel.app/" would otherwise never match). Empty entries are
        dropped. The result is handed to the CORS middleware in api/main.py.

        Returns:
            list[str]: website origins allowed to call the API.

        Example:
            >>> Settings(allowed_origins="http://localhost:3000, https://x.vercel.app/").origins_list
            ['http://localhost:3000', 'https://x.vercel.app']
        """
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def using_default_jwt_secret(self) -> bool:
        """
        True when JWT_SECRET_KEY wasn't set — fine locally, unsafe online.

        The built-in default secret starts with "dev-only-secret". Anyone who
        reads the source code knows it and could forge login tokens, so the app
        logs a warning at startup (api/main.py) when this is True on Render.

        Returns:
            bool: True if the default development secret is still in use.

        Example:
            >>> Settings(jwt_secret_key="dev-only-secret-x").using_default_jwt_secret
            True
        """
        return self.jwt_secret_key.startswith("dev-only-secret")

    @property
    def max_upload_bytes(self) -> int:
        """
        MAX_UPLOAD_MB converted to bytes (1 MB = 1,048,576 bytes).

        Upload size checks compare against the raw byte count of the file, so
        this saves every caller from repeating the conversion.

        Returns:
            int: the largest accepted single-file size, in bytes.

        Example:
            >>> Settings(max_upload_mb=50).max_upload_bytes
            52428800
        """
        return self.max_upload_mb * 1024 * 1024


@lru_cache
def get_settings() -> Settings:
    """
    Return the one shared Settings object (created on first call).

    The `@lru_cache` decorator remembers the result, so environment variables
    and the `.env` file are read only once per process; later calls return the
    very same object. Tests that change environment variables must call
    `get_settings.cache_clear()` to force a fresh read.

    Returns:
        Settings: the validated application settings.

    Raises:
        pydantic.ValidationError: on the first call, if an environment value
            cannot be converted to its declared type (e.g. MAX_UPLOAD_MB=abc).

    Example:
        >>> from api.core.config import get_settings
        >>> get_settings() is get_settings()
        True
    """
    return Settings()
