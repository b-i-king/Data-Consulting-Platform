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
5. **`api/services/file_reader.py` → `read_file()`** — picks the pandas reader by
   extension; tries several text encodings; splits Excel sheets.
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

## File reference

| File | One-line purpose | Change it when… |
|---|---|---|
| `api/core/config.py` | Settings from env vars | Adding a new setting |
| `api/core/database.py` | Connection pool | Changing DB connection behavior |
| `api/core/security.py` | JWT create/verify | Switching to Supabase Auth |
| `api/models/accounts.py` | Validates signup/quote JSON | Adding signup fields |
| `api/routers/*.py` | HTTP endpoints (thin) | Adding an endpoint |
| `api/services/file_reader.py` | Read uploads | Supporting a new file type |
| `api/services/categorizer.py` | Relate multiple files | Improving entity detection |
| `api/services/cleaning.py` | Cleaning pipeline | Adding a cleaning step |
| `api/services/sample_size.py` | Adequacy snapshot | Adding rules of thumb |
| `api/services/pricing.py` | Services & prices | Changing packages/prices |
| `api/services/intake.py` | Questionnaire choices | Launching a connector |
| `api/services/account_store.py` | Account/usage storage | Changing tables |
| `db/schema.sql` | Database tables | Any table change (also update `account_store.py`) |
| `web/lib/types.ts` | Response shapes | Any API response change |
| `web/components/wizard/Wizard.tsx` | Step order + state | Adding/reordering steps |

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
`tests/test_api.py` walks the full guest → signup → download path.
Run `pytest -q` after every change.
