# Cloud data sources and integrations

**Question:** *"Do you have the infrastructure for cloud data sources and
integrations like QuickBooks and Gusto?"*

## Where things stand today

**Not yet.** The platform currently reads **files only** (every format pandas
can safely read — see DATA_CLEANING.md). Cloud warehouses and business apps
appear in the questionnaire as "coming soon" and route to your booking link,
which also tells you which ones clients ask for most.

What *is* ready, and what each connector will plug into:

| Piece | Status |
|---|---|
| Common pipeline: DataFrame → categorize → clean → preview | ✅ built — a connector only has to return DataFrames |
| Fuzzy matching for records with no shared ID (`entity_matching.py`) | ✅ built and tested |
| Warehouse to store synced data (`db/warehouse.sql`, with `dim_source` lineage) | ✅ schema built and tested |
| Connector code, OAuth sign-in, credential storage, scheduled syncs | ❌ not built |

### A note on the "connectors" in your Claude account

The connectors on your Claude **data plugin** page (BigQuery, Snowflake,
Databricks, Amplitude, Hex, Atlassian, ...) let **Claude** query those systems
while *you* work — for example during a consulting engagement. They are not
part of this web app, and your clients can't use them. In this session the
BigQuery connector was waiting for authorization and several others couldn't
connect, so they're worth checking in your Claude connector settings if you plan
to use them for client work.

## Two kinds of sources, two jobs

| Kind | Examples | Job on the platform |
|---|---|---|
| **Core data** | Snowflake, BigQuery, Postgres, S3, Google Sheets, file uploads | The client's main tables (sales, customers, operations) |
| **Enhancers** | QuickBooks, Gusto, Square, Shopify, Stripe | Add money and people to the core data: invoices, payments, payroll, headcount — making analysis and dashboards richer |

You described QuickBooks and Gusto as enhancers — that's the right framing.
They rarely replace a client's own data; they **join** to it:

- QuickBooks invoices + the client's sales file → revenue vs. billed vs. collected
- Gusto payroll + sales → labor cost as a % of sales, sales per employee
- Customer feedback + QuickBooks customers → does satisfaction predict repeat revenue?

## Joining when there's no shared ID

QuickBooks customers have a QuickBooks `Id`; a client's spreadsheet usually
doesn't. `api/services/entity_matching.py` links them:

1. exact **email** match (score 100)
2. exact **phone** match (score 100, US numbers normalized to 10 digits)
3. fuzzy **name** match — "ACME BAKERY, LLC" ≈ "Acme Bakery" — ignoring
   punctuation, word order, and suffixes like LLC/Inc/Co
4. scores 92+ are accepted automatically; 80–91 go to a person to confirm

The upload flow already uses this: if two files share no ID column but both have
names, emails, or phones, the results page suggests a fuzzy link (marked ≈).
In the warehouse, matched customers share one `master_customer_key`
(see DATA_MODEL.md).

## How to build a connector (when you're ready)

Each connector is a small module that returns DataFrames, e.g.
`api/connectors/quickbooks.py`:

```python
def fetch(credentials, since: date) -> dict[str, pd.DataFrame]:
    """Return {"customers": df, "invoices": df, "payments": df}."""
```

Steps for an OAuth app (QuickBooks, Gusto, Shopify, Square, Stripe):

1. Register a developer app with the vendor (Intuit Developer, Gusto Developer
   Portal, ...). Each requires a privacy policy and a redirect URL; QuickBooks
   and Gusto also review apps before production access.
2. Add `GET /api/connect/<vendor>` (redirect to vendor sign-in) and
   `GET /api/connect/<vendor>/callback` (exchange the code for tokens).
3. Store tokens **encrypted** (e.g. Supabase Vault), never in plain columns.
4. `fetch()` pages through the vendor API into DataFrames.
5. Run the existing pipeline; for Data Engineering clients, load into the
   warehouse with `dim_source` set and `source_record_id` = the vendor's ID.
6. Schedule re-syncs (daily is plenty for small businesses).

For warehouses (Snowflake, BigQuery, Postgres), the client creates a
**read-only** user/service account and pastes credentials; `fetch()` runs a
`SELECT` with `pandas.read_sql`.

### Suggested order

Build from what clients actually pick in the questionnaire (save answers to
`intake_responses` first — ROADMAP step 4). A typical small-business order:
Google Sheets → QuickBooks → Square/Shopify → Gusto → Snowflake/BigQuery.
