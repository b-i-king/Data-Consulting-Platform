-- ============================================================================
-- Data Consulting Platform — the whole database (Supabase / PostgreSQL)
-- ============================================================================
-- HOW TO APPLY
--   python scripts/setup_database.py          (runs this file, then lists every table)
--   or: Supabase dashboard -> SQL Editor -> paste this file -> Run.
--
-- TWO SCHEMAS (a schema is a folder of tables inside one database)
--   dim   THE PLATFORM: who signed up, consultants, teams, what each account
--         pays for, consultations, download billing, storage consent and the
--         load log. Small tables that describe people and accounts.
--   fact  CLIENT DATA: only for Data Engineering clients who said "yes, store
--         my data". One table, account_data, holds every client's data in
--         one shape (the unified data design), plus field_map, the shared
--         lookup that lines up field names across sources.
--   (Supabase keeps its own sign-in tables in its built-in `auth` schema.
--    Passwords live there, never here.)
--
--   dim.accounts ──< dim.subscriptions        what each account pays for
--        │      ──< dim.consultations         free first call, then paid sessions
--        │      ──< dim.usage_events          one row per download (billing)
--        │      ──< dim.storage_consents      "yes, store my data"
--        │      ──< dim.load_runs             one row per load into fact.account_data
--        │      ──< dim.data_connections      integrations: QuickBooks (schedule), a client's own
--        │                                    Supabase/PostgreSQL database (read-only, encrypted login)
--        │      ──< dim.dashboard_tabs        the client's own dashboard tabs (saved questions)
--        │      ──< dim.question_log          questions asked of the data (to suggest dashboard tabs)
--        │      ──< fact.account_data         the stored data (one row per metric)
--        │      ──< fact.entities             each customer / vendor / item / location, ONCE
--        │                                    ──< fact.entity_sources  how each source refers to it
--        ├─ team_id ──> dim.teams             businesses: owner + colleagues
--        └─ consultant_id ──> dim.consultants who manages this client
--   fact.field_map                            shared lookup, not per account
--
-- WHAT IS *NOT* STORED
--   Uploaded files. They are cleaned in memory and discarded — except the
--   cleaned rows a consenting Data Engineering client loads into fact.account_data.
--   Passwords: Supabase Auth keeps them; local mode keeps only a one-way hash.
--
-- Full design and ER diagram: docs/DATABASE.md (dim) and docs/DATA_MODEL.md (fact).
-- Safe to re-run: every statement uses IF NOT EXISTS / ON CONFLICT.
-- ============================================================================

-- gen_random_uuid() comes from pgcrypto (enabled by default on Supabase).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE SCHEMA IF NOT EXISTS dim;
CREATE SCHEMA IF NOT EXISTS fact;


-- ############################################################################
-- dim: the platform
-- ############################################################################

