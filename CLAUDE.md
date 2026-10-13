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
  - `api/services/cleaning.py` → update `.claude/skills/data-cleaning/SKILL.md` and `docs/DATA_CLEANING.md`
  - `api/services/categorizer.py` → update `.claude/skills/flat-file-intake/SKILL.md` and `docs/DATA_CLEANING.md`
  - `api/services/sample_size.py` → update `.claude/skills/sample-size/SKILL.md`
  - `api/services/pricing.py` → update `docs/PRICING.md` (service ids are also skill category ids)
  - any API response → update `web/lib/types.ts`
  - any table → update `db/schema.sql` and `api/services/account_store.py`
  - `db/warehouse.sql` → update `docs/DATA_MODEL.md` (ER diagram too)
  - `api/services/file_reader.py` (formats) → update `docs/DATA_CLEANING.md` and add a sample to `tests/test_file_formats.py`
  - `api/services/entity_matching.py` → update `docs/CONNECTORS.md` and `docs/DATA_MODEL.md`
- **Never read pickle or HDF5 uploads** — they can run code. Untrusted files only
  go through the readers in `file_reader.py`.
- **pandas is the data engine.** Prefer clear pandas over clever pandas.
- **Never store client data** without an explicit design change documented in
  `docs/ARCHITECTURE.md`.
- **Routers thin, services thick.** Logic in `api/services/`, HTTP in `api/routers/`.
- Add or update a test in `tests/` for every behavior change.

## Commands

```bash
pytest -q                          # backend tests
uvicorn api.main:app --reload      # backend dev server (:8000)
cd web && npm run dev              # frontend dev server (:3000)
cd web && npm run typecheck        # TypeScript
cd web && npm run build            # production build
```

## Skills

- 30 skills in `.claude/skills/` (project-written, Daniel Rosehill's plugin, and
  the Anthropic data plugin v1.1.0), each in exactly ONE of the seven service
  categories listed in `.claude/skills/catalog.json`. See
  `.claude/skills/README.md` for the categorized list and when to use each.
- A new skill must be added to `catalog.json`; `pytest` enforces it.

## Design

- Light and dark themes; default follows the visitor's computer, with a
  Light / Dark / System switch in the header. All colors are CSS variables in
  `web/app/globals.css` — never hard-code colors in components.
- Look: VGST palette (navy-black, gold `#c8ac44`, Fira Code headings/numbers)
  with Copilot Money-style rounded cards and one color per service category
  (`web/lib/categories.ts`).

## Tech stack (from the Video_Game_Stats_Tracker template)

Next.js on Vercel · FastAPI on Render · Supabase (PostgreSQL) via asyncpg.
