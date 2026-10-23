# Code map — what each file does and why

A guided reading order for understanding (and critically reviewing) the code.
Each source file also starts with a header comment explaining itself.

## Read in this order

### 1. Follow one request: "Clean my data"

1. **`web/components/wizard/StepUpload.tsx`** — the user picks files. `addFiles()`
   checks type/size in the browser; `analyze()` calls `analyzeFiles()`.
2. **`web/lib/api.ts` → `analyzeFiles()`** — builds a multipart form and POSTs
   to `/api/files/analyze`.
3. **`api/routers/files.py` → `analyze_files()`** — the endpoint. It is short on
   purpose: load → run pipeline → shape JSON.
4. **`api/services/pipeline.py`**
   - `load_uploads()` enforces limits and calls the reader.
   - `run_pipeline()` = categorize → stack each group → clean → sample size.
5. **`api/services/file_reader.py` → `read_file()`** — looks up the format in the
   `FORMATS` registry (CSV/Excel recommended + every safe pandas format), unwraps
   .zip/.gz first, tries several text encodings, and splits multi-table files.
6. **`api/services/categorizer.py` → `categorize()`** — the 6-step algorithm in
   the file header. Then `combine_group()` stacks files with `source_file`.
7. **`api/services/cleaning.py` → `clean_dataframe()`** — Steps 1–6; returns
   `(cleaned_df, CleaningReport)`.
8. **`api/services/sample_size.py` → `sample_size_snapshot()`**.
9. **`api/services/pricing.py` → `runtime_cost()`** — seconds → billable minutes → $.
10. Back in the browser: **`StepResults.tsx`** renders the report and
    **`DataPreview.tsx`** pages through the rows.

### 2. Follow signup and download

1. **`StepAccount.tsx` → `AuthPanel.submit()`** → `signup()` in `lib/api.ts`.
2. **`api/routers/accounts.py` → `signup()`** → `account_store.create_account()`
   (creates a team for businesses) → `create_access_token()`.
3. Token saved by **`web/lib/session.ts`**.
4. **`DownloadPanel.download()`** → `downloadCleaned()` re-sends the files.
5. **`api/routers/files.py` → `download_cleaned()`** — `Depends(get_current_account)`
   rejects missing/expired tokens; re-runs the pipeline; records usage; streams
   CSV or Excel.

### 3. Follow a skill run: the dashboard

1. **`web/components/explore/Dashboard.tsx`** — calls `runSkill("build-dashboard", input)`
   with the files and missing-value choices; range and comparison buttons just switch
   between precomputed series. Changing a filter (customer type, age group…) calls it again.
2. **`api/routers/skills.py` → `run_skill_endpoint()`** → **`api/services/skills_runner.py`**
   — cleans the uploads with the same pipeline, then calls the registry.
3. **`data_skills/registry.py` → `run_skill()`** — checks the options and calls
   **`data_skills/build-dashboard/skill.py` → `run()`**, which finds the type column
   (`find_kpi_column`) and the customer/demographic filters (`find_filter_columns`).
4. **`data_skills/_time.py`** — `window()` (which dates each range covers and
   what it's compared with) and `period_series()` (one row per day/week/month
   with the previous period and % change).
5. Back in the browser: **`components/charts/LineChart.tsx`** draws it.

Every other skill follows the same path; **`SkillsPanel.tsx`** shows any
skill's summary, findings, tables, charts (through **`charts/ChartView.tsx`**) and
downloads; the EDA report is shown section by section by **`EdaReport.tsx`**. Only skills of the
services the client chose are offered; the rest are badges from
**`LockedService.tsx`** that add the service.

### 4. Follow a missing-value choice

1. **`web/components/wizard/MissingValues.tsx`** — the per-column dropdowns; **Apply
   changes** calls `onApply`.
2. **`Wizard.tsx` → `applyActions()`** — re-sends the files with `column_actions` to
   `/api/files/analyze` and stores the choices for the dashboard, skills and download.
3. **`api/services/pipeline.py` → `parse_column_actions()`** →
   **`api/services/cleaning.py` → `_missing_advice()`** (levels, skewness, fill
   suggestion) and **`_apply_column_actions()`** (drop / fill).

### 5. Follow a sign-in to the workspace

1. **`app/page.tsx`** — "Start as a guest", and under it **Sign in** / **Create an
   account** → **`app/signin/page.tsx`** → **`components/auth/AuthForm.tsx`**.
