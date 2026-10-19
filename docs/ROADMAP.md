# Roadmap

What exists today and what comes next, in priority order.

## ✅ v0.2 — consultant run-through

- Email + password sign-in (Supabase Auth; local hashes in development)
- Consultants see and test all seven services (AI as a preview)
- Data Engineering: preview how data will be transformed for the warehouse,
  and why, then accept (with storage consent) or not — [ARCHITECTURE.md](ARCHITECTURE.md)
- Consultations: Cal.com webhook + consultant confirmation; free call, then
  the paid booking link
- `scripts/setup_database.py` creates and checks every table
- Step-by-step first run: [RUN_THROUGH.md](RUN_THROUGH.md)

## ✅ v0.1

- Guest questionnaire: individual/business → where data lives → upload
- Flat files: CSV, TSV, TXT, Excel (multi-sheet), JSON, Parquet
- Multi-file categorization: same structure / partitioned by year / different
  entities, with suggested join keys
- Automatic cleaning with a per-column missing-value report, fill/remove advice, and a first-rows preview
- Sample-size snapshot (adapted `sample-size` skill)
- Service selection with a price estimate and consultant booking hand-off
- Signup at download (individual or business + team members); CSV/Excel download
- Runtime-based usage recorded per download
- Supabase schema, Render setup (`render.yaml`), tests, docs, project skills

## Next

### 1. Security — verified sign-in *(finish before launch)*
- ✅ Email + password through **Supabase Auth** (`api/services/auth.py`), with
  local hashes when Supabase isn't configured. Turn on Supabase's "Confirm
  email" setting for verified addresses (the app already handles it).
