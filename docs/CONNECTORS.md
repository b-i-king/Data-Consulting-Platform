# Cloud data sources and integrations

**Question:** *"Do you have the infrastructure for cloud data sources and
integrations like QuickBooks and Gusto?"*

## Where things stand today

Settings → **Integrations** has two sections (`api/services/connections.py`):

- **Cloud storage — read where it lives, never copied.** ✅ First one built:
  **Supabase database** (the client's own Supabase, or any PostgreSQL). The
  client pastes a connection string (ideally a **read-only** user); the server
  checks it (a public host, it connects, it lists tables) and saves it
  **encrypted** (Fernet, key derived from `JWT_SECRET_KEY`). Tables are listed
  **schema by schema, and only tables with data are offered**: PostgreSQL's
  own free row estimate answers "has rows?" first, and only when it says 0 or
  "unknown" (and for views) do we ask the table itself with a one-row
  `EXISTS` check (2-second limit each, at most 300 per listing). Empty tables
  are hidden and refused if opened (422) — nothing is read, cleaned or
  analyzed for them. A table with data can be opened in the workspace — for
  analysis, dashboards, ML and AI — read in a READ ONLY transaction, in chunks
  of 5,000 rows, stopping at 100,000 rows or the upload size limit
  (`MAX_UPLOAD_MB`), whichever comes first (the workspace then says "the first
  N rows"), never stored. For cloud data,
  Data Cleaning is a **report only** (what's missing, what looks off — fix it at
  the source) and "Load into database" is switched off: the client already
  manages that data. Code: `api/services/cloud_sources.py`.
  It can also be connected with **"Sign in with Supabase"** (OAuth, when the
  platform's Supabase OAuth keys are set): no password changes hands; the
  client picks a project and we read through Supabase's Management API
  read-only endpoint (`POST /v1/projects/{ref}/database/query/read-only`, run
  as Supabase's `supabase_read_only_user`) — tables in 5,000-row pages by row
  address (`ctid`), views in one read capped at 20,000 rows. Same rules:
  empty tables hidden, never stored.
- **Business tools — QuickBooks Online and Xero.** ✅ "Connect" sign-in on the
  tool's own site (OAuth 2.0, read-only scopes; `api/services/oauth.py`). Once
  connected, two things work **live, never stored** (`api/services/business_tools.py`):
  - **Quick look** (included with any paid plan): income, expenses, net income, what
    customers owe, what the business owes, month by month, top customers, for
    any dates. QuickBooks: its own Profit and Loss (by month), Sales by
    Customer and A/R / A/P Aging reports (4 calls). Xero: added up from sales
    invoices + money received and bills + money spent, before tax, without
    drafts, voided items and transfers — labeled an **estimate**, because
    Xero keeps its own P&L and Balance Sheet reports for App Partners.
  - **Open records in the workspace**: QuickBooks invoices, sales receipts,
    payments, expenses, bills (one row per transaction line, with the
    platform's unified column names from `unified.py`); Xero sales invoices,
    bills, bank transactions, payments, contacts. Empty record types are found
    with one cheap call each (QuickBooks `COUNT(*)`, Xero a one-item page) and
    hidden. Up to 20,000 records per open, and no more than the upload limit.

  **Stored sync (Data Engineering) — ✅ built** (`api/services/sync.py`):
  "Store your records" on the card (or "Turn on the stored sync" at the end
  of Get Started) stores the full history once, then only what changed
  (QuickBooks `MetaData.LastUpdatedTime`, Xero `If-Modified-Since`), on the
  refresh schedule (daily by default; `python scripts/run_syncs.py` as a
  Render Cron Job). Customers / vendors / products go to `fact.entities`,
  matched with the client's other sources; records also found in uploaded
  files are reported; voided Xero records are removed. Every call is counted
  by the CallMeter, so a sync stops cleanly at the day's allowance and
  carries on the next day. **Design rule (to keep storage and memory low):** pull only what changed since the
  last sync (QuickBooks Change Data Capture / `MetaData.LastUpdatedTime`), skip
  entities that return no rows, and load page by page — `row_hash` in
  `fact.records` already skips rows whose content didn't change, and an
  empty table is never loaded.

**What clients see as planned** (`COMING_SOON` in `api/services/connections.py`,
shown under each section as "Coming soon" / "Later" — nothing can be set up
for them yet):

| Section | Coming soon | Later |
|---|---|---|
| Cloud storage | Snowflake, Google BigQuery, Amazon Redshift, MySQL (incl. Amazon RDS / Aurora MySQL), Microsoft SQL Server / Azure SQL | Databricks, Microsoft Fabric / Azure Synapse, Amazon S3 (files), Google Drive / Sheets |
| Business tools | Square, Shopify, Stripe | Gusto, FreshBooks, Clover, Toast, HubSpot, Salesforce |

Already working through the PostgreSQL connector (no new code): Amazon RDS /
Aurora PostgreSQL, Google Cloud SQL for PostgreSQL, Azure Database for
PostgreSQL and Neon — anything that speaks PostgreSQL. To launch one, build
it, add it to `CONNECTORS`, remove it from `COMING_SOON`, and flip its
`available` in the questionnaire (`api/services/intake.py`).

Files are still the main way in (every format pandas can safely read — see
DATA_CLEANING.md).

What *is* ready, and what each connector will plug into:

| Piece | Status |
|---|---|
| Common pipeline: DataFrame → categorize → clean → preview | ✅ built — a connector only has to return DataFrames |
| Fuzzy matching for records with no shared ID (`entity_matching.py`) | ✅ built and tested |
| Table to store synced data (`fact.records` + `fact.fields`, `source_system` = the connector; QuickBooks fields mapped in `fact.field_map`) | ✅ built and tested |
| Supabase / PostgreSQL (cloud storage): connect, list tables, read one — encrypted login, read-only | ✅ built and tested |
| "Sign in with Supabase" (OAuth + Management API, read-only) | ✅ built; tested against Supabase's documented API shapes |
| QuickBooks and Xero: OAuth sign-in, encrypted tokens (refreshed and rotated), live quick look, open records live | ✅ built; tested against the documented API shapes — test with the sandbox / a real organization once keys are set |
| QuickBooks and Xero: stored sync on the refresh schedule (incremental, entities, duplicates reported) | ✅ built; tested against the documented API shapes and on PostgreSQL |

### A note on the "connectors" in your Claude account

The connectors on your Claude **data plugin** page (BigQuery, Snowflake,
Databricks, Amplitude, Hex, Atlassian, ...) let **Claude** query those systems
while *you* work — for example during a consulting engagement. They are not
part of this web app, and your clients can't use them. In this session the
BigQuery connector was waiting for authorization and several others couldn't
connect, so they're worth checking in your Claude connector settings if you plan
to use them for client work.

## Business tools come with a paid plan

QuickBooks, Xero and Wave give the platform ONE free API allowance shared by
every client (Xero's free plan: 5 connected businesses). So connecting them —
and reading them live — comes with **any paid plan** (`entitlements.has_paid_plan`:
a live subscription with any tool; admins and testers count as paid). A free
account gets 402 with a pointer to the alternative: **download an export from
the tool and upload it** — the analysis is the same and uses none of the
shared allowance. Supabase / PostgreSQL stays free (it's the client's own
database, no shared allowance).

When a tool's spots are full and a paying client connects, spots held by
accounts whose paid plan ended are released first (Disconnect, including at
Xero), in `api/services/integration_slots.py`.

## Xero data: no AI and no machine learning

Xero's developer terms (developer.xero.com/xero-developer-platform-terms-conditions,
clause 7(b)) say API data "cannot be used to train, fine tune, adapt, or
enhance any AI Models" (predictive analytics included) and can't be passed to
another company without the user's consent. So for data from Xero — live
records, or stored data that includes Xero records — the **AI** service
(Beacon and the AI skills) and the **Data Science & Machine Learning** service
(predictions, segments, reorder planning, multi-factor analysis) are off.
Cleaning, exploring and dashboards work as usual.

Enforced on the server (`api/services/xero_guard.py`): the API remembers a
fingerprint (SHA-256) of every Xero file it sends; `POST /api/ai/ask` and the
ML / AI skills refuse a matching upload, or one named like the live Xero
records (`xero.<type>.csv`), with 403. The workspace shows a note instead of
those tools. QuickBooks and Wave data are not restricted.

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
   `fact.records` with `source_system` = the vendor and `record_id` = the
   vendor's ID (QuickBooks: `unified.flatten_quickbooks()`).
6. Schedule re-syncs (daily is plenty for small businesses).

For warehouses (Snowflake, BigQuery, Postgres), the client creates a
**read-only** user/service account and pastes credentials; `fetch()` runs a
`SELECT` with `pandas.read_sql`.

### Suggested order

Build from what clients actually pick in the questionnaire (save answers to
`intake_responses` first — ROADMAP step 4). A typical small-business order:
Google Sheets → QuickBooks → Square/Shopify → Gusto → Snowflake/BigQuery.

## Setting up the "Connect" buttons (one pair of keys per tool)

Every "Connect" button needs the PLATFORM's own Client ID + Client Secret from
that tool's developer site — set once on Render, never per client. A tool
without keys simply shows as "coming soon", so add only the ones you want.
All of them use the **same redirect URI**:

    https://<your Render API address>/api/connections/oauth/callback

(The API knows its own address from Render's `RENDER_EXTERNAL_URL`; set
`PUBLIC_API_URL` only if you use a custom domain. Locally it is
`http://localhost:8000/api/connections/oauth/callback`.)

| Tool | Where | Settings on Render | Notes |
|---|---|---|---|
| QuickBooks | developer.intuit.com → Dashboard → Create an app → QuickBooks Online and Payments → scope **Accounting** → Keys & credentials (Development) → add the redirect URI | `QUICKBOOKS_CLIENT_ID`, `QUICKBOOKS_CLIENT_SECRET` (`QUICKBOOKS_ENVIRONMENT` = `sandbox` by default) | Development keys only work with Intuit's free **sandbox company** — test there first. Production keys come after Intuit's app assessment; then set `QUICKBOOKS_ENVIRONMENT=production`. |
| Xero | developer.xero.com → My Apps → New app → **Web app** → redirect URI → Configuration → generate a secret | `XERO_CLIENT_ID`, `XERO_CLIENT_SECRET` | Free **Starter** tier: up to **5 connected organizations** — enough for a friend's business to test. "Disconnect" removes the organization connection at Xero, freeing a slot. Scopes asked (read-only, granular — required for apps made after March 2, 2026): `accounting.invoices.read accounting.payments.read accounting.banktransactions.read accounting.contacts.read offline_access`. |
| Wave | developer.waveapps.com → Manage Applications → Create an application → redirect URI | `WAVE_CLIENT_ID`, `WAVE_CLIENT_SECRET` (`WAVE_SCOPES` = the read-only permissions asked for) | The business must be on **Wave Pro or Advisors** to connect (Wave's rule since May 2025). Wave's API is GraphQL; it shares invoices, customers and products — **not** expenses, bills or reports. |
| Supabase (optional) | supabase.com → your organization → OAuth Apps → Add → redirect URI → permissions **Projects: read** and **Database: read** | `SUPABASE_OAUTH_CLIENT_ID`, `SUPABASE_OAUTH_CLIENT_SECRET` | Optional: without it, clients paste a connection string (no keys at all). PKCE is used. |

### Xero's plan limits — safeguards

Xero's free **Starter** plan allows **5 connected organizations** and **1,000
API calls per organization per day** (paid plans: 50+ and 5,000). Going over
gets the app blocked, so (`api/routers/connections.py`, `CallMeter` in
`api/services/business_tools.py`):

| Safeguard | How |
|---|---|
| Never a 6th connection | "Connect Xero" is refused once `XERO_MAX_CONNECTIONS` (5) organizations are connected platform-wide — checked at Connect AND again when the sign-in comes back (two people could click at once); a sign-in that arrives too late is undone at Xero (connection removed, tokens revoked). The card says "connections are full" instead of a Connect button. Reconnecting your own Xero reuses your spot. |
| One sign-in = one spot | If the person shares several organizations in Xero's sign-in, we keep the first and disconnect the rest at once. |
| Disconnect frees a spot | "Disconnect" removes the organization connection at Xero, not just ours. |
| Never past the daily limit | Each organization gets a budget of 90% of `XERO_DAILY_CALL_LIMIT` (900 of 1,000) per UTC day. Every call is counted and saved on the connection (`api_calls_day` / `api_calls_count`), so a restart doesn't reset it. Xero's own count (the `X-DayLimit-Remaining` header on every answer) wins when it is higher. Past the budget, requests are refused with "used up — starts over at midnight UTC". |
| Under 60 a minute | At most 55 calls per organization in any 60 seconds; a request waits up to 10 seconds, else is refused. |
| If Xero still says 429 | The message says which limit; a daily one stops calls for the rest of the day. |

What a Xero action costs: quick look ≈ 3 calls; "Open records in the
workspace" list = 5 calls; opening one record type = 1 call per 1,000 records.
So 900 calls a day is a lot of use for one small business. On a paid plan, set
`XERO_MAX_CONNECTIONS` and `XERO_DAILY_CALL_LIMIT` to the new numbers.

### QuickBooks' limits — safeguards

Intuit's free **Builder** tier (App Partner Program) allows **500,000
"CorePlus" API calls a month for the whole app** — every read we make (query,
report, company info) is one. Past that, Intuit doesn't bill you: it
**blocks** every CorePlus call until the next month and shows the app as
"Offline" in every client's QuickBooks. Per company it also allows 500 calls a
minute and 10 at a time. There is no limit on how many companies connect.
So (same `CallMeter` as Xero):

| Safeguard | How |
|---|---|
| Never past the month | The whole app stops at 90% of `QUICKBOOKS_MONTHLY_CALL_LIMIT` (450,000), counted in `dim.api_usage` (one row per tool per month). Clients see "paused until the 1st — uploading an export works meanwhile". |
| One company can't use everyone's month | Each company gets `QUICKBOOKS_DAILY_CALL_LIMIT` (1,000) calls per UTC day, saved on its connection. |
| Room for everyone | `QUICKBOOKS_MAX_CONNECTIONS` (15): 15 companies × 1,000 calls × 30 days = 450,000 — even if every company used its full share every day, the month still fits. Same "connections are full" behavior as Xero. |
| Under 500 a minute | At most 450 calls per company in any 60 seconds (our calls are one at a time, so the 10-at-once limit is never reached). |

What a QuickBooks action costs: quick look = 4 calls (the reports);
"Open records in the workspace" list = 5 calls; opening one record type = 1
call per 1,000 records. To allow more companies, raise
`QUICKBOOKS_MAX_CONNECTIONS` and lower `QUICKBOOKS_DAILY_CALL_LIMIT` so that
companies × daily × 30 stays under 450,000 — or move to Intuit's paid Silver
tier and raise `QUICKBOOKS_MONTHLY_CALL_LIMIT`.

**Fewest keys:** Xero only = 2 settings; Xero + QuickBooks = 4; all three = 6.
Tokens are saved ENCRYPTED (key from `JWT_SECRET_KEY`), refreshed
automatically before they expire, and the rotated refresh token is saved each
time. The `state` sent with each sign-in is an encrypted, 15-minute ticket
(who is connecting, and Supabase's PKCE verifier), so nothing is stored
between "Connect" and the callback.

## Get Started paths and "Add another source"

**Get Started** (`web/components/wizard/Wizard.tsx`) takes one of three paths
after "Where is your data?" — each with the workspace's rules:

| Path | Steps after "Your data" |
|---|---|
| Files on my computer | Upload → Clean & preview → Your needs → Next steps (sign up) |
| Cloud storage (Supabase / PostgreSQL) | Account → Connect & choose a table → Preview (read-only report) → Your needs (no "just download") → Next steps (workspace opens on the table) |
| Business apps (QuickBooks, Xero) | Account → Connect → Quick look (free; optional record preview) → Your needs → Next steps ("Turn on the stored sync") |

The account comes first on the last two (a connection is saved on it). The
provider's sign-in returns to `/get-started/connect` (`return_to=start`; from the
workspace, `return_to=app` returns to `/workspace/settings/integrations`), and the wizard
picks up at "Connect" from what it saved in the tab's sessionStorage. A
"coming soon" choice (Snowflake, Square...) offers a consultation or the file
path. QuickBooks / Xero show "connect now" only when the platform's keys are
set (`intake.data_locations()`). The free quick look covers the **last 12
months**; full history is the stored sync.

**Add another source** (workspace, next to "Change data"): a file, stored
data, a database table or QuickBooks / Xero records open together in the tab
— same-column tables stacked, others as tabs, with how they relate (a shared
ID, or names matched). The stricter rule wins: any cloud or live source makes
it all report-only, with storing off. Nothing new is stored.

### Wave — what it reads, and its limits

Wave (`api/services/business_tools.py`, Wave section) is connected like
QuickBooks and Xero ("Connect Wave", read-only), with the client keys sent as
form fields (Wave's way). Its API shares **invoices (with items), customers
and products**, so:

- **Quick look:** sales by month (invoice totals before tax, drafts left out),
  what customers still owe on those invoices, and top customers. Expenses and
  "you owe" show as "—": Wave's API doesn't share them (upload an export).
- **Open records:** invoices (one row per item), customers, products.
- **Stored sync:** invoices (invoice/sent) into `fact.records`, with
  customers and products in `fact.entities` (`unified.flatten_wave_invoice`,
  `WAVE_FIELD_MAP`, seeded in `fact.field_map` as system `wave`). Only invoices
  changed since the last run are asked for (`modifiedAtAfter`; if Wave refuses
  that filter, everything is read and unchanged rows are skipped).

**Wave's free plan.** Since May 26, 2025 Wave allows the "Connect Wave"
sign-in (OAuth) only for businesses on **Wave Pro or Advisors**. A business on
the free plan can't connect that way. For TESTING with your own business,
**admins** can paste a Wave **full access token** instead (developer.waveapps.com →
your application → full access token; `POST /api/connections/wave/token`,
"Admins: use your own Wave with a personal token" on the Wave card). Wave
allows those tokens only for development and personal use, so clients never
see the option. It's saved encrypted, doesn't need the Wave app keys, and
still goes through the limits below. Without Pro or a token: export from Wave
(Settings → Data Export, or a report's CSV) and upload the file.

**Limits (not pulling data rapidly):** Wave allows about **60 requests a
minute per connection** and **5,000 a day for the whole app**. The CallMeter
paces each business under **50 a minute**, gives it **`WAVE_DAILY_CALL_LIMIT`
(500) calls a day**, and the app allows **`WAVE_MAX_CONNECTIONS` (9)**
businesses: 9 × 500 = 4,500, under 90% of the 5,000 even if every business
used its full share. Pages are 50 records. A quick look is usually 1–2 calls;
listing record types 3.
