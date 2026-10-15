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
1. **Forgot password**: `supabase.auth.reset_password_for_email(email,
   {"redirect_to": SITE_URL + "/reset-password"})` plus a small page that sets
   the new password. Until then, reset from Supabase → Authentication → Users.
2. **Invite emails** for team colleagues and consultant-added clients. Today the
   first sign-in with their email sets the password, so whoever gets there first
   chooses it — sign them in promptly.
3. Consultants: replace the shared `CONSULTANT_ACCESS_CODE` with two-factor
   (Supabase MFA), since a consultant can open every client assigned to them.
4. Google and Apple sign-in (Supabase Auth providers) — no table changes needed.
5. Sign-in rate limiting on our side (Supabase rate-limits its own endpoints).

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
ready (`db/warehouse.sql`, [DATA_MODEL.md](DATA_MODEL.md)): one shared star
schema, `client_id` + Row Level Security.
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

## Later

### 8. Consultant workflow
- ✅ Consultants table, "do it for me" signup, "Working on" client picker
  (searchable), "Add a client", every tool unlocked, downloads billed to the client.
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
([CONNECTORS.md](CONNECTORS.md)).

### Mobile apps
React Native + Expo app (iOS/Android) calling the same API, following the
template repo's `mobile/` approach.

### Team growth
Consultant accounts and assignment of client projects when you bring on help.
