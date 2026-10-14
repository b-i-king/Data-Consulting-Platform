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
skill's summary, findings, tables, charts and downloads. Only skills of the
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
| `api/core/security.py` | JWT create/verify | Switching to Supabase Auth |
| `api/models/accounts.py` | Validates signup/quote JSON | Adding signup fields |
| `api/routers/*.py` | HTTP endpoints (thin) | Adding an endpoint |
| `api/services/file_reader.py` | Read uploads (format registry) | Supporting a new file type: add one `FileFormat` + a test sample |
| `api/services/categorizer.py` | Relate multiple files | Improving entity detection |
| `api/services/entity_matching.py` | Fuzzy-link records without a shared ID | Tuning match thresholds / name rules |
| `api/data/naics_sectors.json` | NAICS sectors & subsectors for the funnel | Regenerate with `scripts/build_naics_reference.py` |
| `db/warehouse.sql` | Client-data star schema (opt-in) | Adding event types or dimensions (see DATA_MODEL.md) |
| `api/services/cleaning.py` | Cleaning pipeline | Adding a cleaning step |
| `api/services/sample_size.py` | Adequacy snapshot | Adding rules of thumb |
| `api/services/pricing.py` | Services & prices | Changing packages/prices |
| `api/services/intake.py` | Questionnaire choices | Launching a connector |
| `api/services/account_store.py` | Account/usage storage | Changing tables |
| `db/schema.sql` | Database tables | Any table change (also update `account_store.py`) |
| `web/lib/types.ts` | Response shapes | Any API response change |
| `web/components/wizard/Wizard.tsx` | Step order + state | Adding/reordering steps |
| `data_skills/<id>/skill.py` | One runnable skill | Changing what a skill computes (keep its README.md in step) |
| `data_skills/_time.py` | Date ranges, DoD/WoW/MoM | Adding a range or comparison (update DASHBOARDS.md) |
| `data_skills/registry.py` | Load, validate, run skills | Changing how options are checked |
| `api/services/skills_runner.py` | Uploads → cleaned tables → skill | Changing which data a skill receives |
| `web/components/explore/*.tsx` | Dashboard + skills panel | Changing the Explore UI |
| `scripts/make_sample_data.py` | Builds `samples/` | Adding a sample story or file |
| `scripts/sync_skill_docs.py` | Regenerates each README.md "Run it" section | After changing a skill's options |
| `web/components/wizard/MissingValues.tsx` | Missing-value advice + fill/remove choices | Changing the missing-value UI |
| `web/components/explore/LockedService.tsx` | Locked-service badges | Changing how services unlock skills |
| `web/components/explore/TimeBar.tsx` | Floating range + comparison panels, short labels | Changing the time controls |
| `web/components/workspace/Workspace.tsx` | Signed-in workspace and left pane | Changing the workspace |
| `api/services/entitlements.py` | Services on an account's plan | Adding payments (unlock on payment) |
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
