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
--   field_map       how each source's field names line up, and which are metrics (shared lookup)
--   unified_data    every METRIC of every record, one row per metric; IDs, dates and
--                   categories kept in `attributes` (the unified data source)
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

-- ── field_map: align field names AND describe metrics (shared lookup) ──────
-- Every source names things differently: QuickBooks "TxnDate", a spreadsheet
-- "order_date". Each row here says "in system X, field Y means unified field
-- Z" — and, when Z is a METRIC, what it measures (metric_type / subtype /
-- unit). Fields whose metric_type is NULL are not metrics: IDs, dates, names
-- and categories, kept as they are in unified_data.attributes.
-- The loader (api/services/unified.py) renames fields with this map before
-- writing unified_data. A field that is NOT listed is still stored, under a
-- snake_case version of its own name (an unlisted number is described by a
-- guess from its name, flagged mapped = false), so nothing is ever lost.
-- Adding a row here aligns that field from then on; the unified-metrics skill
-- writes ready-to-run rows for a client's reviewed columns.
-- Paths use dots for nesting and leave out list positions: 'Line.Amount' is
-- the Amount of every item in a QuickBooks Line list.
-- KEEP IN SYNC with QUICKBOOKS_FIELD_MAP / FILE_UPLOAD_FIELD_MAP in
-- api/services/unified.py (tests/test_unified.py compares the two).
CREATE TABLE IF NOT EXISTS field_map (
    field_map_id    SERIAL PRIMARY KEY,
    system          TEXT NOT NULL,           -- same values as dim_source.system: 'quickbooks', 'file_upload', ...
    source_field    TEXT NOT NULL,           -- the source's name or JSON path, e.g. 'CustomerRef.name'
    field_name      TEXT NOT NULL,           -- the unified name, e.g. 'customer_name'
    value_type      TEXT NOT NULL CHECK (value_type IN ('text', 'number', 'date', 'timestamp', 'boolean')),
    metric_type     TEXT,                    -- set = this field is a metric, e.g. 'amount'; NULL = kept as an attribute
    metric_subtype  TEXT,                    -- e.g. 'line', 'total', 'unit' (optional)
    metric_unit     TEXT,                    -- e.g. 'usd', 'count', 'percent'
    notes           TEXT,                    -- plain-words explanation, when the name is not obvious
    UNIQUE (system, source_field),
    CHECK (metric_type IS NULL OR (value_type = 'number' AND metric_unit IS NOT NULL))
);
-- Databases set up before metrics were described here get the new columns.
ALTER TABLE field_map ADD COLUMN IF NOT EXISTS metric_type    TEXT;
ALTER TABLE field_map ADD COLUMN IF NOT EXISTS metric_subtype TEXT;
ALTER TABLE field_map ADD COLUMN IF NOT EXISTS metric_unit    TEXT;

-- The seed is the source of truth: re-running this file brings existing rows up to date.
INSERT INTO field_map (system, source_field, field_name, value_type, metric_type, metric_subtype, metric_unit, notes) VALUES
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
    ('file_upload', 'notes', 'memo', 'text', NULL, NULL, NULL, NULL)
ON CONFLICT (system, source_field) DO UPDATE
    SET field_name = EXCLUDED.field_name,         value_type = EXCLUDED.value_type,
        metric_type = EXCLUDED.metric_type,       metric_subtype = EXCLUDED.metric_subtype,
        metric_unit = EXCLUDED.metric_unit,       notes = EXCLUDED.notes;

-- ── unified_data: every METRIC of every record, one row per metric ─────────
-- The owner's "unified data source": all of a Data Engineering client's clean
-- data, from every source, in ONE shape. IDs, dates and categories stay as
-- they are (in `attributes`, on every row of the record line); each number
-- that is measured becomes its own row, described the same way for every
-- client: metric_type / metric_subtype / metric_unit / metric_value.
--
--   wide:  order_id | order_date | store   | quantity | unit_price | amount
--          50001    | 2024-10-11 | Midtown | 3        | 7.00       | 21.00
--   here:  field_name  | metric_type | metric_subtype | metric_unit | metric_value | attributes
--          quantity    | quantity    |                | count       | 3            | {"reference_number": 50001, "transaction_date": "2024-10-11", "location_name": "Midtown"}
--          unit_price  | price       | unit           | usd         | 7.00         | (same)
--          line_amount | amount      | line           | usd         | 21.00        | (same)
--
-- A new kind of number from a new source is a new metric_type VALUE, never a
-- new column. Test a client's file first with the unified-metrics skill: it
-- shows every decision and proves by round trip that nothing is lost.
--
-- How it relates to fact_event:
--   fact_event    one row per EVENT, with fixed measures (amount, quantity,
--                 score) and real dimension keys. What the warehouse load in
--                 the app writes today; fast for dashboards.
--   unified_data  one row per METRIC, from any source (QuickBooks, files),
--                 with nothing thrown away (unknown fields are kept too).
--                 The complete, lossless copy; fact_event can be rebuilt from it.
--
-- No duplicate data, three ways:
--   1. UNIQUE (client_id, source_id, record_id, line_number, field_name):
--      one metric of one record line can only exist once.
--   2. record_id is the source's own ID ("Invoice:130") or, for files with no
--      ID column, a SHA-256 hash of the row's content — the same row uploaded
--      twice gets the same record_id, so rule 1 catches it.
--   3. row_hash: a reload with the same content matches the stored hash and
--      the upsert (example at the bottom) skips the row — nothing is
--      rewritten and modified_at keeps its old value.
-- Reshape code: api/services/unified.py. Explained in docs/DATA_MODEL.md.

