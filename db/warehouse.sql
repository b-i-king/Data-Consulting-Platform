-- ============================================================================
-- Client data warehouse — ONE shared star schema for every client
-- ============================================================================
-- Used by the Data Engineering service, ONLY for clients who opt in to having
-- their data stored (the free cleaning flow stores nothing). Design notes and
-- the ER diagram: docs/DATA_MODEL.md.
--
-- THE BIG IDEA
--   Every client's data lives in the SAME tables. Each row carries client_id.
--   No per-client tables, no per-client columns. Client-specific extras go in
--   a JSONB `attributes` column. Row Level Security (bottom of this file)
--   makes sure a query only ever sees one client's rows.
--
-- HOW TO APPLY (after db/schema.sql, which creates `accounts` and `teams`)
--   psql "$DATABASE_URL" -f db/warehouse.sql      (safe to re-run)
--
-- TABLES
--   dim_client      who the data belongs to (an individual or a business)
--   dim_date        one row per calendar day (for easy grouping by month, etc.)
--   dim_source      where rows came from (CSV upload, QuickBooks, Gusto, ...)
--   dim_event_type  WHAT happened: type + subtype (income, invoice sent, CSAT, hire...)
--   dim_customer    the client's customers
--   dim_employee    the client's employees
--   dim_product     the client's products / services sold
--   fact_event      one row per business event, at the smallest grain available
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS warehouse;
SET search_path = warehouse, public;

-- ── dim_client: the tenant ──────────────────────────────────────────────────
-- Points at EITHER an individual account OR a business team (never both).
-- The user dimension itself already exists: public.accounts (individual vs.
-- business, names, team, created_at, last_login_at, industry).
CREATE TABLE IF NOT EXISTS dim_client (
    client_id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id           UUID REFERENCES public.accounts (id) ON DELETE CASCADE,   -- individuals
    team_id              UUID REFERENCES public.teams (id) ON DELETE CASCADE,      -- businesses
    display_name         TEXT NOT NULL,
    client_type          TEXT NOT NULL CHECK (client_type IN ('individual', 'business')),
    -- Copied from the account at onboarding, for industry benchmarks.
    industry_supersector TEXT,
    naics_sector         TEXT,
    naics_subsector      TEXT,
    company_size         TEXT,               -- LinkedIn size code A-I (benchmarks by size)
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((account_id IS NULL) <> (team_id IS NULL)),            -- exactly one owner
    CHECK ((client_type = 'business') = (team_id IS NOT NULL))
);
ALTER TABLE dim_client ADD COLUMN IF NOT EXISTS company_size TEXT;   -- added after v0.1
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_client_account ON dim_client (account_id) WHERE account_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_dim_client_team    ON dim_client (team_id)    WHERE team_id IS NOT NULL;

-- ── dim_date: shared by all clients ─────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dim_date (
    date_key     INTEGER PRIMARY KEY,          -- YYYYMMDD, e.g. 20240315
    full_date    DATE NOT NULL UNIQUE,
    year         SMALLINT NOT NULL,
    quarter      SMALLINT NOT NULL,
    month        SMALLINT NOT NULL,
    month_name   TEXT NOT NULL,
    week_of_year SMALLINT NOT NULL,
    day_of_week  SMALLINT NOT NULL,            -- 1 = Monday ... 7 = Sunday (ISO)
    day_name     TEXT NOT NULL,
    is_weekend   BOOLEAN NOT NULL
);

-- Fill 2000-01-01 .. 2040-12-31 (about 15,000 rows; re-running adds nothing).
INSERT INTO dim_date
SELECT to_char(d, 'YYYYMMDD')::INT, d::DATE,
       EXTRACT(YEAR FROM d), EXTRACT(QUARTER FROM d), EXTRACT(MONTH FROM d), to_char(d, 'FMMonth'),
       EXTRACT(WEEK FROM d), EXTRACT(ISODOW FROM d), to_char(d, 'FMDay'), EXTRACT(ISODOW FROM d) IN (6, 7)
FROM generate_series('2000-01-01'::DATE, '2040-12-31'::DATE, INTERVAL '1 day') AS d
ON CONFLICT (date_key) DO NOTHING;