-- ── dim.accounts: everyone who signed up ────────────────────────────────────
-- Clients (individual or business) AND consultants: a consultant is a normal
-- account with a dim.consultants row. The account that holds a plan is an
-- individual, or a business's team OWNER (colleagues share the owner's plan).
CREATE TABLE IF NOT EXISTS dim.accounts (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_type          TEXT NOT NULL CHECK (account_type IN ('individual', 'business')),
    first_name            TEXT NOT NULL,
    last_name             TEXT NOT NULL,
    email                 TEXT NOT NULL UNIQUE,      -- stored lowercase by the API
    company_name          TEXT,                      -- business accounts only
    -- The "team column": NULL for individuals; set for business owners and the
    -- colleagues they add. (Foreign key added below: teams points back here.)
    team_id               UUID,
    team_role             TEXT CHECK (team_role IN ('owner', 'member')),
    status                TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'invited', 'disabled')),
    -- Passwords (api/services/auth.py). One of the two is set once the person
    -- has a password; both stay NULL for an account made FOR someone (a
    -- colleague, or a client a consultant added) until their first sign-in.
    auth_user_id          UUID UNIQUE,               -- Supabase Auth user id (auth.users.id)
    password_hash         TEXT,                      -- one-way scrypt hash (local mode only)
    -- Industry, asked in the funnel: goods vs. services -> NAICS sector -> subsector.
    industry_supersector  TEXT CHECK (industry_supersector IN ('goods_producing', 'service_providing')),
    naics_sector          TEXT,                      -- e.g. '44-45' (Retail Trade)
    naics_subsector       TEXT,                      -- e.g. '445'   (Food and Beverage Retailers)
    industry_other        TEXT,                      -- "Other": what they typed (no NAICS match)
    -- Company size: LinkedIn code, 'A' = self-employed, 'B' = 1-10 ... 'I' = 10,001+.
    company_size          TEXT CHECK (company_size IN ('A','B','C','D','E','F','G','H','I')),
    -- Optional location, chosen from the signup's city picker (GeoNames).
    location_geoname_id   INTEGER,                   -- e.g. 4180439
    location_city         TEXT,                      -- 'Atlanta'
    location_region       TEXT,                      -- 'Georgia' (US states only)
    location_country_code TEXT,                      -- 'US' (ISO 3166-1)
    location_label        TEXT,                      -- 'Atlanta, Georgia, United States'
    -- Consultant: wants_consultant = false -> consultant_id must be NULL (they
    -- do it themselves); true -> the assigned consultant (NULL until assigned).
    wants_consultant      BOOLEAN NOT NULL DEFAULT false,
    consultant_id         UUID,                      -- -> dim.consultants (foreign key below)
    -- How the consultant helps (chosen on "Your needs"): 'one_time' = one
    -- analysis of everything they sent; 'monthly' = an ongoing monthly
    -- engagement. NULL when they do it themselves.
    consultant_plan       TEXT CHECK (consultant_plan IN ('one_time', 'monthly')),
    -- Roles. is_admin: an ADMIN_EMAILS address (set by the API at sign-in);
    -- admins approve consultants. is_consultant: an approved consultant (the
    -- API also creates their dim.consultants row). Nobody becomes a consultant
    -- on their own: they ASK (consultant_request = 'pending') and an admin
    -- approves ('approved') or denies ('denied') — api/services/consultants.py.
    is_admin                BOOLEAN NOT NULL DEFAULT false,
    is_consultant           BOOLEAN NOT NULL DEFAULT false,
    consultant_request      TEXT CHECK (consultant_request IN ('pending', 'approved', 'denied')),
    consultant_requested_at TIMESTAMPTZ,
    consultant_decided_at   TIMESTAMPTZ,
    consultant_decided_by   UUID,                    -- the admin's account id
    -- Terms of Service + Privacy Policy, ticked at sign-up (NULL for an account
    -- made FOR someone, until they sign up themselves).
    terms_accepted_at     TIMESTAMPTZ,
    terms_version         TEXT,                      -- e.g. '2026-10-15' (web/app/terms)
    -- Projects they'd like a consultant's help with ("Projects with a
    -- consultant" on Your needs). INTEREST ONLY: unlocks nothing; tools unlock
    -- through dim.subscriptions. Service ids from api/services/pricing.py.
    interested_services   TEXT[] NOT NULL DEFAULT '{}',
    stripe_customer_id    TEXT UNIQUE,               -- cus_..., set the first time they pay
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at         TIMESTAMPTZ,               -- NULL until an invited member first signs in
    CHECK (wants_consultant OR consultant_id IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_accounts_team       ON dim.accounts (team_id);
CREATE INDEX IF NOT EXISTS idx_accounts_consultant ON dim.accounts (consultant_id);
CREATE INDEX IF NOT EXISTS idx_accounts_naics      ON dim.accounts (naics_sector, naics_subsector);

-- ── dim.consultants: the people who do the work for clients ─────────────────
-- A consultant signs in with a normal account. The API creates this row when
-- an admin APPROVES their request (or, for an admin, at sign-in) — you never
-- insert it by hand.
-- capacity: how many clients they take at once (they set it in the
-- workspace, 1 to 10; default 3). Clients pick a consultant by name and see
-- how full they are; a full consultant can't be picked.
-- Cal.com, per consultant (Settings -> "Your booking links"): their FREE
-- 30-minute link and their PAID session link (paid through their own Cal.com
-- + Stripe, so they get paid directly). Their clients see these links instead
-- of BOOKING_URL / PAID_BOOKING_URL. calcom_webhook_secret: made by the app,
-- pasted by the consultant into Cal.com -> Settings -> Developer -> Webhooks,
-- with the subscriber URL /api/consultations/calcom-webhook/<consultant id>.
CREATE TABLE IF NOT EXISTS dim.consultants (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id    UUID NOT NULL UNIQUE REFERENCES dim.accounts (id) ON DELETE CASCADE,
    display_name  TEXT NOT NULL,                     -- shown to clients: "Brandon"
    email         TEXT NOT NULL UNIQUE,              -- lowercase
    is_default    BOOLEAN NOT NULL DEFAULT false,    -- "do it for me" clients who pick nobody go here
    active        BOOLEAN NOT NULL DEFAULT true,
    capacity      INTEGER NOT NULL DEFAULT 3 CHECK (capacity BETWEEN 1 AND 10),   -- MAX_CAPACITY in consultants.py
    free_booking_url      TEXT,                      -- e.g. 'https://cal.com/kim/30min' (free first consultation)
    paid_booking_url      TEXT,                      -- e.g. 'https://cal.com/kim/session' (paid on Cal.com)
    calcom_webhook_secret TEXT,                      -- signs this consultant's Cal.com webhook (NULL = not set up)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Upgrading a database made before these columns existed (CREATE TABLE IF NOT
-- EXISTS leaves an existing table as it is, so new columns are added here).
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS consultant_plan TEXT
    CHECK (consultant_plan IN ('one_time', 'monthly'));
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS is_consultant BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS consultant_request TEXT
    CHECK (consultant_request IN ('pending', 'approved', 'denied'));
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS consultant_requested_at TIMESTAMPTZ;
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS consultant_decided_at TIMESTAMPTZ;
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS consultant_decided_by UUID;
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ;
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS terms_version TEXT;
-- "Forgot password" when Supabase Auth is off (local mode): a one-way hash of
-- the emailed token and when it stops working. Supabase mode keeps its own.
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS password_reset_hash TEXT;
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS password_reset_expires_at TIMESTAMPTZ;
-- Business context for dashboards (Account -> "About your business"):
-- {"about": "...", "goals": "...", "key_questions": ["...", ...]}.
ALTER TABLE dim.accounts ADD COLUMN IF NOT EXISTS business_context JSONB NOT NULL DEFAULT '{}';
ALTER TABLE dim.consultants ADD COLUMN IF NOT EXISTS capacity INTEGER NOT NULL DEFAULT 3
    CHECK (capacity BETWEEN 1 AND 10);
ALTER TABLE dim.consultants ADD COLUMN IF NOT EXISTS free_booking_url TEXT;
ALTER TABLE dim.consultants ADD COLUMN IF NOT EXISTS paid_booking_url TEXT;
ALTER TABLE dim.consultants ADD COLUMN IF NOT EXISTS calcom_webhook_secret TEXT;
-- The admin's to-do list: who is waiting for a decision.
CREATE INDEX IF NOT EXISTS idx_accounts_consultant_request ON dim.accounts (consultant_request)
    WHERE consultant_request = 'pending';

-- ── dim.teams: one per business ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dim.teams (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name              TEXT NOT NULL,
    owner_account_id  UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The two links that point "backwards" (accounts was created first).
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'accounts_team_id_fkey') THEN
        ALTER TABLE dim.accounts ADD CONSTRAINT accounts_team_id_fkey
            FOREIGN KEY (team_id) REFERENCES dim.teams (id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'accounts_consultant_id_fkey') THEN
        ALTER TABLE dim.accounts ADD CONSTRAINT accounts_consultant_id_fkey
            FOREIGN KEY (consultant_id) REFERENCES dim.consultants (id) ON DELETE SET NULL;
    END IF;
END $$;

-- ── dim.subscriptions: what each account pays for ───────────────────────────
-- One row per subscription (a package of tools, or All tools), monthly or
-- yearly. Its tools are in `items`. Written by the Stripe webhook
-- (api/services/billing.py) — or, in test mode (no Stripe key), switched on
-- at once with no charge. Held by the plan holder (individual or team owner).
-- The tools on sale and their prices live in code (api/services/pricing.py)
-- and in Stripe (scripts/sync_stripe.py), so there is no plans table.
CREATE TABLE IF NOT EXISTS dim.subscriptions (
    id                      UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id              UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,
    billing_interval        TEXT NOT NULL CHECK (billing_interval IN ('month', 'year')),
    -- Stripe's statuses. 'active' and 'trialing' unlock the tools; 'past_due'
    -- keeps them on while Stripe retries the card; the rest switch them off.
    status                  TEXT NOT NULL CHECK (status IN ('active', 'trialing', 'past_due', 'unpaid', 'canceled',
                                                            'incomplete', 'incomplete_expired', 'paused')),
    -- The tools: [{"plan_id": "data_visualization", "stripe_subscription_item_id": "si_..."}, ...]
    -- (plan ids from pricing.SUBSCRIPTIONS; the item id is NULL in test mode).
    items                   JSONB NOT NULL DEFAULT '[]',
    cancel_at_period_end    BOOLEAN NOT NULL DEFAULT false,   -- "canceled, but paid through ..."
    current_period_end      TIMESTAMPTZ,
    stripe_subscription_id  TEXT UNIQUE,                      -- sub_... (NULL in test mode)
    stripe_customer_id      TEXT,                             -- cus_...
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_subscriptions_account ON dim.subscriptions (account_id, status);

-- ── dim.consultations: the free first call, then paid sessions ──────────────
-- One row per consultation booked through Cal.com (written by the Cal.com
-- webhook) or entered by hand by the consultant. The FREE consultation counts
-- as used only when a consultant marks one 'completed' — after that the
-- client is sent to PAID_BOOKING_URL ($80/hour, paid on Cal.com).
-- See api/services/consultations.py.
CREATE TABLE IF NOT EXISTS dim.consultations (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    -- The plan holder. NULL when someone booked with an email that has no
    -- account yet; matched later by attendee_email.
    account_id          UUID REFERENCES dim.accounts (id) ON DELETE SET NULL,
    attendee_email      TEXT,                        -- lowercase
    attendee_name       TEXT,
    kind                TEXT NOT NULL CHECK (kind IN ('free', 'paid')),
    -- booked    on the calendar (also after a reschedule)
    -- canceled  canceled or rejected in Cal.com, or by the consultant
    -- ended     Cal.com says the meeting ended — waiting for your confirmation
    -- completed YOU confirmed it happened (this is what uses up the free call)
    -- no_show   the client didn't come (the free call is NOT used up)
    status              TEXT NOT NULL CHECK (status IN ('booked', 'canceled', 'ended', 'completed', 'no_show')),
    source              TEXT NOT NULL CHECK (source IN ('calcom', 'manual')),
    calcom_booking_uid  TEXT UNIQUE,                 -- Cal.com's booking id (NULL for manual rows)
    starts_at           TIMESTAMPTZ,
    ends_at             TIMESTAMPTZ,
    -- Whose calendar it was booked on (the consultant's own webhook), so each
    -- consultant's sessions can be counted and paid. NULL = the shared link.
    consultant_id       UUID REFERENCES dim.consultants (id) ON DELETE SET NULL,
    confirmed_by        UUID REFERENCES dim.consultants (id) ON DELETE SET NULL,
    confirmed_at        TIMESTAMPTZ,
    note                TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_consultations_account ON dim.consultations (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_consultations_email   ON dim.consultations (attendee_email);
ALTER TABLE dim.consultations ADD COLUMN IF NOT EXISTS consultant_id UUID REFERENCES dim.consultants (id) ON DELETE SET NULL;

-- ── dim.usage_events: one row per download (runtime billing) ────────────────
-- Metadata only — never the data itself.
CREATE TABLE IF NOT EXISTS dim.usage_events (
    id                BIGSERIAL PRIMARY KEY,
    account_id        UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,
    consultant_id     UUID REFERENCES dim.consultants (id) ON DELETE SET NULL,  -- set when a consultant downloaded for the client
    dataset_name      TEXT NOT NULL,             -- e.g. "Transactions (partitioned by year)"
    row_count         INTEGER NOT NULL,
    runtime_seconds   NUMERIC(10, 3) NOT NULL,   -- measured processing time
    billable_minutes  NUMERIC(10, 2) NOT NULL,   -- rounded up to the billing increment
    amount_usd        NUMERIC(10, 2) NOT NULL,
    -- Fair use (api/services/fair_use.py): a one-way SHA-256 fingerprint of the
    -- downloaded table. It can't be turned back into data; it only tells us when
    -- the same account downloads the exact same result again (not charged twice).
    data_fingerprint  TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_usage_events_account     ON dim.usage_events (account_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_usage_events_fingerprint ON dim.usage_events (account_id, data_fingerprint);

-- ── dim.storage_consents: "yes, store my data" (Data Engineering) ───────────
-- Loading into fact.account_data is refused without an active (not withdrawn)
-- row here. See docs/ARCHITECTURE.md -> "Managed storage (opt-in exception)".
CREATE TABLE IF NOT EXISTS dim.storage_consents (
    id            BIGSERIAL PRIMARY KEY,
    account_id    UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,   -- plan holder
    consented_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    withdrawn_at  TIMESTAMPTZ,              -- set by "delete my stored data"
    terms_version TEXT NOT NULL,            -- which storage terms they agreed to (web/app/storage-terms)
    consented_by  UUID REFERENCES dim.accounts (id) ON DELETE SET NULL   -- who clicked (client, or consultant acting)
);
CREATE INDEX IF NOT EXISTS idx_storage_consents_account ON dim.storage_consents (account_id);

-- ── dim.load_runs: one row per load into fact.account_data ──────────────────
-- What was loaded, how many rows, and whether it worked — the audit trail.
-- column_plan remembers how the file's columns were stored (roles, unified
-- names, metric descriptions), so "Use my stored data" can rebuild the
-- original table with the client's own column names.
CREATE TABLE IF NOT EXISTS dim.load_runs (
    id                BIGSERIAL PRIMARY KEY,
    account_id        UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,   -- plan holder
    source            TEXT NOT NULL DEFAULT 'file_upload',   -- 'file_upload', later 'quickbooks', ...
    source_name       TEXT NOT NULL,                         -- e.g. 'sales.csv'
    event_type        TEXT NOT NULL,                         -- e.g. 'transaction/income'
    rows_in           INTEGER NOT NULL DEFAULT 0,            -- rows in the cleaned table
    rows_loaded       INTEGER NOT NULL DEFAULT 0,            -- new account_data rows
    rows_updated      INTEGER NOT NULL DEFAULT 0,            -- stored rows whose content changed
    rows_skipped      INTEGER NOT NULL DEFAULT 0,            -- already stored, unchanged
    column_plan       JSONB,                                 -- [{"column", "role", "field_name", ...}, ...]
    plan_fingerprint  TEXT,                                  -- the preview the person accepted
    status            TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    error             TEXT,
    started_by        UUID REFERENCES dim.accounts (id) ON DELETE SET NULL,
    started_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at       TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_load_runs_account ON dim.load_runs (account_id, started_at DESC);

-- ── dim.data_connections: connectors such as QuickBooks ─────────────────────
-- One row per connector a client has set up (Data Engineering): the SCHEDULE
-- and status only. The pulled data goes to fact.account_data.
-- refresh_frequency ids and prices: api/services/pricing.py REFRESH_OPTIONS.
CREATE TABLE IF NOT EXISTS dim.data_connections (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id         UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,     -- the client (plan holder)
    consultant_id      UUID REFERENCES dim.consultants (id) ON DELETE SET NULL,          -- who set it up, if a consultant
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
CREATE INDEX IF NOT EXISTS idx_data_connections_due ON dim.data_connections (next_sync_at) WHERE status = 'connected';

-- Integrations (Settings -> Integrations): what KIND of source, a name to show,
-- and — for a client's own database — the login, ENCRYPTED with a key derived
-- from JWT_SECRET_KEY (api/services/cloud_sources.py). We only ever read it,
-- read-only; nothing from a cloud database is stored here.
ALTER TABLE dim.data_connections ADD COLUMN IF NOT EXISTS kind TEXT
    CHECK (kind IN ('business_tool', 'cloud_storage'));
ALTER TABLE dim.data_connections ADD COLUMN IF NOT EXISTS display_name TEXT;
ALTER TABLE dim.data_connections ADD COLUMN IF NOT EXISTS credentials_encrypted TEXT;
-- API calls made to this connection TODAY (UTC), so a provider's daily limit
-- is never passed (Xero Starter: 1,000 per organization per day). Starts over
-- each day: api_calls_day says which day api_calls_count belongs to.
ALTER TABLE dim.data_connections ADD COLUMN IF NOT EXISTS api_calls_day DATE;
ALTER TABLE dim.data_connections ADD COLUMN IF NOT EXISTS api_calls_count INTEGER NOT NULL DEFAULT 0;
-- The stored sync's progress (api/services/sync.py): per record type, the newest
-- "last changed" time already stored — the next sync asks only for what changed
-- after it. {"Invoice": "2026-10-16T12:00:00+00:00", ...}
ALTER TABLE dim.data_connections ADD COLUMN IF NOT EXISTS sync_state JSONB NOT NULL DEFAULT '{}';

-- ── dim.api_usage: API calls the whole app made to a tool, per month ────────
-- QuickBooks' free Builder tier allows 500,000 read calls a MONTH for the
-- whole app (all clients together) and blocks everyone past it, so the app
-- counts here and stops at 90% (api/services/business_tools.py CallMeter).
-- One small row per tool per month; never any client data.
CREATE TABLE IF NOT EXISTS dim.api_usage (
    connector  TEXT NOT NULL,                 -- 'quickbooks', 'xero'
    month      DATE NOT NULL,                 -- the 1st of the month (UTC)
    calls      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (connector, month)
);

-- ── dim.dashboard_tabs: the client's own dashboard tabs ─────────────────────
-- The dashboard opens on "Overview" (built from the data automatically). Each
-- extra tab is a list of saved plain-English questions ("total amount by
-- store") answered again on the current data every time it opens — so a tab
-- grows with the business's own questions (api/services/dashboards.py).
CREATE TABLE IF NOT EXISTS dim.dashboard_tabs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id  UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,   -- plan holder
    name        TEXT NOT NULL,                                   -- "Sales by store"
    questions   JSONB NOT NULL DEFAULT '[]',                     -- ["total amount by store", ...]
    position    INTEGER NOT NULL DEFAULT 0,                      -- left-to-right order
    created_by  UUID REFERENCES dim.accounts (id) ON DELETE SET NULL,   -- client or consultant
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_dashboard_tabs_account ON dim.dashboard_tabs (account_id, position);

-- ── dim.question_log: questions asked of the data ──────────────────────────
-- Each plain-English question (AI, "Ask your data") — the TEXT only, never
-- the data. Questions asked again and again become suggested dashboard tabs.
CREATE TABLE IF NOT EXISTS dim.question_log (
    id          BIGSERIAL PRIMARY KEY,
    account_id  UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,   -- plan holder
    question    TEXT NOT NULL,
    answered    BOOLEAN NOT NULL DEFAULT true,                   -- false = "I can't answer that yet"
    asked_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_question_log_account ON dim.question_log (account_id, asked_at DESC);

-- ############################################################################
-- fact: client data (Data Engineering, with consent)
-- ############################################################################

-- ── fact.field_map: line names up across sources, and describe metrics ─────
-- Every source names things differently: QuickBooks "TxnDate", a spreadsheet
-- "order_date". Each row says "in system X, field Y means unified field Z" —
-- and, when Z is a METRIC, what it measures (metric_type / subtype / unit).
-- metric_type NULL = not a metric (an ID, date, name or category), kept as is
-- in account_data.attributes. A field NOT listed here is still stored, under a
-- snake_case version of its own name (mapped = false), so nothing is lost.
-- The unified-metrics skill writes ready-to-run rows for a client's new columns.
-- Paths use dots for nesting and leave out list positions: 'Line.Amount' is
-- the Amount of every item in a QuickBooks Line list.
-- KEEP IN SYNC with QUICKBOOKS_FIELD_MAP / FILE_UPLOAD_FIELD_MAP / XERO_FIELD_MAP in
-- api/services/unified.py (tests/test_unified.py compares them).
CREATE TABLE IF NOT EXISTS fact.field_map (
    field_map_id    SERIAL PRIMARY KEY,
    system          TEXT NOT NULL,           -- 'quickbooks', 'file_upload', ... (= account_data.source_system)
    source_field    TEXT NOT NULL,           -- the source's name or JSON path, e.g. 'CustomerRef.name'
    field_name      TEXT NOT NULL,           -- the unified name, e.g. 'customer_name'
    value_type      TEXT NOT NULL CHECK (value_type IN ('text', 'number', 'date', 'timestamp', 'boolean')),
    metric_type     TEXT,                    -- set = a metric, e.g. 'amount'; NULL = kept as an attribute
    metric_subtype  TEXT,                    -- e.g. 'line', 'total', 'unit' (optional)
    metric_unit     TEXT,                    -- e.g. 'usd', 'count', 'percent'
    notes           TEXT,                    -- plain-words explanation, when the name is not obvious
    UNIQUE (system, source_field),
    CHECK (metric_type IS NULL OR (value_type = 'number' AND metric_unit IS NOT NULL))
);

-- The seed is the source of truth: re-running this file brings existing rows up to date.
INSERT INTO fact.field_map (system, source_field, field_name, value_type, metric_type, metric_subtype, metric_unit, notes) VALUES
    ('quickbooks', 'Id', 'source_record_id', 'text', NULL, NULL, NULL, 'Also part of record_id ("Invoice:130")'),
    ('quickbooks', 'SyncToken', 'sync_token', 'text', NULL, NULL, NULL, 'QuickBooks edit counter, goes up on every save'),
    ('quickbooks', 'TxnDate', 'transaction_date', 'date', NULL, NULL, NULL, 'Also copied to event_date'),
    ('quickbooks', 'DueDate', 'due_date', 'date', NULL, NULL, NULL, NULL),
    ('quickbooks', 'DocNumber', 'reference_number', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'TotalAmt', 'total_amount', 'number', 'amount', 'total', 'usd', 'Whole transaction, tax included'),
    ('quickbooks', 'Balance', 'balance_due', 'number', 'balance', 'due', 'usd', 'Amount still owed (Invoice, Bill)'),
    ('quickbooks', 'UnappliedAmt', 'unapplied_amount', 'number', 'amount', 'unapplied', 'usd', NULL),
    ('quickbooks', 'TxnTaxDetail.TotalTax', 'tax_amount', 'number', 'tax', NULL, 'usd', NULL),
    ('quickbooks', 'CurrencyRef.value', 'currency', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'CurrencyRef.name', 'currency_name', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'ExchangeRate', 'exchange_rate', 'number', 'rate', 'exchange', 'ratio', NULL),
    ('quickbooks', 'CustomerRef.value', 'customer_source_id', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'CustomerRef.name', 'customer_name', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'VendorRef.value', 'vendor_source_id', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'VendorRef.name', 'vendor_name', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'EntityRef.value', 'payee_source_id', 'text', NULL, NULL, NULL, 'Purchase: who was paid (vendor, customer or employee)'),
    ('quickbooks', 'EntityRef.name', 'payee_name', 'text', NULL, NULL, NULL, 'Purchase: who was paid'),
    ('quickbooks', 'EntityRef.type', 'payee_type', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'AccountRef.value', 'payment_account_id', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'AccountRef.name', 'payment_account_name', 'text', NULL, NULL, NULL, 'Purchase: bank or credit card account the money left'),
    ('quickbooks', 'DepositToAccountRef.name', 'deposit_account_name', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'PaymentType', 'payment_type', 'text', NULL, NULL, NULL, 'Purchase: Cash, Check or CreditCard'),
    ('quickbooks', 'PaymentMethodRef.name', 'payment_method_name', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'PaymentRefNum', 'payment_reference', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'DepartmentRef.name', 'location_name', 'text', NULL, NULL, NULL, 'QuickBooks "Location" (store, branch)'),
    ('quickbooks', 'SalesTermRef.name', 'terms', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'PrivateNote', 'memo', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'CustomerMemo.value', 'customer_memo', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'BillEmail.Address', 'customer_email', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'BillAddr.Line1', 'bill_address_line1', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'BillAddr.City', 'bill_city', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'BillAddr.CountrySubDivisionCode', 'bill_state', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'BillAddr.PostalCode', 'bill_postal_code', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'EmailStatus', 'email_status', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'LinkedTxn.TxnId', 'linked_txn_id', 'text', NULL, NULL, NULL, 'e.g. the Payment that paid this Invoice'),
    ('quickbooks', 'LinkedTxn.TxnType', 'linked_txn_type', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'MetaData.CreateTime', 'source_created_at', 'timestamp', NULL, NULL, NULL, NULL),
    ('quickbooks', 'MetaData.LastUpdatedTime', 'source_updated_at', 'timestamp', NULL, NULL, NULL, 'Also copied to source_modified_at'),
    ('quickbooks', 'Line.Id', 'line_source_id', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.LineNum', 'line_num', 'number', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.DetailType', 'line_detail_type', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.Amount', 'line_amount', 'number', 'amount', 'line', 'usd', 'Line total; SubTotalLineDetail lines repeat the sum'),
    ('quickbooks', 'Line.Description', 'line_description', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.SalesItemLineDetail.ItemRef.value', 'item_source_id', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.SalesItemLineDetail.ItemRef.name', 'item_name', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.SalesItemLineDetail.Qty', 'quantity', 'number', 'quantity', NULL, 'count', NULL),
    ('quickbooks', 'Line.SalesItemLineDetail.UnitPrice', 'unit_price', 'number', 'price', 'unit', 'usd', NULL),
    ('quickbooks', 'Line.SalesItemLineDetail.ServiceDate', 'service_date', 'date', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.SalesItemLineDetail.TaxCodeRef.value', 'tax_code', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.SalesItemLineDetail.ItemAccountRef.name', 'income_account_name', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.ItemBasedExpenseLineDetail.ItemRef.value', 'item_source_id', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.ItemBasedExpenseLineDetail.ItemRef.name', 'item_name', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.ItemBasedExpenseLineDetail.Qty', 'quantity', 'number', 'quantity', NULL, 'count', NULL),
    ('quickbooks', 'Line.ItemBasedExpenseLineDetail.UnitPrice', 'unit_price', 'number', 'price', 'unit', 'usd', NULL),
    ('quickbooks', 'Line.ItemBasedExpenseLineDetail.TaxCodeRef.value', 'tax_code', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.AccountBasedExpenseLineDetail.AccountRef.value', 'expense_category_id', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.AccountBasedExpenseLineDetail.AccountRef.name', 'expense_category', 'text', NULL, NULL, NULL, 'The "Category" column in QuickBooks'),
    ('quickbooks', 'Line.AccountBasedExpenseLineDetail.BillableStatus', 'billable_status', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.AccountBasedExpenseLineDetail.TaxCodeRef.value', 'tax_code', 'text', NULL, NULL, NULL, NULL),
    ('quickbooks', 'Line.DiscountLineDetail.DiscountPercent', 'discount_percent', 'number', 'discount', NULL, 'percent', NULL),
    ('quickbooks', 'Line.LinkedTxn.TxnId', 'linked_txn_id', 'text', NULL, NULL, NULL, 'Payment/BillPayment: the Invoice or Bill paid'),
    ('quickbooks', 'Line.LinkedTxn.TxnType', 'linked_txn_type', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'date', 'transaction_date', 'date', NULL, NULL, NULL, NULL),
    ('file_upload', 'order_date', 'transaction_date', 'date', NULL, NULL, NULL, NULL),
    ('file_upload', 'transaction_date', 'transaction_date', 'date', NULL, NULL, NULL, NULL),
    ('file_upload', 'expense_date', 'transaction_date', 'date', NULL, NULL, NULL, NULL),
    ('file_upload', 'invoice_date', 'transaction_date', 'date', NULL, NULL, NULL, NULL),
    ('file_upload', 'due_date', 'due_date', 'date', NULL, NULL, NULL, NULL),
    ('file_upload', 'order_id', 'reference_number', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'invoice_number', 'reference_number', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'receipt_number', 'reference_number', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'customer_id', 'customer_source_id', 'text', NULL, NULL, NULL, 'The client''s own customer ID'),
    ('file_upload', 'customer', 'customer_name', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'customer_name', 'customer_name', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'customer_email', 'customer_email', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'vendor', 'vendor_name', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'supplier', 'vendor_name', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'product', 'item_name', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'item', 'item_name', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'description', 'line_description', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'quantity', 'quantity', 'number', 'quantity', NULL, 'count', NULL),
    ('file_upload', 'qty', 'quantity', 'number', 'quantity', NULL, 'count', NULL),
    ('file_upload', 'unit_price', 'unit_price', 'number', 'price', 'unit', 'usd', NULL),
    ('file_upload', 'price', 'unit_price', 'number', 'price', 'unit', 'usd', NULL),
    ('file_upload', 'amount', 'line_amount', 'number', 'amount', 'line', 'usd', 'A file row is one line, like Line.Amount'),
    ('file_upload', 'total', 'total_amount', 'number', 'amount', 'total', 'usd', NULL),
    ('file_upload', 'total_amount', 'total_amount', 'number', 'amount', 'total', 'usd', NULL),
    ('file_upload', 'tax', 'tax_amount', 'number', 'tax', NULL, 'usd', NULL),
    ('file_upload', 'payment_method', 'payment_method_name', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'store', 'location_name', 'text', NULL, NULL, NULL, 'Same as QuickBooks Location'),
    ('file_upload', 'location', 'location_name', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'memo', 'memo', 'text', NULL, NULL, NULL, NULL),
    ('file_upload', 'notes', 'memo', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'InvoiceID', 'source_record_id', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'BankTransactionID', 'source_record_id', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'PaymentID', 'source_record_id', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'InvoiceNumber', 'reference_number', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'Reference', 'reference_note', 'text', NULL, NULL, NULL, 'Xero Reference: a PO number or note (not the invoice number)'),
    ('xero', 'Type', 'xero_type', 'text', NULL, NULL, NULL, 'ACCREC / ACCPAY, RECEIVE / SPEND'),
    ('xero', 'PaymentType', 'xero_type', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'Status', 'status', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'DateString', 'transaction_date', 'date', NULL, NULL, NULL, 'Also copied to event_date'),
    ('xero', 'DueDateString', 'due_date', 'date', NULL, NULL, NULL, NULL),
    ('xero', 'CurrencyCode', 'currency', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'CurrencyRate', 'exchange_rate', 'number', 'rate', 'exchange', 'ratio', NULL),
    ('xero', 'LineAmountTypes', 'line_amount_types', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'SubTotal', 'subtotal_amount', 'number', 'amount', 'subtotal', 'usd', NULL),
    ('xero', 'TotalTax', 'tax_amount', 'number', 'tax', NULL, 'usd', NULL),
    ('xero', 'Total', 'total_amount', 'number', 'amount', 'total', 'usd', NULL),
    ('xero', 'AmountDue', 'balance_due', 'number', 'balance', 'due', 'usd', NULL),
    ('xero', 'AmountPaid', 'amount_paid', 'number', 'amount', 'paid', 'usd', NULL),
    ('xero', 'AmountCredited', 'amount_credited', 'number', 'amount', 'credited', 'usd', NULL),
    ('xero', 'Amount', 'payment_amount', 'number', 'amount', 'payment', 'usd', 'Payments: the amount paid'),
    ('xero', 'Contact.ContactID', 'customer_source_id', 'text', NULL, NULL, NULL, 'On bills / money spent / bill payments this becomes vendor_source_id'),
    ('xero', 'Contact.Name', 'customer_name', 'text', NULL, NULL, NULL, 'On bills / money spent / bill payments this becomes vendor_name'),
    ('xero', 'Contact.EmailAddress', 'customer_email', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'Invoice.InvoiceID', 'linked_txn_id', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'Invoice.InvoiceNumber', 'linked_reference_number', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'Invoice.Contact.ContactID', 'customer_source_id', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'Invoice.Contact.Name', 'customer_name', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'BankAccount.AccountID', 'payment_account_id', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'BankAccount.Name', 'payment_account_name', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'IsReconciled', 'is_reconciled', 'boolean', NULL, NULL, NULL, NULL),
    ('xero', 'UpdatedDateUTC', 'source_updated_at', 'timestamp', NULL, NULL, NULL, NULL),
    ('xero', 'LineItems.LineItemID', 'line_source_id', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'LineItems.Description', 'line_description', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'LineItems.Quantity', 'quantity', 'number', 'quantity', NULL, 'count', NULL),
    ('xero', 'LineItems.UnitAmount', 'unit_price', 'number', 'price', 'unit', 'usd', NULL),
    ('xero', 'LineItems.LineAmount', 'line_amount', 'number', 'amount', 'line', 'usd', NULL),
    ('xero', 'LineItems.TaxAmount', 'line_tax_amount', 'number', 'tax', 'line', 'usd', NULL),
    ('xero', 'LineItems.DiscountRate', 'discount_percent', 'number', 'discount', NULL, 'percent', NULL),
    ('xero', 'LineItems.ItemCode', 'item_source_id', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'LineItems.Item.Name', 'item_name', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'LineItems.AccountCode', 'account_code', 'text', NULL, NULL, NULL, NULL),
    ('xero', 'LineItems.TaxType', 'tax_code', 'text', NULL, NULL, NULL, NULL)
ON CONFLICT (system, source_field) DO UPDATE
    SET field_name = EXCLUDED.field_name,         value_type = EXCLUDED.value_type,
        metric_type = EXCLUDED.metric_type,       metric_subtype = EXCLUDED.metric_subtype,
        metric_unit = EXCLUDED.metric_unit,       notes = EXCLUDED.notes;

-- ── fact.account_data: every client's stored data, one row per METRIC ──────
-- The unified data design (docs/DATA_MODEL.md). IDs, dates and categories
-- stay as they are (in `attributes`, on every row of the record line); each
-- number that is measured becomes its own row, described the same way for
-- every client: metric_type / metric_subtype / metric_unit / metric_value.
--
--   file:  order_id | order_date | store   | quantity | unit_price | amount
--          50001    | 2024-10-11 | Midtown | 3        | 7.00       | 21.00
--   here:  field_name  | metric_type | metric_subtype | metric_unit | metric_value | attributes
--          quantity    | quantity    |                | count       | 3            | {"reference_number": 50001, "transaction_date": "2024-10-11", "location_name": "Midtown"}
--          unit_price  | price       | unit           | usd         | 7.00         | (same)
--          line_amount | amount      | line           | usd         | 21.00        | (same)
--
-- A new kind of number from a new source is a new metric_type VALUE, never a
-- new column or table. Reshape code: api/services/unified.py; the load:
-- api/services/warehouse_store.py.
--
-- No duplicate data, three ways:
--   1. UNIQUE (account_id, source_system, source_name, record_id, line_number,
--      field_name): one metric of one record line can only exist once.
--   2. record_id is the source's own ID ("Invoice:130", an order number) or,
--      for files with no ID column, a fingerprint of the row's content — the
--      same row uploaded twice gets the same record_id, so rule 1 catches it.
--   3. row_hash: a reload with the same content matches the stored hash and
--      the load skips the row — nothing is rewritten, modified_at stays.
CREATE TABLE IF NOT EXISTS fact.account_data (
    row_id              BIGSERIAL PRIMARY KEY,     -- ID of this one row (one metric of one record line)
    account_id          UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,       -- whose data (the plan holder); Row Level Security filters on it
    consultant_id       UUID REFERENCES dim.consultants (id) ON DELETE SET NULL,            -- the consultant who loaded it, if any
    source_system       TEXT NOT NULL DEFAULT 'file_upload',  -- where it came from: 'file_upload', 'quickbooks', ...
    source_name         TEXT NOT NULL,             -- which file / connection: 'sales.csv', 'QuickBooks Online'
    record_id           TEXT NOT NULL,             -- the record's ID in the source, e.g. '50001', 'Invoice:130', or 'sha256:...'
    line_number         INTEGER NOT NULL DEFAULT 0, -- 0 = one row per record (or a QuickBooks header); 1, 2, ... = lines of an order / invoice
    event_type          TEXT NOT NULL,             -- WHAT the rows are, e.g. 'transaction' (api/services/warehouse_loader.py EVENT_TYPES)
    event_subtype       TEXT NOT NULL,             -- e.g. 'income'
    event_date          DATE,                      -- the record's date (also kept in attributes); same on every row of a record
    field_name          TEXT NOT NULL,             -- the metric's unified name, e.g. 'line_amount'; '_no_metric' = a line with no numbers, kept for its attributes
    source_field        TEXT NOT NULL,             -- the client's own column / path, e.g. 'amount' or 'Line.Amount' — traces every value back
    metric_type         TEXT,                      -- what is measured: 'amount', 'price', 'cost', 'quantity', 'score', ...
    metric_subtype      TEXT,                      -- which one (optional): 'line', 'total', 'unit', 'shipping', 'csat', ...
    metric_unit         TEXT,                      -- what the number is in: 'usd', 'count', 'percent', 'hours', ... (never sum across units)
    metric_value        NUMERIC,                   -- the number (exact; no rounding)
    attributes          JSONB NOT NULL DEFAULT '{}', -- the line's IDs, dates and categories, by unified name: {"customer_name": "Blue Door Cafe", ...}
    row_hash            TEXT NOT NULL,             -- fingerprint of metric + value + date + attributes (skips unchanged reloads)
    mapped              BOOLEAN NOT NULL DEFAULT true, -- false = named/described by a guess (not in field_map yet): worth a review
    added_at            TIMESTAMPTZ NOT NULL DEFAULT now(), -- first stored
    modified_at         TIMESTAMPTZ NOT NULL DEFAULT now(), -- content last changed (unchanged reloads leave it alone)
    source_modified_at  TIMESTAMPTZ,               -- when the source last changed the record (QuickBooks MetaData.LastUpdatedTime)
    -- A metric row has its type, unit and value; a line's one '_no_metric' row has none.
    CHECK (CASE WHEN field_name = '_no_metric'
                THEN metric_type IS NULL AND metric_unit IS NULL AND metric_value IS NULL
                ELSE metric_type IS NOT NULL AND metric_unit IS NOT NULL AND metric_value IS NOT NULL END),
    UNIQUE (account_id, source_system, source_name, record_id, line_number, field_name)   -- no duplicate data
);
CREATE INDEX IF NOT EXISTS idx_account_data_date       ON fact.account_data (account_id, event_date);
CREATE INDEX IF NOT EXISTS idx_account_data_metric     ON fact.account_data (account_id, metric_type, metric_subtype);
CREATE INDEX IF NOT EXISTS idx_account_data_source     ON fact.account_data (account_id, source_system, source_name);
-- Fast filters on categories: WHERE attributes @> '{"location_name": "Midtown"}'
CREATE INDEX IF NOT EXISTS idx_account_data_attributes ON fact.account_data USING gin (attributes jsonb_path_ops);

-- ── fact.entities: each customer, vendor, product, payee, location — ONCE ──
-- Before this, a customer's name, email and address were copied onto every
-- metric row of every invoice, and the same customer from two sources was two
-- unrelated pieces of text. Now each real customer is ONE row here (its
-- details merged from every source), each source's way of naming it is a row
-- in fact.entity_sources, and account_data's attributes hold only the link
-- ({"customer_link": 12}). Matching: api/services/entities.py (same email or
-- phone, the same normalized name, or a fuzzy name match; 80-91 waits for a
-- person). See docs/DATA_MODEL.md -> "Entities".
CREATE TABLE IF NOT EXISTS fact.entities (
    entity_id     BIGSERIAL PRIMARY KEY,
    account_id    UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,   -- whose (RLS filters on it)
    entity_type   TEXT NOT NULL CHECK (entity_type IN ('customer', 'vendor', 'item', 'payee', 'location')),
    display_name  TEXT,                                -- the first name seen: "Blue Door Cafe"
    details       JSONB NOT NULL DEFAULT '{}',         -- {"customer_email": "...", "bill_city": "..."}: once
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_entities_account ON fact.entities (account_id, entity_type);

-- ── fact.entity_sources: how each source refers to an entity ─────────────────
-- "QuickBooks customer 58" and "sales.csv: blue door cafe" -> the same entity.
-- account_data rows point HERE (not at the entity), so merging two customers
-- later re-points one row and no stored metric changes.
CREATE TABLE IF NOT EXISTS fact.entity_sources (
    link_id              BIGSERIAL PRIMARY KEY,
    account_id           UUID NOT NULL REFERENCES dim.accounts (id) ON DELETE CASCADE,
    entity_id            BIGINT NOT NULL REFERENCES fact.entities (entity_id) ON DELETE CASCADE,
    entity_type          TEXT NOT NULL,
    source_system        TEXT NOT NULL,                -- 'quickbooks', 'xero', 'file_upload'
    source_key           TEXT NOT NULL,                -- 'id:58' (the source's ID) or 'name:blue door cafe'
    source_id            TEXT,                         -- the source's own ID, when it has one
    source_label         TEXT,                         -- the name as this source writes it
    match_method         TEXT NOT NULL CHECK (match_method IN ('new', 'email', 'phone', 'name', 'fuzzy',
                                                               'review', 'confirmed')),
    match_score          SMALLINT,                     -- 0-100 for name matches
    -- A possible match a PERSON should confirm (score 80-91): "Is this the same customer?"
    suggested_entity_id  BIGINT REFERENCES fact.entities (entity_id) ON DELETE SET NULL,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (account_id, entity_type, source_system, source_key)
);
CREATE INDEX IF NOT EXISTS idx_entity_sources_entity ON fact.entity_sources (entity_id);


-- ############################################################################
-- Row Level Security
-- ############################################################################
-- dim tables: RLS on with NO policies = nobody can read them through
-- Supabase's browser API (the safe default). The FastAPI backend connects with
-- the database password and decides who may see what itself
-- (api/services/consultants.py: a consultant sees only their own clients).
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['accounts', 'consultants', 'teams', 'subscriptions', 'consultations', 'usage_events',
                             'storage_consents', 'load_runs', 'data_connections', 'dashboard_tabs', 'question_log',
                             'api_usage']
    LOOP
        EXECUTE format('ALTER TABLE dim.%I ENABLE ROW LEVEL SECURITY', t);
    END LOOP;
END $$;
ALTER TABLE fact.field_map ENABLE ROW LEVEL SECURITY;

-- fact.account_data: each query sees ONE account's rows. The API runs
-- set_config('app.account_id', '<uuid>', true) inside the transaction before
-- touching it, and also filters by account_id itself (belt and braces: the
-- login in DATABASE_URL — Supabase's `postgres` — may bypass RLS, and must,
-- because the dim tables above have no policies; every query still asks for
-- one account only). FORCE applies the rule to the table's owner too, so any
-- other login that does NOT bypass RLS (a read-only reporting user, say)
-- sees only the account named in app.account_id, and nothing without it.
ALTER TABLE fact.account_data ENABLE ROW LEVEL SECURITY;
ALTER TABLE fact.account_data FORCE ROW LEVEL SECURITY;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'fact' AND tablename = 'account_data'
                   AND policyname = 'one_account') THEN
        CREATE POLICY one_account ON fact.account_data
            USING (account_id = NULLIF(current_setting('app.account_id', true), '')::uuid);
    END IF;
END $$;
-- The same rule for the entity tables (they hold client data too).
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['entities', 'entity_sources']
    LOOP
        EXECUTE format('ALTER TABLE fact.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE fact.%I FORCE ROW LEVEL SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'fact' AND tablename = t
                       AND policyname = 'one_account') THEN
            EXECUTE format('CREATE POLICY one_account ON fact.%I USING '
                           '(account_id = NULLIF(current_setting(''app.account_id'', true), '''')::uuid)', t);
        END IF;
    END LOOP;
END $$;


-- ############################################################################
-- Examples (not run): reading account_data
-- ############################################################################
-- Monthly revenue by store. Categories come out of `attributes` with ->>.
--   SELECT set_config('app.account_id', '<account uuid>', true);
--   SELECT date_trunc('month', event_date)::date AS month,
--          attributes->>'location_name'          AS store,
--          SUM(metric_value)                     AS revenue
--   FROM fact.account_data
--   WHERE account_id = '<account uuid>'
--     AND metric_type = 'amount' AND metric_subtype = 'line' AND metric_unit = 'usd'
--     AND (event_type, event_subtype) = ('transaction', 'income')
--   GROUP BY 1, 2 ORDER BY 1, 2;
--
-- One row per record line again (what "Use my stored data" does in pandas,
-- api/services/unified.py unified_to_wide):
--   SELECT record_id, line_number, event_date,
--          MAX(attributes->>'item_name')                               AS item_name,
--          MAX(metric_value) FILTER (WHERE field_name = 'quantity')    AS quantity,
--          MAX(metric_value) FILTER (WHERE field_name = 'line_amount') AS line_amount
--   FROM fact.account_data
--   WHERE account_id = '<account uuid>' AND source_name = 'sales.csv'
--   GROUP BY record_id, line_number, event_date ORDER BY record_id, line_number;