-- The first draft of this table stored one row per FIELD (value_text /
-- value_number / ...). It was never loaded by the app; if a database still
-- has it, it is renamed (not dropped, so nothing could be lost) to make room.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.columns
               WHERE table_schema = 'warehouse' AND table_name = 'unified_data' AND column_name = 'value_text') THEN
        ALTER TABLE warehouse.unified_data RENAME TO unified_data_v0;
        ALTER INDEX IF EXISTS warehouse.idx_unified_client_date  RENAME TO idx_unified_v0_client_date;
        ALTER INDEX IF EXISTS warehouse.idx_unified_client_field RENAME TO idx_unified_v0_client_field;
        ALTER INDEX IF EXISTS warehouse.idx_unified_account      RENAME TO idx_unified_v0_account;
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS unified_data (
    row_id              BIGSERIAL PRIMARY KEY,     -- ID of this one row (one metric of one record line)
    client_id           UUID NOT NULL REFERENCES dim_client (client_id) ON DELETE CASCADE,  -- the tenant; Row Level Security filters on it
    account_id          UUID NOT NULL REFERENCES public.accounts (id) ON DELETE CASCADE,    -- the client's account (a business: the team owner, who holds the plan)
    consultant_id       UUID REFERENCES public.consultants (id) ON DELETE SET NULL,         -- the consultant who manages this client, if any
    source_id           BIGINT NOT NULL REFERENCES dim_source (source_id) ON DELETE CASCADE, -- where it came from (removing a source removes its data)
    record_id           TEXT NOT NULL,             -- the source record's ID, e.g. 'Invoice:130', or 'sha256:...' for files with no ID; ties one record's rows together
    line_number         INTEGER NOT NULL DEFAULT 0, -- 0 = header; 1, 2, ... = QuickBooks Line items (or repeated-ID file rows)
    event_type          TEXT NOT NULL,             -- WHAT happened, e.g. 'invoice'   } together a row of
    event_subtype       TEXT NOT NULL,             -- e.g. 'sent'                     } dim_event_type
    event_date          DATE,                      -- the date used in the clean data (e.g. QuickBooks TxnDate); same on every row of a record
    added_at            TIMESTAMPTZ NOT NULL DEFAULT now(), -- when this row was first added to our database
    modified_at         TIMESTAMPTZ NOT NULL DEFAULT now(), -- when this row's content last changed in our database (unchanged reloads leave it alone)
    source_modified_at  TIMESTAMPTZ,               -- when the source last changed the record, as of this row's last change (QuickBooks MetaData.LastUpdatedTime)
    field_name          TEXT NOT NULL,             -- the metric's unified name, e.g. 'line_amount' (via field_map); '_no_metric' = a line with no numbers, kept for its attributes
    source_field        TEXT NOT NULL,             -- the original name/path, e.g. 'Line.Amount' or 'Amount ($)'; traces every value back
    metric_type         TEXT,                      -- what is measured: 'amount', 'price', 'cost', 'quantity', 'score', ...
    metric_subtype      TEXT,                      -- which one (optional): 'line', 'total', 'unit', 'shipping', 'csat', ...
    metric_unit         TEXT,                      -- what the number is in: 'usd', 'count', 'percent', 'hours', ... (never sum across units)
    metric_value        NUMERIC,                   -- the number (exact; no rounding)
    attributes          JSONB NOT NULL DEFAULT '{}', -- the line's IDs, dates and categories by unified name, e.g. {"customer_name": "Blue Door Cafe", "location_name": "Midtown"}
    row_hash            TEXT NOT NULL,             -- SHA-256 of the metric, value, event_date and attributes; equal content -> equal hash (skips no-op reloads)
    mapped              BOOLEAN NOT NULL DEFAULT true, -- false = the metric was named/described by a guess (not in field_map yet): review it
    FOREIGN KEY (event_type, event_subtype) REFERENCES dim_event_type (event_type, event_subtype),
    -- A metric row has its type, unit and value; the one '_no_metric' row of a line has none.
    CHECK (CASE WHEN field_name = '_no_metric'
                THEN metric_type IS NULL AND metric_unit IS NULL AND metric_value IS NULL
                ELSE metric_type IS NOT NULL AND metric_unit IS NOT NULL AND metric_value IS NOT NULL END),
    UNIQUE (client_id, source_id, record_id, line_number, field_name)   -- no duplicate data
);
CREATE INDEX IF NOT EXISTS idx_unified_client_date   ON unified_data (client_id, event_date);
CREATE INDEX IF NOT EXISTS idx_unified_client_metric ON unified_data (client_id, metric_type, metric_subtype);
CREATE INDEX IF NOT EXISTS idx_unified_account       ON unified_data (account_id);
-- Fast filters on categories: WHERE attributes @> '{"location_name": "Midtown"}'
CREATE INDEX IF NOT EXISTS idx_unified_attributes    ON unified_data USING gin (attributes jsonb_path_ops);

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
    FOREACH t IN ARRAY ARRAY['dim_source', 'dim_customer', 'dim_employee', 'dim_product', 'fact_event', 'dim_client', 'unified_data']
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

