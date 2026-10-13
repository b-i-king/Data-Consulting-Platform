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
--   * usage_events  one row per download, for runtime-based billing
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
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ                -- NULL until an invited member first signs in
);

-- Columns added after v0.1 (safe to re-run on an existing database).
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS industry_supersector TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS naics_sector TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS naics_subsector TEXT;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMPTZ;
ALTER TABLE accounts ADD COLUMN IF NOT EXISTS company_size TEXT;
CREATE INDEX IF NOT EXISTS idx_accounts_naics ON accounts (naics_sector, naics_subsector);

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

CREATE INDEX IF NOT EXISTS idx_usage_events_account ON usage_events (account_id, created_at DESC);

-- ── Services on the plan ─────────────────────────────────────────────────────
-- Which services an account (an individual, or a business team's OWNER) has.
-- Team members share their owner's services. Drives the signed-in workspace:
-- active services are open in the left pane, the rest are grayed out.
-- PLACEHOLDER UNTIL PAYMENTS: services chosen at signup (source 'signup') or
-- added in the workspace (source 'added') count as active. When Stripe is
-- added, only rows with source 'payment' should unlock paid services.
CREATE TABLE IF NOT EXISTS account_services (
    account_id  UUID NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    service_id  TEXT NOT NULL,                 -- ids from api/services/pricing.py
    status      TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'canceled')),
    source      TEXT NOT NULL DEFAULT 'signup' CHECK (source IN ('signup', 'added', 'payment')),
    added_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (account_id, service_id)
);

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
-- The FastAPI backend connects with the database password (bypasses RLS), so
-- these policies only matter if you later query Supabase directly from the
-- browser. Enabling RLS with no policies = "deny everything" by default,
-- which is the safe starting point.
ALTER TABLE accounts         ENABLE ROW LEVEL SECURITY;
ALTER TABLE teams            ENABLE ROW LEVEL SECURITY;
ALTER TABLE usage_events     ENABLE ROW LEVEL SECURITY;
ALTER TABLE intake_responses ENABLE ROW LEVEL SECURITY;