-- ── dim_source: lineage (where each row came from) ──────────────────────────
CREATE TABLE IF NOT EXISTS dim_source (
    source_id       BIGSERIAL PRIMARY KEY,
    client_id       UUID NOT NULL REFERENCES dim_client (client_id) ON DELETE CASCADE,
    system          TEXT NOT NULL,             -- 'file_upload', 'quickbooks', 'gusto', 'shopify', ...
    name            TEXT NOT NULL,             -- e.g. 'sales_2024.csv' or 'QuickBooks Online'
    connected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_loaded_at  TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_dim_source_client ON dim_source (client_id);

-- ── dim_event_type: WHAT happened (shared lookup, not per client) ───────────
-- Your "type" and "subtype". Instead of a separate table per kind of thing
-- (invoice types, feedback types, ...), every kind is a ROW here. A new kind
-- of event is an INSERT, never a new table.
CREATE TABLE IF NOT EXISTS dim_event_type (
    event_type_id  SMALLSERIAL PRIMARY KEY,
    event_type     TEXT NOT NULL,              -- 'transaction', 'invoice', 'feedback', 'employee', 'payroll'
    event_subtype  TEXT NOT NULL,              -- 'income', 'expense', 'sent', 'received', 'csat', ...
    measure        TEXT NOT NULL CHECK (measure IN ('amount', 'quantity', 'score', 'count')),
    -- For money events: +1 = money in, -1 = money out, 0 = neither.
    -- Net cash = SUM(amount * direction).
    direction      SMALLINT NOT NULL DEFAULT 0 CHECK (direction IN (-1, 0, 1)),
    description    TEXT NOT NULL,
    UNIQUE (event_type, event_subtype)
);

INSERT INTO dim_event_type (event_type, event_subtype, measure, direction, description) VALUES
    ('transaction', 'income',      'amount',   1, 'Money received: a sale, payment, or other income'),
    ('transaction', 'expense',     'amount',  -1, 'Money spent: a purchase, bill, or other expense'),
    ('transaction', 'refund',      'amount',  -1, 'Money returned to a customer'),
    ('invoice',     'sent',        'amount',   0, 'Invoice issued to a customer (accounts receivable)'),
    ('invoice',     'received',    'amount',   0, 'Invoice received from a vendor (accounts payable)'),
    ('invoice',     'paid',        'amount',   0, 'An invoice was paid (sent or received)'),
    ('feedback',    'csat',        'score',    0, 'Customer satisfaction score (e.g. 1–5)'),
    ('feedback',    'rating',      'score',    0, 'Star rating or review score'),
    ('feedback',    'nps',         'score',    0, 'Net Promoter Score response (0–10)'),
    ('employee',    'hire',        'count',    0, 'An employee started'),
    ('employee',    'termination', 'count',    0, 'An employee left'),
    ('payroll',     'gross_pay',   'amount',  -1, 'Gross wages paid in a pay run'),
    ('inventory',   'adjustment',  'quantity', 0, 'Stock added or removed')
ON CONFLICT (event_type, event_subtype) DO NOTHING;

-- ── Business dimensions (one row per real-world thing, per client) ─────────
-- source_*_id is the ID in the source system. It can be NULL (a spreadsheet
-- with names only); then fuzzy matching (api/services/entity_matching.py)
-- links the row to an existing customer, and `match_status` records how.
CREATE TABLE IF NOT EXISTS dim_customer (
    customer_key        BIGSERIAL PRIMARY KEY,
    client_id           UUID NOT NULL REFERENCES dim_client (client_id) ON DELETE CASCADE,
    source_id           BIGINT REFERENCES dim_source (source_id) ON DELETE SET NULL,
    source_customer_id  TEXT,                  -- e.g. QuickBooks Customer.Id; NULL if none
    name                TEXT,
    email               TEXT,
    phone               TEXT,
    city                TEXT,
    state               TEXT,                  -- 2-letter US state code
    postal_code         TEXT,
    -- Same customer seen in several sources -> they share one master record.
    master_customer_key BIGINT REFERENCES dim_customer (customer_key) ON DELETE SET NULL,
    match_status        TEXT NOT NULL DEFAULT 'source_id'
                        CHECK (match_status IN ('source_id', 'exact_email', 'exact_phone', 'fuzzy_auto', 'fuzzy_reviewed', 'unmatched')),
    attributes          JSONB NOT NULL DEFAULT '{}',   -- client-specific extras
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, source_id, source_customer_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_customer_client ON dim_customer (client_id);
CREATE INDEX IF NOT EXISTS idx_dim_customer_email  ON dim_customer (client_id, lower(email));

CREATE TABLE IF NOT EXISTS dim_employee (
    employee_key        BIGSERIAL PRIMARY KEY,
    client_id           UUID NOT NULL REFERENCES dim_client (client_id) ON DELETE CASCADE,
    source_id           BIGINT REFERENCES dim_source (source_id) ON DELETE SET NULL,
    source_employee_id  TEXT,                  -- e.g. Gusto employee uuid
    name                TEXT,
    email               TEXT,
    job_title           TEXT,
    department          TEXT,
    pay_type            TEXT CHECK (pay_type IN ('hourly', 'salary', 'contractor')),
    hire_date           DATE,
    termination_date    DATE,
    attributes          JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, source_id, source_employee_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_employee_client ON dim_employee (client_id);

CREATE TABLE IF NOT EXISTS dim_product (
    product_key         BIGSERIAL PRIMARY KEY,
    client_id           UUID NOT NULL REFERENCES dim_client (client_id) ON DELETE CASCADE,
    source_id           BIGINT REFERENCES dim_source (source_id) ON DELETE SET NULL,
    source_product_id   TEXT,                  -- SKU or source item ID
    name                TEXT NOT NULL,
    category            TEXT,
    unit_price          NUMERIC(14, 2),
    unit_cost           NUMERIC(14, 2),
    attributes          JSONB NOT NULL DEFAULT '{}',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, source_id, source_product_id)
);
CREATE INDEX IF NOT EXISTS idx_dim_product_client ON dim_product (client_id);

-- ── fact_event: one row per business event, at the smallest grain ──────────
-- A sale line, an invoice, a CSAT response, a hire, a pay run... all here.
-- Columns that don't apply stay NULL (a CSAT row has a score, no amount).
CREATE TABLE IF NOT EXISTS fact_event (
    event_id          BIGSERIAL PRIMARY KEY,
    client_id         UUID NOT NULL REFERENCES dim_client (client_id) ON DELETE CASCADE,
    event_type_id     SMALLINT NOT NULL REFERENCES dim_event_type (event_type_id),
    date_key          INTEGER NOT NULL REFERENCES dim_date (date_key),
    occurred_at       TIMESTAMPTZ,             -- exact time, when the source has it
    source_id         BIGINT REFERENCES dim_source (source_id) ON DELETE SET NULL,
    source_record_id  TEXT,                    -- ID in the source; makes reloads safe
    customer_key      BIGINT REFERENCES dim_customer (customer_key) ON DELETE SET NULL,
    employee_key      BIGINT REFERENCES dim_employee (employee_key) ON DELETE SET NULL,
    product_key       BIGINT REFERENCES dim_product (product_key) ON DELETE SET NULL,
    -- Measures (use the one dim_event_type.measure names; others stay NULL).
    amount            NUMERIC(14, 2),          -- US dollars, always positive; see direction
    quantity          NUMERIC(14, 3),
    score             NUMERIC(6, 2),           -- CSAT, rating, NPS
    -- Common descriptive fields.
    reference_number  TEXT,                    -- invoice #, order #, receipt #
    status            TEXT,                    -- e.g. 'open', 'paid', 'overdue'
    due_date          DATE,                    -- invoices
    attributes        JSONB NOT NULL DEFAULT '{}',   -- anything client-specific
    loaded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (client_id, source_id, source_record_id)  -- re-loading a file never duplicates rows
);
CREATE INDEX IF NOT EXISTS idx_fact_event_client_date ON fact_event (client_id, date_key);
CREATE INDEX IF NOT EXISTS idx_fact_event_client_type ON fact_event (client_id, event_type_id);
CREATE INDEX IF NOT EXISTS idx_fact_event_customer    ON fact_event (customer_key);

-- ── Row Level Security: each query sees ONE client ──────────────────────────
-- The API runs `SET LOCAL app.client_id = '<uuid>'` inside a transaction
-- before touching warehouse tables; these policies hide every other client's
-- rows. FORCE makes the rules apply to the table owner too.
-- (A Postgres superuser still bypasses RLS — use a dedicated, non-superuser
-- role for the API's warehouse connection, set up once like this:)
--
--   CREATE ROLE warehouse_api LOGIN PASSWORD '...';
--   GRANT USAGE ON SCHEMA warehouse TO warehouse_api;
--   GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA warehouse TO warehouse_api;
--   GRANT USAGE ON ALL SEQUENCES IN SCHEMA warehouse TO warehouse_api;
DO $$
DECLARE t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY['dim_source', 'dim_customer', 'dim_employee', 'dim_product', 'fact_event', 'dim_client']
    LOOP
        EXECUTE format('ALTER TABLE warehouse.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE warehouse.%I FORCE ROW LEVEL SECURITY', t);
        IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE schemaname = 'warehouse' AND tablename = t AND policyname = 'client_isolation') THEN
            EXECUTE format(
                'CREATE POLICY client_isolation ON warehouse.%I USING (client_id = NULLIF(current_setting(''app.client_id'', true), '''')::uuid)',
                t);
        END IF;
    END LOOP;
END $$;

-- ── Example: monthly net cash for the current client ────────────────────────
-- SET LOCAL app.client_id = '...';
-- SELECT d.year, d.month, SUM(f.amount * et.direction) AS net_cash
-- FROM warehouse.fact_event f
-- JOIN warehouse.dim_event_type et USING (event_type_id)
-- JOIN warehouse.dim_date d USING (date_key)
-- WHERE et.event_type = 'transaction'
-- GROUP BY d.year, d.month ORDER BY d.year, d.month;

RESET search_path;