-- ── Example: monthly revenue by store from unified_data ─────────────────────
-- Categories come out of `attributes` with ->>; metrics are filtered by type.
-- SET LOCAL app.client_id = '...';
-- SELECT date_trunc('month', event_date) AS month,
--        attributes->>'location_name'     AS store,
--        SUM(metric_value)                AS revenue
-- FROM warehouse.unified_data
-- WHERE metric_type = 'amount' AND metric_subtype = 'line' AND metric_unit = 'usd'
--   AND (event_type, event_subtype) = ('transaction', 'income')
-- GROUP BY 1, 2 ORDER BY 1, 2;

-- ── Example: pivot unified_data back to one row per record line ────────────
-- (FILTER picks each metric's value; MAX just collapses the one row per metric.)
-- SET LOCAL app.client_id = '...';
-- SELECT record_id, line_number, event_date,
--        MAX(attributes->>'item_name')                                AS item_name,
--        MAX(metric_value) FILTER (WHERE field_name = 'quantity')     AS quantity,
--        MAX(metric_value) FILTER (WHERE field_name = 'unit_price')   AS unit_price,
--        MAX(metric_value) FILTER (WHERE field_name = 'line_amount')  AS line_amount
-- FROM warehouse.unified_data
-- WHERE event_type = 'invoice' AND event_subtype = 'sent'
-- GROUP BY record_id, line_number, event_date
-- ORDER BY record_id, line_number;
-- (Header fields such as customer_name sit on line 0; join line 0 to lines
--  1.. on record_id to put the customer on every line.)

-- ── Example: load a batch without duplicates or no-op rewrites ──────────────
-- The loader first writes the batch (from api/services/unified.py, with
-- attributes as JSON from attributes_json()) to a temporary table `staging`
-- with the same columns, then:
-- INSERT INTO warehouse.unified_data AS u
--        (client_id, account_id, consultant_id, source_id, record_id, line_number,
--         event_type, event_subtype, event_date, source_modified_at, field_name,
--         source_field, metric_type, metric_subtype, metric_unit, metric_value,
--         attributes, row_hash, mapped)
-- SELECT client_id, account_id, consultant_id, source_id, record_id, line_number,
--        event_type, event_subtype, event_date, source_modified_at, field_name,
--        source_field, metric_type, metric_subtype, metric_unit, metric_value,
--        attributes, row_hash, mapped
-- FROM staging
-- ON CONFLICT (client_id, source_id, record_id, line_number, field_name) DO UPDATE
--    SET metric_type = EXCLUDED.metric_type,     metric_subtype = EXCLUDED.metric_subtype,
--        metric_unit = EXCLUDED.metric_unit,     metric_value = EXCLUDED.metric_value,
--        attributes = EXCLUDED.attributes,       row_hash = EXCLUDED.row_hash,
--        source_field = EXCLUDED.source_field,   event_date = EXCLUDED.event_date,
--        event_type = EXCLUDED.event_type,       event_subtype = EXCLUDED.event_subtype,
--        mapped = EXCLUDED.mapped,               source_modified_at = EXCLUDED.source_modified_at,
--        consultant_id = EXCLUDED.consultant_id,
--        modified_at = now()                     -- added_at is never touched
--    -- Only rows whose content really changed are rewritten:
--    WHERE u.row_hash IS DISTINCT FROM EXCLUDED.row_hash;
-- Metrics that disappeared from a re-sent record ("stale" in plan_upsert) are
-- left in place; the loader deletes them by row_id if the client wants that.

RESET search_path;
