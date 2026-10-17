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

    # ── Passwords (Supabase Auth, email + password) ───────────────────────────
    # Supabase -> Project Settings -> API: the Project URL and the anon /
    # publishable key (NOT the service_role / secret key). When both are set,
    # Supabase Auth stores and checks passwords (api/services/auth.py). When
    # empty, passwords are hashed and checked by the API itself ("local" mode)
    # so you can develop and run tests without Supabase.
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_key: str = Field(default="", alias="SUPABASE_KEY")

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
    # A SECOND scheduling link for paid consulting sessions ($80/hour), shown
    # once a client's free consultation is marked as done (api/services/consultations.py).
    # Empty = everyone keeps seeing BOOKING_URL.
    paid_booking_url: str = Field(default="", alias="PAID_BOOKING_URL")
    # Cal.com webhook signing secret (Cal.com -> Settings -> Developer ->
    # Webhooks -> "Secret"). Lets Cal.com tell the app when a consultation is
    # booked, rescheduled, canceled or has ended. Empty = webhook switched off;
    # you can still mark consultations by hand in the workspace.
    calcom_webhook_secret: str = Field(default="", alias="CALCOM_WEBHOOK_SECRET")

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

    # ── Admin and consultants ─────────────────────────────────────────────────
    # Email addresses of the platform ADMINS (comma-separated; normally just
    # yours). An admin is also a consultant, and the FIRST admin is the default
    # consultant: "do it for me" clients who don't pick anyone go to them.
    # Everyone else becomes a consultant only by asking ("I'd like to work as a
    # consultant") and being APPROVED by an admin in the workspace's Admin panel
    # — there is no access code to share or guess.
    admin_emails: str = Field(default="", alias="ADMIN_EMAILS")

    # ── Integrations: clients' own databases (api/services/cloud_sources.py) ──
    # Leave OFF in production: when true, a client's database may be on a
    # private / local address (only for testing with a database on this machine).
    allow_private_db_hosts: bool = Field(default=False, alias="ALLOW_PRIVATE_DB_HOSTS")

    # ── AI: Beacon, the "Ask your data" assistant (api/services/ai_assistant.py) ──
    # Monthly question allowances, per client (the plan holder): FREE for
    # everyone, PREMIUM with the AI package. Admins have no limit; a consultant
    # uses the allowance of the client they're working on.
    ai_free_monthly: int = Field(default=20, alias="AI_FREE_MONTHLY")
    ai_premium_monthly: int = Field(default=200, alias="AI_PREMIUM_MONTHLY")
    # OPTIONAL: with a Google Gemini key (aistudio.google.com -> Get API key), a
    # Gemini model turns each question into a calculation plan (only the
    # table's column names and category values are sent — never the rows).
    # Without it, the built-in plain-English parser answers (no cost). Free uses
    # Flash-Lite, premium Flash. For client data, turn on BILLING for the key's
    # Google project: Google may use free-tier prompts to improve its products,
    # paid-tier prompts are not used (docs/PRICING.md).
    gemini_api_key: str = Field(default="", alias="GEMINI_API_KEY")
    ai_model_free: str = Field(default="gemini-2.5-flash-lite", alias="AI_MODEL_FREE")
    ai_model_premium: str = Field(default="gemini-2.5-flash", alias="AI_MODEL_PREMIUM")

    # ── Integrations: "Connect" sign-in buttons (api/services/oauth.py) ──────
    # Each provider needs ONE pair of keys from its developer site — the keys
    # belong to the platform, not to each client. A pair left empty simply
    # hides that "Connect" button (the provider shows as coming soon). Every
    # provider sends people back to ONE address, registered in each developer
    # site as the redirect URI:  <API address>/api/connections/oauth/callback
    #   QuickBooks: developer.intuit.com -> your app -> Keys & credentials
    #   Xero:       developer.xero.com -> My Apps -> your app -> Configuration
    #   Supabase:   supabase.com -> your organization -> OAuth Apps (OPTIONAL:
    #               clients can always paste a connection string instead)
    quickbooks_client_id: str = Field(default="", alias="QUICKBOOKS_CLIENT_ID")
    quickbooks_client_secret: str = Field(default="", alias="QUICKBOOKS_CLIENT_SECRET")
    # "sandbox" works with Intuit's DEVELOPMENT keys (a test company); switch to
    # "production" when you use the production keys after Intuit's app review.
    quickbooks_environment: str = Field(default="sandbox", alias="QUICKBOOKS_ENVIRONMENT")
    # QuickBooks' free Builder tier: 500,000 "CorePlus" (read) calls a MONTH for
    # the whole app; past that, every client is BLOCKED until next month. The
    # app stops at 90% (450,000), caps each company at a daily share, and
    # allows 15 companies — 15 x 1,000 x 30 days = 450,000, so even if every
    # company used its full share every day, the month would still fit.
    quickbooks_max_connections: int = Field(default=15, alias="QUICKBOOKS_MAX_CONNECTIONS")
    quickbooks_daily_call_limit: int = Field(default=1000, alias="QUICKBOOKS_DAILY_CALL_LIMIT")
    quickbooks_monthly_call_limit: int = Field(default=500_000, alias="QUICKBOOKS_MONTHLY_CALL_LIMIT")
    xero_client_id: str = Field(default="", alias="XERO_CLIENT_ID")
    xero_client_secret: str = Field(default="", alias="XERO_CLIENT_SECRET")
    # Xero's plan limits (developer.xero.com/pricing). Starter (free): 5
    # connected organizations and 1,000 API calls per organization per day.
    # The app refuses a 6th connection and stops calling an organization at
    # 90% of its daily limit (api/services/business_tools.py CallMeter).
    # Raise both if you move to a paid Xero plan (Core: 50 and 5,000).
    xero_max_connections: int = Field(default=5, alias="XERO_MAX_CONNECTIONS")
    xero_daily_call_limit: int = Field(default=1000, alias="XERO_DAILY_CALL_LIMIT")
    # Wave (developer.waveapps.com -> Manage Applications): the business must be
    # on Wave Pro or Advisors to connect. Wave allows about 60 requests a minute
    # per connection and 5,000 a day for the whole app, so the app caps each
    # business at WAVE_DAILY_CALL_LIMIT a day and allows WAVE_MAX_CONNECTIONS
    # businesses: 9 x 500 = 4,500 — under 90% of the 5,000 even if all are busy.
    wave_client_id: str = Field(default="", alias="WAVE_CLIENT_ID")
    wave_client_secret: str = Field(default="", alias="WAVE_CLIENT_SECRET")
    # The read-only permissions asked for (Wave's "resource:operation" scopes).
    wave_scopes: str = Field(default="business:read customer:read invoice:read product:read user:read",
                             alias="WAVE_SCOPES")
    wave_max_connections: int = Field(default=9, alias="WAVE_MAX_CONNECTIONS")
    wave_daily_call_limit: int = Field(default=500, alias="WAVE_DAILY_CALL_LIMIT")
    supabase_oauth_client_id: str = Field(default="", alias="SUPABASE_OAUTH_CLIENT_ID")
    supabase_oauth_client_secret: str = Field(default="", alias="SUPABASE_OAUTH_CLIENT_SECRET")
    # This API's public address, for the redirect URI. Render sets
    # RENDER_EXTERNAL_URL automatically, so on Render nothing needs to be set.
    public_api_url: str = Field(default="", alias="PUBLIC_API_URL")
    render_external_url: str = Field(default="", alias="RENDER_EXTERNAL_URL")

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
    def admin_email_list(self) -> list[str]:
        """
        ADMIN_EMAILS as a clean lowercase list, default consultant first.

        Splits the comma-separated setting, trims spaces around each address,
        lowercases it (so sign-in matching ignores case) and drops empty pieces
        left by stray or trailing commas. Order is kept, so index 0 is the
        default consultant that new "do it for me" clients are assigned to.

        Returns:
            list[str]: admin email addresses, lowercase, in the order written;
            an empty list when ADMIN_EMAILS is not set.

        Example:
            >>> Settings(admin_emails=" Ana@Example.com, ,bo@example.com,").admin_email_list
            ['ana@example.com', 'bo@example.com']
        """
        return [e.strip().lower() for e in self.admin_emails.split(",") if e.strip()]

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

    @property
    def api_base_url(self) -> str:
        """
        This API's public address (no trailing "/"): PUBLIC_API_URL, else
        Render's own RENDER_EXTERNAL_URL, else the local dev server.

        Example:
            >>> Settings(render_external_url="https://dcp-api.onrender.com/").api_base_url
            'https://dcp-api.onrender.com'
        """
        return (self.public_api_url or self.render_external_url or "http://localhost:8000").rstrip("/")


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
