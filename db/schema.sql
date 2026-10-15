-- ============================================================================
-- Data Consulting Platform — Supabase / PostgreSQL schema
-- ============================================================================
-- HOW TO APPLY
--   Supabase dashboard -> SQL Editor -> paste this file -> Run.
--   (Or: psql "$DATABASE_URL" -f db/schema.sql)
--
-- WHAT IS STORED
--   * accounts      people who signed up (individual or business)
--   * teams         one per business; accounts.team_id links members to it
--   * consultants   the people who do the work for clients (a "dim" table);
--                   each signs in with their own account
--   * usage_events  one row per download, for runtime-based billing
--   * subscription_plans  what you sell (= Stripe products): each tool, and
--                   the All tools bundle, with a monthly and an annual price
--   * subscriptions one row per client subscription, kept in sync by Stripe
--   * subscription_items  the tools in each subscription ("build your own")
--   * consulting_purchases  prepaid consulting hours bought through Stripe
--   Full design, ER diagram and planned tables: docs/DATABASE.md
--   * intake_responses  optional, anonymous answers to the guest questionnaire
--                       (helps you see which data sources people use)
--
-- WHAT IS *NOT* STORED
--   Client data. Uploaded files are processed in memory and discarded.
--
-- Safe to re-run: every statement uses IF NOT EXISTS.
-- ============================================================================

