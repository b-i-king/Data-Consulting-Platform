# Architecture

## The big picture

```
 Browser (Next.js on Vercel)                    FastAPI (Python on Render)              Supabase (PostgreSQL)
 ───────────────────────────                    ──────────────────────────              ─────────────────────
 /start wizard                                  /api/intake/options   ── questions
   0 About you      ──── GET options ─────────► /api/quote            ── pricing
   1 Your data                                  /api/files/analyze    ── pandas pipeline
   2 Upload ─── files (multipart) ────────────►   read → categorize → clean → preview
   3 Clean & preview ◄── report + rows ───────    (nothing saved)
   4 Your needs     ──── POST quote ──────────►
   5 Account ─────── POST signup ─────────────► /api/accounts/signup  ───────────────────► accounts, teams
     Download ── same files + token ──────────► /api/files/download   ───────────────────► usage_events
               ◄── cleaned CSV / Excel ───────    (re-runs pipeline, streams result)        (billing metadata only)
```

The tech stack follows the [Video_Game_Stats_Tracker](https://github.com/b-i-king/Video_Game_Stats_Tracker)
template: **Next.js (Vercel) + FastAPI (Render) + Supabase (PostgreSQL)**, with
the same folder conventions (`api/core`, `api/routers`, `api/services`,
`api/models`, `web/app`, `web/components`, `web/lib`).

## Folder map

```
api/                      FastAPI backend (Python)
  main.py                 app setup: CORS, routers, startup/shutdown
  core/config.py          all settings from environment variables
  core/database.py        Supabase connection pool (optional in development)
  core/security.py        login tokens (JWT) + "who is calling?" dependency
  models/accounts.py      request/response shapes (validation)
  routers/intake.py       GET questions, GET services, POST quote
  routers/files.py        POST analyze (guest), POST download (signed in)
  routers/accounts.py     signup, signin, me, team
  routers/skills.py       GET skills, POST run a skill on uploaded files
  services/file_reader.py   file -> DataFrames: every safe pandas format (CSV/Excel recommended)
  services/entity_matching.py  fuzzy-link records with no shared ID (names/emails/phones)
  services/categorizer.py   how multiple files relate
  services/cleaning.py      the cleaning pipeline + report
  services/sample_size.py   "is there enough data?" snapshot
  services/pipeline.py      glues the above together in order
  services/skills_runner.py cleans uploads, then hands them to a data skill
  services/pricing.py       service catalog, runtime billing, quotes
  services/intake.py        questionnaire answer choices
  services/account_store.py accounts/teams/usage (Postgres or in-memory)
db/schema.sql             platform tables: accounts, teams, usage (run once in Supabase)
db/warehouse.sql          client-data star schema for Data Engineering (opt-in)
api/data/                 reference data (NAICS sectors/subsectors)
scripts/                  one-off scripts (rebuild the NAICS list)
data/reference/           source files for reference data (SBA size standards)
tests/                    pytest unit + API tests
web/                      Next.js frontend (TypeScript + Tailwind CSS)
  app/page.tsx            landing page
  app/start/page.tsx      the guest wizard page
  components/wizard/      one component per wizard step + shared UI
  components/explore/     Explore your data: Dashboard + Run a skill
  components/charts/      SVG line and bar charts (hover tooltips)
  lib/api.ts              every call to the backend
  lib/types.ts            TypeScript shapes of API responses
  lib/session.ts          keeps the login token for the browser tab
data_skills/              30 skills: SKILL.md (procedure) + skill.py (runnable code)
  _core.py, _time.py      shared result types; date ranges and DoD/WoW/MoM math
  registry.py, __main__.py  load/run skills; command line (python -m data_skills)
.claude/skills            symlink to data_skills/ so Claude Code finds the skills
samples/                  fictional bakery data for testing (scripts/make_sample_data.py)
docs/                     you are here
```

For a file-by-file walkthrough, see [CODE_MAP.md](CODE_MAP.md).

## Key design decisions

### 1. Stateless processing (no client data stored)

You wanted to avoid storing client data. The platform never writes uploaded
files to disk or the database:

- **Analyze:** the browser uploads the files; the API reads them into memory,
  cleans them, returns the report and preview, and forgets them.
- **Download:** the browser still holds the files the user picked (in page
  memory — not localStorage, not our server). It uploads them again with the
  login token; the API re-runs the identical pipeline and streams the cleaned
  file straight back.

| Pros | Cons |
|---|---|
| Nothing to secure, back up, expire, or delete | Files are uploaded twice |
| Simple privacy story for clients | Processing runs twice (billed once per download) |
| Cheap: no file storage bills | If the user refreshes the page, they must pick the files again |

**When this stops working:** very large files (hundreds of MB) or long-running
jobs (ML training). At that point, add temporary storage (Supabase Storage with a
24-hour auto-delete) and a background job queue. Pricing would then add a
storage component — see [PRICING.md](PRICING.md).

**Opt-in exception: Data Engineering.** Clients who buy Data Engineering can
choose to have their data stored in the warehouse schema (`db/warehouse.sql`,
explained in [DATA_MODEL.md](DATA_MODEL.md)): one shared star schema for all
clients, `client_id` on every row, Row Level Security for isolation. The free
cleaning flow never writes to it.

### 2. Guest first, account only at download

Every step up to the preview works without an account. Signup asks only for
what's needed: first name, last name, email (plus company name and optional
team members for businesses). This keeps the funnel short.

### 3. The backend owns the questionnaire

Question choices (data locations, services, prices) come from
`GET /api/intake/options`. Changing a choice or launching a connector is a
backend edit — the web app and a future phone app both pick it up.

### 4. Light and dark themes

Default follows the visitor's computer (`prefers-color-scheme`). The header
switch offers Light, Dark, and "Match my computer"; a choice is saved in the
browser and applied by a tiny inline script before the page paints (no flash).
Colors are CSS variables in `web/app/globals.css`: VGST's palette (navy-black,
gold, Fira Code) with Copilot Money-inspired rounded cards and category colors.

### 5. Database optional in development

Without `DATABASE_URL`, accounts live in memory so you can run everything
locally in two commands. In production, always set `DATABASE_URL`.

## Hosting and cost (as of this writing — verify current pricing)

| Piece | Host | Starting cost | Notes |
|---|---|---|---|
| Website | Vercel Hobby | Free | Hobby is for non-commercial use; move to Pro once you charge clients. Netlify / Cloudflare Pages are alternatives. |
| API | Render Starter | ~$7/month | Free tier sleeps after inactivity (slow first request). Railway or Fly.io are alternatives. |
| Database | Supabase Free | Free | Pauses after a week of inactivity on the free tier. |

The API needs a real Python server (pandas), so it can't run on Vercel's edge
functions; keeping it on Render matches your template and avoids
serverless time limits on larger files.

## Path to iOS and Android

The API is the product; the website is one client. A React Native + Expo app
(as in the template's `mobile/` folder) can call the same endpoints. File picking
on phones uses `expo-document-picker`; everything else in `web/lib/api.ts` ports
almost unchanged.
