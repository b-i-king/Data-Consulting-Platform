# CLAUDE.md — project instructions for Claude Code

## About the project owner

A data consultant, data scientist, and entrepreneur (United States) building a
platform to help underrepresented small, medium, and large businesses with
their data. The owner directs the work and relies on Claude for most of the
coding, so **understanding the code matters as much as the code itself**.

## Conventions (always follow)

- **US English** everywhere: code comments, docs, UI text (color, analyze,
  organization, behavior). US formats: `1,234.56`, `$1,234.56`, `MM/DD/YYYY`
  in UI text, ISO `YYYY-MM-DD` in data files.
- **Explain the code.** Every source file starts with a header comment saying
  what it does and why. Non-obvious lines get a short comment. Write comments
  for a reader who knows pandas and data work well but is not a web developer.
- **Keep docs and skills in sync with code.** A change to
  - `api/services/cleaning.py` or `api/services/transforms.py` → update `data_skills/data-cleaning/README.md` and `docs/DATA_CLEANING.md`
  - `api/services/categorizer.py` → update `data_skills/flat-file-intake/README.md` and `docs/DATA_CLEANING.md`
  - `api/services/sample_size.py` → update `data_skills/sample-size/README.md`
  - `api/services/pricing.py` → update `docs/PRICING.md` (service ids are also skill category ids);
    plan or price changes also need `python scripts/sync_stripe.py` (Stripe products)
  - `api/services/billing.py` or `api/routers/billing.py` → update `docs/PAYMENTS.md`
  - any API response → update `web/lib/types.ts`
  - any table → update `db/schema.sql`, `api/services/account_store.py` and `docs/DATABASE.md` (ER diagram too)
  - `db/warehouse.sql` → update `docs/DATA_MODEL.md` (ER diagram too)
  - `api/services/file_reader.py` (formats) → update `docs/DATA_CLEANING.md` and add a sample to `tests/test_file_formats.py`
  - `api/services/entity_matching.py` → update `docs/CONNECTORS.md` and `docs/DATA_MODEL.md`
  - `data_skills/<id>/skill.py` → keep its `README.md` procedure in step, and run
    `python scripts/sync_skill_docs.py` to regenerate the "Run it" options table (`pytest` checks it)
  - `data_skills/_time.py` or `data_skills/build-dashboard/` → update `docs/DASHBOARDS.md`
- **Never read pickle or HDF5 uploads** — they can run code. Untrusted files only
  go through the readers in `file_reader.py`.
- **pandas is the data engine.** Prefer clear pandas over clever pandas.
- **Never store client data** without an explicit design change documented in
  `docs/ARCHITECTURE.md`.
- **Routers thin, services thick.** Logic in `api/services/`, HTTP in `api/routers/`.
- **Client access goes through `get_acting_context`** (`api/core/security.py`)
  for anything that reads or changes a client's plan or billing, so a consultant
  can only reach their own clients (`api/services/consultants.py`).
- Add or update a test in `tests/` for every behavior change.

## Git

- Commit finished, tested work to the current feature branch, then push it
  (`git push -u origin <branch>`). Never push to `main`/`master` directly and
  never force-push.

## Commands

```bash
pytest -q                          # backend tests
uvicorn api.main:app --reload      # backend dev server (:8000)
cd web && npm run dev              # frontend dev server (:3000)
cd web && npm run lint             # ESLint (also runs on every PR: .github/workflows/vercel_checks.yml)
cd web && npm run typecheck        # TypeScript
cd web && npm run build            # production build
python -m data_skills list         # skills by category
python -m data_skills run build-dashboard samples/sales.csv   # run a skill -> outputs/
python scripts/make_sample_data.py # regenerate samples/ (fictional bakery)
python scripts/sync_skill_docs.py  # regenerate each README.md "Run it" section from skill.py
```

## Skills

- 30 skills in `data_skills/` (project-written, Daniel Rosehill's plugin, and
  the Anthropic data plugin v1.1.0), each in exactly ONE of the seven service
  categories listed in `data_skills/catalog.json`. See
  `data_skills/README.md` for the categorized list and when to use each.
- Each skill folder has `README.md` (the procedure) **and** `skill.py` (the
  same procedure as code, run by the CLI, `POST /api/skills/{id}/run` and the
  web Explore panel). In the app, a skill is offered only when the client has
  chosen its service (Data Cleaning is always included).
- A new skill needs `README.md`, `skill.py`, a `catalog.json` entry, and a
  case in `tests/test_data_skills.py`; `pytest` enforces it.

## Design

- Light and dark themes; default follows the visitor's computer, with a
  Light / Dark / System switch in the header. All colors are CSS variables in
  `web/app/globals.css` — never hard-code colors in components.
- Look: VGST palette (navy-black, gold `#c8ac44`, Fira Code headings/numbers)
  with Copilot Money-style rounded cards and one color per service category
  (`web/lib/categories.ts`).

## Tech stack (from the Video_Game_Stats_Tracker template)

Next.js on Vercel · FastAPI on Render · Supabase (PostgreSQL) via asyncpg.