-- gen_random_uuid() comes from pgcrypto (enabled by default on Supabase).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── Accounts ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS accounts (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_type  TEXT NOT NULL CHECK (account_type IN ('individual', 'business')),
    first_name    TEXT NOT NULL,
    last_name     TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,          -- stored lowercase by the API
    company_name  TEXT,                          -- business accounts only
    -- The "team column": NULL for individuals; set for business owners and
    -- the colleagues they add. Foreign key is added below (teams references
    -- accounts too, so one of them has to be added after both exist).
    team_id       UUID,
    team_role     TEXT CHECK (team_role IN ('owner', 'member')),
    status        TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invited', 'disabled')),
    -- Industry (business accounts; team members copy the owner's values).
    -- Asked in the funnel: goods vs. services -> NAICS sector -> subsector.
    industry_supersector TEXT CHECK (industry_supersector IN ('goods_producing', 'service_providing')),
    naics_sector         TEXT,               -- e.g. '44-45' (Retail Trade)
    naics_subsector      TEXT,               -- e.g. '445'   (Food and Beverage Retailers)
    -- Company size (business accounts): LinkedIn size code, 'A' = self-employed,
    -- 'B' = 1-10 employees ... 'I' = 10,001+ (see api/services/intake.py).
    company_size         TEXT CHECK (company_size IN ('A','B','C','D','E','F','G','H','I')),
    -- Optional location, chosen from the signup's city picker (GeoNames).
    location_geoname_id  INTEGER,            -- e.g. 4180439
    location_city        TEXT,               -- 'Atlanta'
    location_region      TEXT,               -- 'Georgia' (US states only)
    location_country_code TEXT,              -- 'US' (ISO 3166-1)
    location_label       TEXT,               -- 'Atlanta, Georgia, United States'
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ                -- NULL until an invited member first signs in
);

-- Columns added after v0.1 (safe to re-run on an existing database).
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS industry_supersector TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS naics_sector TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS naics_subsector TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS company_size TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS location_geoname_id INTEGER;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS location_city TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS location_region TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS location_country_code TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS location_label TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS industry_other TEXT;   -- "Other": what they typed (no NAICS match)
CREATE INDEX IF NOT EXISTS idx_accounts_naics ON accounts (naics_sector, naics_subsector);

-- ── Consultants (dimension table) ───────────────────────────────────────────
-- One row per consultant. A consultant signs in with a normal account (the
-- email must be listed in CONSULTANT_EMAILS and they must enter
-- CONSULTANT_ACCESS_CODE); the API creates this row on their first sign-in.
CREATE TABLE IF NOT EXISTS consultants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id    UUID NOT NULL UNIQUE REFERENCES accounts (id) ON DELETE CASCADE,
    display_name  TEXT NOT NULL,                 -- shown to clients: "Brandon"
    email         TEXT NOT NULL UNIQUE,          -- lowercase
    is_default    BOOLEAN NOT NULL DEFAULT false,   -- new "do it for me" clients go here
    active        BOOLEAN NOT NULL DEFAULT true,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Which clients want a consultant, and who it is. Set on the account that
-- holds the plan (an individual, or a business's team owner).
--   wants_consultant = false -> consultant_id must be NULL (they do it themselves)
--   wants_consultant = true  -> consultant_id = the assigned consultant (NULL only
--                               until one is assigned, e.g. none configured yet)
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS wants_consultant BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS consultant_id UUID REFERENCES consultants (id) ON DELETE SET NULL;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'accounts_consultant_needs_flag') THEN
        ALTER TABLE accounts ADD CONSTRAINT accounts_consultant_needs_flag
            CHECK (wants_consultant OR consultant_id IS NULL);
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_accounts_consultant ON accounts (consultant_id);

-- Stripe customer id (cus_...), set the first time the account pays.
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS stripe_customer_id TEXT UNIQUE;

-- ── Teams ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS teams (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    owner_account_id  UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- accounts.team_id -> teams.id (added separately because of the circular link).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'accounts_team_id_fkey') THEN
        ALTER TABLE accounts
            ADD CONSTRAINT accounts_team_id_fkey
            FOREIGN KEY (team_id) REFERENCES teams (id) ON DELETE SET NULL;
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_accounts_team_id ON accounts (team_id);

-- ── Usage (billing) ─────────────────────────────────────────────────────────
-- One row each time a signed-in user downloads cleaned data.
CREATE TABLE IF NOT EXISTS usage_events (
    id                BIGSERIAL PRIMARY KEY,
    account_id        UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    dataset_name      TEXT NOT NULL,             -- e.g. "Transactions (partitioned by year)"
    row_count         INTEGER NOT NULL,
    runtime_seconds   NUMERIC(10, 3) NOT NULL,   -- measured processing time
    billable_minutes  NUMERIC(10, 2) NOT NULL,   -- rounded up to the billing increment
    amount_usd        NUMERIC(10, 2) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Set when a consultant did the download on the client's behalf.
ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS consultant_id UUID REFERENCES consultants (id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_usage_events_account ON usage_events (account_id, created_at DESC);
-- Fair use (api/services/fair_use.py): a one-way SHA-256 fingerprint of the
-- downloaded table. It can't be turned back into data; it only tells us when the
-- same account downloads the exact same result again (not charged twice).
ALTER TABLE usage_events ADD COLUMN IF NOT EXISTS data_fingerprint TEXT;
CREATE INDEX IF NOT EXISTS idx_usage_events_fingerprint ON usage_events (account_id, data_fingerprint);

-- ── Project interests ────────────────────────────────────────────────────────
-- Services a client said they'd like a consultant's help with ("Projects with
-- a consultant" on Your needs). INTEREST ONLY — these rows do not unlock any
-- tool; tools unlock through a subscription (tables subscriptions +
-- subscription_items), consultant management, or being a consultant.
-- (Table name kept from v0.1.)
CREATE TABLE IF NOT EXISTS account_services (
    account_id  UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    service_id  TEXT NOT NULL,                 -- ids from api/services/pricing.py
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'canceled')),
    source      TEXT NOT NULL DEFAULT 'signup' CHECK (source IN ('signup', 'added', 'payment')),
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, service_id)
);

-- ── Subscription plans (= Stripe products) ───────────────────────────────────
-- One row per thing you sell: each TOOL (Data Cleaning, Dashboards, ...) and
-- the All tools BUNDLE. Each is a Stripe PRODUCT with two PRICES: monthly and
-- annual. Clients build their own package from the tools; the total is the
-- sum of the tools' prices. The rows are written by `python scripts/sync_stripe.py`, which
-- reads the plans from api/services/pricing.py (SUBSCRIPTIONS), creates or
-- updates them in Stripe, and stores the Stripe ids here.
CREATE TABLE IF NOT EXISTS subscription_plans (
    id                       TEXT PRIMARY KEY,          -- 'all_tools', 'data_visualization', ...
    name                     TEXT NOT NULL,             -- 'All tools'
    description              TEXT,
    kind                     TEXT NOT NULL DEFAULT 'tool' CHECK (kind IN ('tool', 'bundle')),
    is_base                  BOOLEAN NOT NULL DEFAULT false,   -- Data Cleaning: in every package
    requires                 TEXT[] NOT NULL DEFAULT '{}',     -- AI: '{data_engineering}' 
    service_ids              TEXT[] NOT NULL DEFAULT '{}',   -- services it unlocks ('{}' = every service)
    monthly_price_usd        NUMERIC(10, 2) NOT NULL,
    annual_price_usd         NUMERIC(10, 2) NOT NULL,
    stripe_product_id        TEXT UNIQUE,               -- prod_...
    stripe_monthly_price_id  TEXT UNIQUE,               -- price_... (lookup key dcp_<id>_month)
    stripe_annual_price_id   TEXT UNIQUE,               -- price_... (lookup key dcp_<id>_year)
    active                   BOOLEAN NOT NULL DEFAULT true,
    sort_order               INTEGER NOT NULL DEFAULT 0,
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Subscriptions (kept in sync by Stripe) ──────────────────────────────────
-- One row per subscription a client starts; its tools are in
-- subscription_items. Written by the Stripe webhook (api/services/billing.py)
-- — never edited by hand. Held by the account that holds the plan (an
-- individual, or a business's team owner). All items share one billing interval.
CREATE TABLE IF NOT EXISTS subscriptions (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id               UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    billing_interval         TEXT NOT NULL CHECK (billing_interval IN ('month', 'year')),
    -- Stripe's statuses. 'active' and 'trialing' unlock the plan; 'past_due'
    -- keeps it on while Stripe retries the card; the rest switch it off.
    status                   TEXT NOT NULL CHECK (status IN ('active', 'trialing', 'past_due', 'unpaid',
                                                             'canceled', 'incomplete', 'incomplete_expired',
                                                             'paused')),
    cancel_at_period_end     BOOLEAN NOT NULL DEFAULT false,   -- "canceled, but paid through ..."
    current_period_end       TIMESTAMPTZ,
    stripe_subscription_id   TEXT UNIQUE,               -- sub_... (NULL in placeholder mode)
    stripe_customer_id       TEXT,                      -- cus_...
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_account ON subscriptions (account_id, status);

-- The tools (or the bundle) in each subscription — one row per Stripe
-- subscription item. Replaced as a whole whenever the subscription changes.
CREATE TABLE IF NOT EXISTS subscription_items (
    subscription_id          UUID NOT NULL REFERENCES subscriptions (id) ON DELETE CASCADE,
    plan_id                  TEXT NOT NULL REFERENCES subscription_plans (id),
    stripe_subscription_item_id TEXT UNIQUE,            -- si_... (NULL in test mode)
    PRIMARY KEY (subscription_id, plan_id)
);

-- ── Consulting hours (prepaid through Stripe) ───────────────────────────────
-- The first 30-minute consultation is free (except Data Strategy & Discovery);
-- after that, clients prepay hours at CONSULTING_RATE_PER_HOUR_USD. One row
-- per paid Stripe Checkout, written by the webhook.
CREATE TABLE IF NOT EXISTS consulting_purchases (
    id                       BIGSERIAL PRIMARY KEY,
    account_id               UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    service_id               TEXT,                      -- what it's for, e.g. 'data_strategy_discovery'
    hours                    NUMERIC(6, 2) NOT NULL CHECK (hours > 0),
    rate_per_hour_usd        NUMERIC(10, 2) NOT NULL,
    amount_usd               NUMERIC(10, 2) NOT NULL,
    stripe_checkout_session_id TEXT UNIQUE,             -- cs_... (NULL in placeholder mode)
    status                   TEXT NOT NULL DEFAULT 'paid' CHECK (status IN ('paid', 'refunded')),
    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_consulting_purchases_account ON consulting_purchases (account_id, created_at DESC);

-- ── Intake responses (optional analytics, not used by the API yet) ─────────
-- Anonymous answers to the guest questionnaire. Useful for deciding which
-- connector (Snowflake? QuickBooks?) to build next.
CREATE TABLE IF NOT EXISTS intake_responses (
    id               BIGSERIAL PRIMARY KEY,
    account_type     TEXT,                       -- individual / business
    data_location    TEXT,                       -- flat_files, cloud_storage, business_apps...
    location_option  TEXT,                       -- snowflake, quickbooks...
    services         TEXT[],                     -- selected service ids
    account_id       UUID REFERENCES accounts (id) ON DELETE SET NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ── Data connections (connectors such as QuickBooks) ────────────────────────
-- One row per connector a client has set up (Data Engineering). Holds only the
-- SCHEDULE and status — the pulled data itself goes to the warehouse schema
-- (db/warehouse.sql). refresh_frequency ids and prices: api/services/pricing.py
-- REFRESH_OPTIONS; rules: api/services/connections.py.
CREATE TABLE IF NOT EXISTS data_connections (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id         UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,     -- the client (plan holder)
    consultant_id      UUID REFERENCES consultants (id) ON DELETE SET NULL,          -- who set it up, if a consultant
    connector          TEXT NOT NULL,                       -- 'quickbooks', 'gusto', ...
    refresh_frequency  TEXT NOT NULL DEFAULT 'daily'
                       CHECK (refresh_frequency IN ('manual', 'weekly', 'daily', 'hourly', 'live')),
    status             TEXT NOT NULL DEFAULT 'pending'      -- pending = schedule chosen, not connected yet
                       CHECK (status IN ('pending', 'connected', 'paused', 'error', 'disconnected')),
    last_synced_at     TIMESTAMPTZ,                         -- last successful pull
    next_sync_at       TIMESTAMPTZ,                         -- when the scheduler pulls next (NULL = manual)
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, connector)                          -- one connection per connector per client
);
CREATE INDEX IF NOT EXISTS idx_data_connections_due ON data_connections (next_sync_at) WHERE status = 'connected';

-- ── Row Level Security ──────────────────────────────────────────────────────
-- WHO CAN SEE WHAT is enforced by the API (api/services/consultants.py):
--   * a client sees only their own account (and team)
--   * a consultant sees only the clients assigned to them (accounts.consultant_id)
-- The FastAPI backend connects with the database password (bypasses RLS), so
-- these policies only matter if you later query Supabase directly from the
-- browser. Enabling RLS with no policies = "deny everything" by default,
-- which is the safe starting point.
ALTER TABLE accounts         ENABLE ROW LEVEL SECURITY;
ALTER TABLE consultants      ENABLE ROW LEVEL SECURITY;
ALTER TABLE account_services ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_plans ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscriptions    ENABLE ROW LEVEL SECURITY;
ALTER TABLE subscription_items ENABLE ROW LEVEL SECURITY;
ALTER TABLE consulting_purchases ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams            ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events     ENABLE ROW LEVEL SECURITY;
ALTER TABLE intake_responses ENABLE ROW LEVEL SECURITY;
ALTER TABLE data_connections ENABLE ROW LEVEL SECURITY;