Still to do:
1. ✅ **Forgot password** ("Forgot password?" on sign-in, "Reset password" in
   Settings → Account): Supabase emails the link back to `/reset-password`
   (add it to Supabase's Redirect URLs); local mode logs the link instead.
2. **Invite emails** for team colleagues and consultant-added clients. Today the
   first sign-in with their email sets the password, so whoever gets there first
   chooses it — sign them in promptly.
3. Consultants: add two-factor sign-in (Supabase MFA), since a consultant can
   open every client assigned to them. (Consultants are already approved one by
   one by an admin; there is no shared access code.)
4. Email the person when an admin approves or denies their consultant application
   (today they see it — with the reason and the date they can apply again — in
   Settings, and their workspace switches on its next load), and email the
   invitation when an admin adds someone to the consultant invite list (today
   the admin copies the sign-up link and sends it).
5. Google and Apple sign-in (Supabase Auth providers) — no table changes needed.
6. ✅ Sign-in lockout: 3 wrong passwords in a row lock sign-in for 15 minutes, each try says how many
   are left, and a password reset unlocks at once (`api/services/login_guard.py`). Later: a per-IP
   limit too (needs care behind Render's proxy, so one shared IP doesn't lock everyone out).

### 2. Payments — ✅ built, in test mode
Stripe subscriptions (each plan has a monthly and an annual price), Checkout,
a signed webhook that keeps `subscriptions` in sync, the billing portal for
"cancel anytime". (Consulting sessions are paid on Cal.com instead.) To go live, follow
[PAYMENTS.md](PAYMENTS.md) (set real prices in `api/services/pricing.py`, run
`scripts/sync_stripe.py`, add the webhook and keys). Still open: charging
pay-per-minute cleaning without a plan through Stripe (today it's recorded in
`usage_events`), and selling the one-off packages through Checkout.

## Product priorities (in this order)

The goal: a business gets every data need met in one place — answered by the
tools automatically when possible, and by a consultant when they want a person.

### 3. Data cleaning — make it effortless *(priority 1)*
- ✅ Column names, header problems, wrong data / wrong format, Power Query-style
  steps, missing values, 10-per-page tables, confirmations.
- Save a client's cleaning steps as a reusable **recipe** (re-apply to next
  month's export in one click; stored as JSON, no client data).
- Standardize categories ("CA", "Calif.", "California") and flag near-duplicates.
- A one-page "what we changed" PDF for the client's records.

### 4. Data visualization — dashboards *(priority 2)*
- Starter dashboards for common small-business data (sales, customers,
  expenses, payroll) chosen automatically from the columns.
- Save and share a dashboard (a link, or a PDF) — needs saved dashboard
  definitions (no client data) or managed storage (step 5).
- Use the Anthropic data plugin skills (`build-dashboard`, `create-viz`) to
  prototype templates.

### 5. Data engineering — managed storage *(priority 3)*
For clients who want their data stored and managed for them. The schema is
ready (`fact.account_data` in `db/schema.sql`, [DATA_MODEL.md](DATA_MODEL.md)):
one shared table in the unified shape, `account_id` + Row Level Security.
1. ✅ `storage_consents` (explicit opt-in, terms version) — [DATABASE.md](DATABASE.md).
2. ✅ Design change recorded in ARCHITECTURE.md ("Managed storage"), plus
   `/privacy`, `/terms`, `/storage-terms` and `/data-deletion` pages.
3. ✅ Loader with a **preview → accept** screen: every transformation step with
   its "why", the first rows of each table, consent, `load_runs` audit, no
   duplicates on reload. Next: fuzzy-match review (customers named slightly
   differently across sources), employees (`dim_employee`), and loading
   several tables of one upload in one go.
4. ✅ "Delete stored data" button (immediate; consent withdrawn) and "Download as
   CSV". Full account deletion is by email for now (`/data-deletion`).
5. ✅ "Use my stored data": the client (own login) and consultant open the same
   stored table in every service. Next: saved dashboards that refresh on their
   own, and scheduled reloads.
6. Storage pricing in the Data Engineering plan.

### 6. QuickBooks *(priority 4)*
The first connector, and the "enhancer" for everything else: customers,
invoices and payments joined to the client's own files by fuzzy matching.
Tables `connections` + `sync_runs` ([DATABASE.md](DATABASE.md)); steps in
[CONNECTORS.md](CONNECTORS.md) (Intuit developer app, OAuth, encrypted tokens,
daily sync). Works without storage (pulled into memory like an upload) and
loads into the warehouse for managed-storage clients.

### 7. AI — ask your data with Gemini *(nice to have)*
Build the AI service following `data_skills/ask-your-data/README.md`:
schema-only prompts (the model sees column names and summaries, not raw rows),
a whitelisted pandas plan executor, and answers that show their calculation.
Use the Gemini API (`GEMINI_API_KEY`) with **caps like the Video Game Stats
Tracker**: daily questions per plan in `ai_usage_daily`, plus a global monthly
budget. When the AI can't answer, offer the consultant (free 30 minutes). Flip
`available=True` for `ai` in `api/services/pricing.py`.

## Gaps compared with leading SaaS products (October 2026 review)

What the best data and small-business SaaS products (Stripe, Notion, Airtable,
Looker Studio, Power BI, HubSpot, QuickBooks itself) do that this platform
doesn't yet — in the order I'd build them. ✅ = done in the October 2026 round.

| # | Gap | Why it matters | Size |
|---|---|---|---|
| ✅ | Forgot / reset password | Basic trust; every app has it | S |
| ✅ | Settings area (account, billing, integrations, roles) | One predictable place for everything that isn't the work | M |
| ✅ | Business context + the client's own dashboard tabs | Dashboards answer *their* questions, and grow with them | M |
| 1 | **Email notifications** (consultant approved, data loaded, a refresh failed, weekly summary) | People come back when something tells them to; today nothing is emailed except Supabase's | M |
| 2 | **Shareable, scheduled dashboards** (a read-only link, and a weekly PDF/email) | Owners want the numbers without signing in; consultants deliver reports this way | M |
| 3 | **Two-factor sign-in** for consultants and admins (Supabase MFA) | They can open client data; required before real clients | S |
| 4 | **Activity log** (who loaded, deleted, connected, changed a plan — per account) | Trust and support: "what happened to my data?"; also needed for audits | S |
| 5 | **Team roles beyond owner/member** (viewer, editor) and invite emails | Businesses share dashboards with staff who shouldn't change billing | M |
| 6 | **Usage & billing history** in the app (downloads, minutes, invoices) | Fewer "why was I charged?" questions; Stripe's portal covers invoices only | S |
| 7 | **Several datasets at once** (a "data catalog": pick tables, join them in the workspace, not only at load) | Most businesses have more than one table; the load step can join, the workspace can't yet | L |
| 8 | **Help center + in-app chat** (searchable how-tos; message your consultant) | Self-serve support; consultants answer in context | M |
| 9 | **Status page and error monitoring** (Sentry or similar) | Know about a broken refresh before the client does | S |
| 10 | **API keys / webhooks for clients** | Larger clients want to push data in or pull results out | M |

## Later

### 8. Consultant workflow
- ✅ Consultants table, "do it for me" signup, "Working on" client picker
  (searchable), "Add a client", downloads billed to the client. Tools follow
  the client's package; admins get everything free.
- ✅ Free 30-minute first consultation, then $80/hour sessions booked and paid on
  Cal.com (Data Strategy & Discovery is paid from the first hour).
- ✅ Consultation tracking: Cal.com webhook (booked / rescheduled / canceled /
  ended) + the consultant's "Confirm it happened"; then the paid Cal.com link
  (`PAID_BOOKING_URL`). Booking links carry the client's name, email and id.
- ✅ Consultants see all seven services, AI as a preview.
- Reassign a client to another consultant; "invite this client" email; notes
  per client; a list of bookings from people without an account yet.
- Pass questionnaire answers to the booking link (Cal.com `metadata[...]`).
- Admin page: new signups, plans, usage, hours.

### Intake analytics
Save guest answers to `intake_responses` (table already exists) to learn which
data sources and services clients ask for → which connector to build after QuickBooks.

### Other connectors
After QuickBooks: Google Sheets → Square/Shopify → Gusto → Snowflake/BigQuery
([CONNECTORS.md](CONNECTORS.md)). Warehouse-sized sources can use the optional
PySpark engine, already set up but off by default ([SPARK.md](SPARK.md)).

### Mobile apps
React Native + Expo app (iOS/Android) calling the same API, following the
template repo's `mobile/` approach.

### Team growth
Consultant accounts and assignment of client projects when you bring on help.