2. **`components/workspace/Workspace.tsx`** — loads the plan with `getMyServices()`
   (**`api/routers/accounts.py` → `my_services()`** → **`api/services/entitlements.py`**),
   orders the left pane (Dashboard or Data Cleaning first; services not on the plan
   grayed out with an ⓘ), then asks for files and shows the chosen service, with a
   **Skill** menu at the top. The default is the built-in view (cleaning report,
   dashboard) or the skill marked `"primary": true` in `data_skills/catalog.json`,
   run automatically by `SkillsPanel` (`fixedSkill`, `autoRun`).

## File reference

| File | One-line purpose | Change it when… |
|---|---|---|
| `api/core/config.py` | Settings from env vars | Adding a new setting |
| `api/core/database.py` | Connection pool | Changing DB connection behavior |
| `api/core/security.py` | JWT create/verify | Changing login tokens |
| `api/services/auth.py` | Passwords: Supabase Auth, or local scrypt hashes | Sign-in rules, Google/Apple later |
| `api/services/consultations.py` + `api/routers/consultations.py` | Free consultation → paid link; Cal.com webhook; consultant confirmation | Booking / consultation rules |
| `api/services/warehouse_loader.py` | Plans a load: mapping, steps with "why", previews, fingerprint (pandas only) | Changing how files fit the warehouse |
| `api/services/combine.py` | Stack (union) or join tables of one upload before loading | Changing how tables are combined |
| `api/services/cloud_sources.py` | A client's own Supabase / PostgreSQL: encrypted login, list tables, read one (read-only); "Sign in with Supabase" reads through the Management API | Cloud integrations |
| `api/services/oauth.py` | "Connect" sign-in (OAuth 2.0) for QuickBooks, Xero, Supabase: the state ticket, code swap, token refresh, encrypted saving | Adding a sign-in integration |
| `api/services/partners.py` + `api/routers/partners.py` | The home page's "Trusted by" carousel: who may apply, the logo turned into a small WebP, admin approval, shown only while paid | Partner rules ([ARCHITECTURE.md](ARCHITECTURE.md) 2b) |
| `web/components/home/PartnerCarousel.tsx`, `ThemedLogo.tsx`, `web/components/workspace/PartnerCard.tsx`, `AdminPartners.tsx` | The carousel; each logo drawn as a silhouette in the theme's colors; the business's application (Settings → Account); the admin review and "Add a company you work with" | How partners look and apply |
| `web/lib/faq.ts` + `web/components/FaqSection.tsx` | The FAQ: home page (with Google's FAQ markup) and Settings → Help & FAQ | Changing a question or answer |
| `web/components/SiteMenu.tsx` | The header's ☰ menu: Sign in (or Help & FAQ), the home page's sections, the legal pages | Header links |
| `api/services/pushdown.py` + `api/services/warehouses.py` + `web/components/workspace/SummarizePanel.tsx`, `WarehouseCard.tsx` | Summarize IN the client's source (PostgreSQL / Redshift, Snowflake, BigQuery, Salesforce): the checked summary request, the query per source, the connectors | Adding a warehouse, or a new kind of summary ([CONNECTORS.md](CONNECTORS.md)) |
| `api/services/google_sheets.py` | "Connect Google Sheets": a tab read live, read-only | Google Sheets |
| `api/services/integration_slots.py` | Business tools come with a paid plan; frees spots held by ended plans when a tool is full; Disconnect | Who may connect QuickBooks / Xero / Wave ([CONNECTORS.md](CONNECTORS.md)) |
| `api/services/xero_guard.py` | Keeps Xero data away from AI and ML (Xero's terms): fingerprints the Xero files the API sends; the AI endpoint and ML / AI skills refuse them | What's off for Xero data |
| `api/services/business_tools.py` | QuickBooks, Xero and Wave read live: the quick look, the record types, one record type as CSV (never stored) | QuickBooks / Xero numbers |
| `web/components/workspace/QuickLook.tsx` | The live quick look card (income, expenses, owed, by month, top customers) | How the quick look looks |
| `api/services/ai_assistant.py` + `api/routers/ai.py` | Beacon, the AI assistant: free / premium monthly allowance, admins unlimited, consultants use the client's; optional Google Gemini planner (schema only) | AI limits and models |
| `api/services/compare.py` + `api/routers/compare.py` + `web/components/explore/CompareOverTime.tsx` | "Compare over time" (beta): two tables with only dates in common, by week or month, with a correlation and its cautions (to remove: delete these and the card in Workspace.tsx) | Unrelated sources side by side |
| `docs/images/logo-512.png` | A copy of the logo (512 x 512) to upload in the QuickBooks / Xero / Wave / Supabase developer settings; the site serves the same picture at `/logo.png` | Your logo elsewhere |
| `web/components/explore/AssistantPanel.tsx` | The AI page (Beacon): conversation, usage bar, suggestions, table facts | How Beacon looks |
| `api/services/sync.py` + `scripts/run_syncs.py` | The stored sync: QuickBooks / Xero into managed storage, only what changed, within the free limits; the scheduler | Stored sync |
| `api/services/entities.py` + `api/routers/entities.py` | Each customer / vendor / product stored once (`fact.entities`), linked from every source; "Is this the same customer?" | Customers across sources |
| `api/services/dedupe.py` | The same record from two sources (date, amount, reference) — at preview and in the sync | Duplicates across sources |
| `scripts/backfill_entities.py` | One-time: link data stored before entities | After upgrading |
| `web/components/wizard/StepConnect.tsx`, `StepQuickLook.tsx` | Get Started's connect and quick-look steps (cloud and business app paths) | The Get Started paths |
| `web/components/workspace/AddSourceDialog.tsx` | "Add another source" in the workspace | Combining sources |
| `web/components/workspace/EntityReview.tsx` | "Is this the same customer?" | Merging customers |
| `api/services/dashboards.py` + `api/routers/dashboards.py` | Business context, suggested questions by industry, the client's own dashboard tabs, the question log | Dashboards built around the business |
| `api/services/warehouse_store.py` + `api/routers/warehouse.py` | Writes an accepted load (RLS, no duplicates); preview / load / summary endpoints | Changing the load or its checks |
| `scripts/setup_database.py` | Runs db/schema.sql (dim + fact schemas), lists every table and row count | After pulling schema changes |
| `api/services/unified.py` | Unified data: field maps, metric descriptions, QuickBooks/file reshape, round trip, no-duplicate load plan (unified rows; stored packed as one row per record line in `fact.records` — `warehouse_store._pack`) | Changing how data is stored, or adding a source |
| `web/components/explore/EdaReport.tsx` + `web/components/charts/ChartView.tsx` | The EDA report view (sections, charts, tables); ChartView draws any chart a skill returns | Changing the EDA report screen or adding a chart type |
| `web/components/explore/WarehouseLoad.tsx` | Data Engineering "Load into database": preview, why, accept | Changing the load screen |
| `web/components/workspace/ConsultationPanel.tsx` | Consultant's Consultations card (confirm the free call) | Changing consultation tracking |
| `web/app/{privacy,terms,storage-terms,data-deletion}/page.tsx` + `web/components/legal/LegalPage.tsx` | Legal pages (from the template) | Changing what you collect or store; storage terms also bump `TERMS_VERSION` in `api/routers/warehouse.py` |
| `web/lib/seo.ts` + `web/app/{sitemap,robots}.ts` + `web/app/opengraph-image.tsx` | SEO: title, description, keywords, link previews, JSON-LD, sitemap, robots (from the template) | Changing how the business is described to search engines |
| `web/components/SocialLinks.tsx` | Social media icons in the footer (also Google's `sameAs` list) | Adding or changing a social account |
| `data_skills/unified-metrics/` | Test bench for the unified data table: runs `api/services/unified.py` on a file, shows every decision, round-trip check | Trying a client file before loading it into `fact.account_data` (DATA_MODEL.md) |
| `.github/workflows/vercel_checks.yml` + `web/eslint.config.mjs` | Lint + type check on every PR touching `web/`, reported to Vercel | Adding checks |
| `api/models/accounts.py` | Validates signup/quote JSON | Adding signup fields |
| `api/routers/*.py` | HTTP endpoints (thin) | Adding an endpoint |
| `api/services/file_reader.py` | Read uploads (format registry) | Supporting a new file type: add one `FileFormat` + a test sample |
| `api/services/categorizer.py` | Relate multiple files | Improving entity detection |
| `api/services/entity_matching.py` | Fuzzy-link records without a shared ID | Tuning match thresholds / name rules |
| `api/data/naics_sectors.json` | NAICS sectors & subsectors for the funnel | Regenerate with `scripts/build_naics_reference.py` |
| `db/schema.sql` | The whole database: `dim` (platform) and `fact` (client data, opt-in) | Any table change (DATABASE.md, DATA_MODEL.md) |
| `api/services/cleaning.py` | Cleaning pipeline | Adding a cleaning step |
| `api/services/spark_engine.py` | Optional PySpark engine: pandas-vs-Spark choice, JDBC reads, Spark cleaning + the unified "one row per metric" reshape ([SPARK.md](SPARK.md)) | Big data / cloud-warehouse connectors |
| `api/services/sample_size.py` | Adequacy snapshot | Adding rules of thumb |
| `api/services/pricing.py` | Services & the tools on sale (default prices; `apply_prices` swaps in the live ones) | Changing packages |
| `data_skills/build-dashboard/layout.py` + `templates/` | Dashboard building blocks: KPI row (1–4) above the fold, container rows (full, ½+½, ⅔+⅓, ⅓+⅔, thirds) from the Canva design; the downloadable HTML | Dashboard layout |
| `data_skills/build-dashboard/industry.py` + `industry_metrics.json` / `INDUSTRY_METRICS.md` | Industry KPI recipes by NAICS sector; generic fallback | Industry dashboards |
| `web/components/explore/Dashboard.tsx` + `charts/HBarChart.tsx` | The dashboard on the site: Filter panel, KPI row, containers, green / gray / red changes | Dashboard screens |
| `api/services/sessions.py` | Signed-in devices: 3 per person, 8-hour idle sign-out, sign out one / all | Sign-in security |
| `api/services/mfa.py` | Two-step sign-in (authenticator codes, backup codes), required for staff | Sign-in security |
| `api/services/audit.py` | Activity log for healthcare clients only | Healthcare |
| `api/services/hipaa.py` + `web/components/workspace/HipaaCard.tsx` / `AdminHipaa.tsx` | HIPAA requests: the NPI check, storing paused while waiting, an admin approves or denies | Healthcare |
| `api/services/pii.py` + `api/core/crypto.py` | Encrypt emails / phone numbers in stored data; the keys | Sensitive data |
| `api/core/rate_limit.py` | Per-IP request limits | Abuse protection |
| `api/core/monitoring.py` | Sentry error reports with personal data stripped | Monitoring |
| `web/components/workspace/SecurityPanel.tsx` | Settings: two-step sign-in, your devices, activity log | Security screens |
| `api/services/teams.py` | Business team roles (owner / developer / member = view only), the 5-person limit, `require_editor` | Team and permission changes |
| `api/services/offers.py` | A first package's free trial or the seasonal sale running today (yearly windows) | Trials and sales |
| `web/components/workspace/TeamPanel.tsx` | Settings → Account → Your team: people, roles, add / remove, "5 of 5 people" | Team screens |
| `web/components/workspace/AdminSales.tsx` | Admin → Seasonal sales | Sale screens |
| `api/services/login_guard.py` | Sign-in lockout: tries left, 15-minute lock after 3 wrong passwords, reset unlocks | Sign-in security |
| `api/services/price_store.py` | Live tool prices in `dim.plan_prices`: loaded at startup, changed in Admin → Prices (Stripe first) | Changing prices |
| `api/services/stripe_catalog.py` | Stripe products and prices by lookup key (`sync`, `sync_plan`) | Stripe product changes |
| `api/services/intake.py` | Questionnaire choices | Launching a connector |
| `api/services/account_store.py` | Account, consultant, subscription and usage storage | Changing tables |
| `db/schema.sql` | Database tables ([DATABASE.md](DATABASE.md)) | Any table change (also update `account_store.py` and DATABASE.md) |
| `api/services/billing.py` + `api/routers/billing.py` | Stripe: packages of tools, Checkout, webhook, portal; the AI rule (needs stored data) | Payment changes ([PAYMENTS.md](PAYMENTS.md)) |
| `api/services/fair_use.py` | Download limit per 24 h and free repeats (one-way data fingerprint) | Changing fair-use rules ([PRICING.md](PRICING.md)) |
| `api/services/connections.py` + `api/routers/connections.py` | Connectors and how often they refresh (schedule rules, "Refresh now" gap) | Adding a connector or refresh choice |
| `api/services/consultants.py` + `api/routers/consultants.py` + `api/routers/admin.py` | Consultants, their clients, the "acting as" access rule, capacity (the fill bars), consultant screening (invite list, the application, denial reasons, the 3+ month waiting period — `web/components/workspace/AdminConsultantRequests.tsx`, `SettingsPanel.tsx` `AskToConsult`), deactivating / reactivating consultants, testers, prices, the deletion counts | Consultant and admin features |
| `web/components/workspace/AdminPanel.tsx` (+ `AdminPrices.tsx`, `AdminTesters.tsx`) | The Admin panel: requests, consultants (deactivate / reactivate), prices, testers, account deletions | Admin screens |
| `web/lib/types.ts` | Response shapes | Any API response change |
| `web/components/wizard/Wizard.tsx` | Step order + state | Adding/reordering steps (give a new step an address in `web/lib/routes.ts`) |
| `web/lib/routes.ts` + `web/next.config.mjs` (`redirects`) | Every page's address; old addresses forwarding to new ones | Renaming or adding a page (backend return pages: `api/services/billing.py`, `api/services/oauth.py`; checked by `tests/test_web_routes.py`) |
| `data_skills/<id>/skill.py` | One runnable skill | Changing what a skill computes (keep its README.md in step) |
| `data_skills/_time.py` | Date ranges, DoD/WoW/MoM | Adding a range or comparison (update DASHBOARDS.md) |
| `data_skills/registry.py` | Load, validate, run skills | Changing how options are checked |
| `api/services/skills_runner.py` | Uploads → cleaned tables → skill | Changing which data a skill receives |
| `web/components/explore/*.tsx` | Dashboard + skills panel | Changing the Explore UI |
| `scripts/make_sample_data.py` | Builds `samples/` | Adding a sample story or file |
| `scripts/sync_skill_docs.py` | Regenerates each README.md "Run it" section | After changing a skill's options |
| `scripts/sync_stripe.py` | Creates/updates Stripe products and prices: the tools in `pricing.py` at the prices in `dim.plan_prices` | When first connecting Stripe, or after editing `dim.plan_prices` by hand |
| `web/components/billing/` | Build-your-own package selector and Plan & billing panel | Billing screens |
| `web/components/ui/SearchSelect.tsx` | Searchable dropdown (business size, "Working on") | Adding another searchable picker |
| `web/components/wizard/MissingValues.tsx` | Missing-value advice + fill/remove choices | Changing the missing-value UI |
| `web/components/explore/LockedService.tsx` | Locked-service badges | Changing how services unlock skills |
| `web/components/explore/TimeBar.tsx` | Floating range + comparison panels, short labels | Changing the time controls |
| `web/components/workspace/Workspace.tsx` | Signed-in workspace and left pane | Changing the workspace |
| `api/services/entitlements.py` | Services on an account's plan (one-off services, live subscriptions, consultants) | Changing who gets what |
| `api/services/consulting_billing.py` + `web/components/billing/ConsultantHelp.tsx` (+ `web/components/workspace/AdminConsultingPrices.tsx`) | Paying a consultant through the platform: setup fee and ongoing help priced by the services picked (Stripe Connect payouts, destination charges, `dim.consultant_engagements`, `dim.consulting_prices`); the rules for switching / losing a consultant and refunds | Consultant prices or payouts ([PRICING.md](PRICING.md), [PAYMENTS.md](PAYMENTS.md)) |
| `api/services/skill_access.py` + `web/components/explore/SkillPreview.tsx` | The server's paid-tool rule: full skill results only with the tool in the package; otherwise one small preview per tool per visit (`SKILL_PREVIEW_HOURS`), then 402 | Changing what a preview shows or how often ([PRICING.md](PRICING.md)) |
| `api/services/validation.py` + `web/lib/validation.ts` | Signup rules (names, business names, emails) — server + browser copies | Changing what the form accepts (change both) |
| `api/services/locations.py` + `api/data/locations.json` | City search for the location picker | Rebuild the list with `scripts/build_locations.py` |
| `web/components/auth/LocationPicker.tsx` | Type-ahead city picker (signup only) | Changing the location field |

## Patterns worth knowing

- **Routers are thin, services are thick.** HTTP details stay in `routers/`;
  data logic stays in `services/` where it is easy to unit test.
- **Dataclasses → JSON.** Services return dataclasses (`CleaningReport`, …);
  routers turn them into dicts with `asdict()`.
- **One source of truth for choices.** Questionnaire options and prices live in
  the backend and are fetched by the frontend.
- **Friendly errors.** Services raise specific errors (`FileReadError`); routers
  convert them to HTTP 400 with a message safe to show users.

## Tests as documentation

`tests/test_services.py` has one small, readable test per promise the services
make (e.g. "exact duplicates are removed", "whole numbers lose their .0").
`tests/test_data_skills.py` runs every skill on the sample data and checks
dashboard math against plain pandas. `tests/test_api.py` walks the full guest → signup → download path.
Run `pytest -q` after every change.
