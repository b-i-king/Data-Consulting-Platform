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
--   * consultations the free first call and paid sessions (Cal.com webhook,
--                   or marked by hand by the consultant)
--   * storage_consents  a client's "yes, store my data" (Data Engineering)
--   * load_runs     one row per load into the warehouse (metadata only)
--   * intake_responses  optional, anonymous answers to the guest questionnaire
--                       (helps you see which data sources people use)
--   Full design, ER diagram and planned tables: docs/DATABASE.md
--
-- WHAT IS *NOT* STORED (in this file's tables)
--   Client data. Uploaded files are processed in memory and discarded. The
--   only exception is managed storage: a client who consents can load cleaned
--   data into the separate warehouse schema (db/warehouse.sql).
--   Passwords: Supabase Auth keeps them; local mode keeps only a one-way hash.
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

-- Passwords (api/services/auth.py). Exactly one is set once the person has a
-- password; both stay NULL for accounts made FOR someone (a team colleague, or
-- a client a consultant added) until their first sign-in sets one.
--   auth_user_id   Supabase Auth user id (auth.users.id) — Supabase mode. The
--                  password itself lives in Supabase, never in this table.
--   password_hash  one-way scrypt hash — local mode (no Supabase configured).
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS auth_user_id UUID UNIQUE;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS password_hash TEXT;

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

-- ── Consultations (the free first call, then paid sessions) ─────────────────
-- One row per consultation booked through Cal.com (written by the Cal.com
-- webhook) or entered by hand by the consultant. The FREE consultation counts
-- as used only when a consultant marks one 'completed' — after that the
-- client is sent to PAID_BOOKING_URL ($80/hour). See api/services/consultations.py.
CREATE TABLE IF NOT EXISTS consultations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The plan holder (individual or team owner). NULL when someone booked
    -- with an email that has no account yet; matched later by attendee_email.
    account_id          UUID REFERENCES accounts (id) ON DELETE SET NULL,
    attendee_email      TEXT,                      -- lowercase
    attendee_name       TEXT,
    kind                TEXT NOT NULL CHECK (kind IN ('free', 'paid')),
    -- booked    on the calendar (also after a reschedule)
    -- canceled  canceled or rejected in Cal.com, or by the consultant
    -- ended     Cal.com says the meeting ended — waiting for your confirmation
    -- completed YOU confirmed it happened (this is what uses up the free call)
    -- no_show   the client didn't come (the free call is NOT used up)
    status              TEXT NOT NULL CHECK (status IN ('booked', 'canceled', 'ended', 'completed', 'no_show')),
    source              TEXT NOT NULL CHECK (source IN ('calcom', 'manual')),
    calcom_booking_uid  TEXT UNIQUE,               -- Cal.com's booking id (NULL for manual rows)
    starts_at           TIMESTAMPTZ,
    ends_at             TIMESTAMPTZ,
    confirmed_by        UUID REFERENCES consultants (id) ON DELETE SET NULL,
    confirmed_at        TIMESTAMPTZ,
    note                TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_consultations_account ON consultations (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_consultations_email ON consultations (attendee_email);

-- ── Managed storage: consent and load history (Data Engineering) ────────────
-- A client's explicit "yes, store my data in the warehouse". Loading into
-- db/warehouse.sql is refused without an active (not withdrawn) row here.
-- See docs/ARCHITECTURE.md -> "Managed storage (opt-in exception)".
CREATE TABLE IF NOT EXISTS storage_consents (
    id            BIGSERIAL PRIMARY KEY,
    account_id    UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,   -- plan holder
    consented_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    withdrawn_at  TIMESTAMPTZ,              -- set -> delete their warehouse rows within 30 days
    terms_version TEXT NOT NULL,            -- which storage terms they agreed to
    consented_by  UUID REFERENCES accounts (id) ON DELETE SET NULL   -- who clicked (client, or consultant acting)
);
CREATE INDEX IF NOT EXISTS idx_storage_consents_account ON storage_consents (account_id);

-- One row per load into the warehouse: what was loaded, how many rows, and
-- whether it worked. Metadata only (the rows themselves are in warehouse.*).
CREATE TABLE IF NOT EXISTS load_runs (
    id                BIGSERIAL PRIMARY KEY,
    account_id        UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,   -- plan holder = warehouse client_id
    source            TEXT NOT NULL DEFAULT 'file_upload',   -- 'file_upload', later 'quickbooks', ...
    source_name       TEXT NOT NULL,                         -- e.g. 'sales.csv'
    event_type        TEXT NOT NULL,                         -- e.g. 'transaction/income'
    rows_in           INTEGER NOT NULL DEFAULT 0,            -- rows in the cleaned table
    rows_loaded       INTEGER NOT NULL DEFAULT 0,            -- new fact_event rows
    rows_skipped      INTEGER NOT NULL DEFAULT 0,            -- no date, or already loaded
    plan_fingerprint  TEXT,                                  -- the preview the person accepted
    status            TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error             TEXT,
    started_by        UUID REFERENCES accounts (id) ON DELETE SET NULL,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_load_runs_account ON load_runs (account_id, started_at DESC);

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
ALTER TABLE consultations    ENABLE ROW LEVEL SECURITY;
ALTER TABLE storage_consents ENABLE ROW LEVEL SECURITY;
ALTER TABLE load_runs        ENABLE ROW LEVEL SECURITY;
