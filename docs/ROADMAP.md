# Roadmap

What exists today (v0.1) and suggested next steps, roughly in priority order.

## ✅ v0.1 — this version

- Guest questionnaire: individual/business → where data lives → upload
- Flat files: CSV, TSV, TXT, Excel (multi-sheet), JSON, Parquet
- Multi-file categorization: same structure / partitioned by year / different
  entities, with suggested join keys
- Automatic cleaning with a per-column missing-value report, fill/remove advice, and a first-rows preview
- Sample-size snapshot (adapted `sample-size` skill)
- Service selection with a price estimate and consultant booking hand-off
- Signup at download (individual or business + team members); CSV/Excel download
- Runtime-based usage recorded per download
- Supabase schema, Render blueprint, tests, docs, project skills

## Next

### 1. Security — verified sign-in *(do before launch)*
Email-only sign-in is a placeholder. Replace with **Supabase Auth magic links**:
1. Enable Email auth in Supabase.
2. In the web app, call `supabase.auth.signInWithOtp({ email })`.
3. In FastAPI, verify the Supabase JWT (`SUPABASE_JWT_SECRET`) in
   `api/core/security.py` instead of issuing our own tokens.
4. Team members receive an invite email instead of being created silently.

### 2. Payments
Stripe Checkout (or Stripe usage-based billing) charging from `usage_events`.
Decide the open questions in [PRICING.md](PRICING.md) first.

### 3. AI — ask your data
Build the AI service following `data_skills/ask-your-data/SKILL.md`:
schema-only prompts, a whitelisted pandas plan executor, and answers that show
their calculation. Flip `available=True` for `ai` in `api/services/pricing.py`.

### 4. Intake analytics
Save guest answers to `intake_responses` (table already exists) to learn which
data sources and services clients ask for → which connector to build next.

### 5. Consultant workflow
- Pass questionnaire answers to the booking link (Calendly supports prefill).
- Admin page listing new signups, selected services, and usage.
- Automatic invoices (Stripe Invoicing) to cut manual invoicing.

### 6. Connectors ("meet them where their data lives")
Start with the most-requested sources from step 4. Likely order:
Google Sheets → QuickBooks → Shopify/Square → Gusto → Snowflake/BigQuery.
Each connector returns DataFrames into the same pipeline; QuickBooks/Gusto act
as enhancers joined with fuzzy matching. Full plan: [CONNECTORS.md](CONNECTORS.md).

### 7. Self-serve analysis and dashboards
- Auto-generated summary (key trends) for Data Exploration
- Starter dashboards for common small-business data (sales, customers, payroll)
- Use the Anthropic data plugin skills (`build-dashboard`, `create-viz`) to
  prototype templates.

### 8. Data engineering tier
The schema is ready (`db/warehouse.sql`, [DATA_MODEL.md](DATA_MODEL.md)): one
shared star schema, `client_id` + Row Level Security. Still to build: loaders
that write cleaned files into it, a review screen for "review" fuzzy matches,
consent, and storage pricing.

### 9. Mobile apps
React Native + Expo app (iOS/Android) calling the same API, following the
template repo's `mobile/` approach.

### 10. Team growth
Consultant accounts and assignment of client projects when you bring on help.
