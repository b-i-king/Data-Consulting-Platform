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
  routers/accounts.py     signup, signin, me, team, services on my plan
  routers/skills.py       GET skills, POST run a skill on uploaded files
  services/file_reader.py   file -> DataFrames: every safe pandas format (CSV/Excel recommended)
  services/entity_matching.py  fuzzy-link records with no shared ID (names/emails/phones)
  services/categorizer.py   how multiple files relate
  services/cleaning.py      the cleaning pipeline + report
  services/sample_size.py   "is there enough data?" snapshot
  services/pipeline.py      glues the above together in order
  services/skills_runner.py cleans uploads, then hands them to a data skill
  services/pricing.py       service catalog, runtime billing, quotes
  services/unified.py       unified data: any source -> one row per metric (IDs, dates, categories kept)
  services/spark_engine.py  optional PySpark engine for big / cloud-warehouse data (off by default)
  services/entitlements.py  which services an account has (drives the workspace)
  services/fair_use.py      download limit + free repeats (fingerprints, never data)
  services/connections.py   connector refresh schedules (daily default, add-ons)
  services/validation.py    signup input rules: names, business names, emails
  services/locations.py     city search for the signup location picker
  services/intake.py        questionnaire answer choices
  services/account_store.py the dim tables: accounts, teams, usage... (Postgres or in-memory)
  services/warehouse_loader.py / warehouse_store.py  preview, then store in fact.account_data
db/schema.sql             the whole database (run once in Supabase): two schemas,
                          dim = platform tables, fact = client data (opt-in)
api/data/                 reference data (NAICS sectors/subsectors)
scripts/                  one-off scripts (rebuild the NAICS list)
data/reference/           source files for reference data (SBA size standards)
tests/                    pytest unit + API tests
web/                      Next.js frontend (TypeScript + Tailwind CSS)
  app/page.tsx            landing page
  app/start/page.tsx      the guest wizard page
  app/signin/page.tsx     sign in / create an account by email
  app/app/page.tsx        the signed-in workspace (Copilot-style left pane)
  components/auth/        sign-in form (+ LocationPicker), header sign-in links
  lib/validation.ts       the same signup rules, checked while typing
  components/workspace/   the workspace: left pane of services + each service's page
  components/wizard/      one component per wizard step + shared UI (MissingValues: fill/remove choices)
  components/explore/     Explore your data: Dashboard (+ TimeBar) + Run a skill (+ locked service badges)
  components/charts/      SVG line and bar charts (hover tooltips)
  lib/api.ts              every call to the backend
  lib/types.ts            TypeScript shapes of API responses
  lib/session.ts          keeps the login token for the browser tab
data_skills/              32 skills: README.md (procedure) + skill.py (runnable code)
  _core.py, _time.py      shared result types; date ranges and DoD/WoW/MoM math
  registry.py, __main__.py  load/run skills; command line (python -m data_skills)
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
- **Missing-value choices** (fill a column with the mean/median/mode, remove a
  column) are just a small JSON field, `column_actions`, that the browser sends
  with every request that needs the data — re-analyze, dashboard, skills and
  download — so the server re-creates the same cleaned data each time without
  keeping anything.

| Pros | Cons |
|---|---|
| Nothing to secure, back up, expire, or delete | Files are uploaded twice |
| Simple privacy story for clients | Processing runs twice (billed once per download) |
| Cheap: no file storage bills | If the user refreshes the page, they must pick the files again |

**When this stops working:** very large files (hundreds of MB) or long-running
jobs (ML training). At that point, add temporary storage (Supabase Storage with a
24-hour auto-delete) and a background job queue. Pricing would then add a
storage component — see [PRICING.md](PRICING.md).

**Managed storage (opt-in exception): Data Engineering.** ✅ Built. Clients with
Data Engineering (or their consultant, working for them) can have their cleaned
data stored in `fact.account_data` (`db/schema.sql`, explained in
[DATA_MODEL.md](DATA_MODEL.md)): one shared table for all clients — IDs, dates
and categories kept as they are, every number one described metric row —
`account_id` on every row, Row Level Security for isolation. The free cleaning
flow never writes to it. The design change, and its safeguards:

1. **Preview first.** `POST /api/warehouse/preview`
   (`api/services/warehouse_loader.py`) shows how each column will be stored
   (its role, its shared name, what each number measures — all changeable),
   every step with *why*, the first rows as they will be stored, and the
   round-trip check: rebuilding the table from those rows gives back every
   cell (if not, loading is refused). **Nothing is stored by a preview.**
2. **Explicit acceptance.** Only "Accept and load" (`POST /api/warehouse/load`)
   writes, and only if the plan rebuilt from the upload has the same
   *fingerprint* as the preview the person accepted — a changed file or choice
   is refused (409), so what's stored is exactly what they saw.
3. **Consent on record.** The first load needs the "store my data" box ticked,
   saved in `storage_consents` (with the terms version and who clicked).
   **Delete my stored data** (`POST /api/warehouse/delete`, a button on the
   "Stored for ..." card) deletes every stored row for the client right away
   and withdraws the consent. Terms: `/storage-terms`; how to delete: `/data-deletion`.
4. **Audit trail.** Each load writes a `dim.load_runs` row (rows new /
   updated / unchanged, status, error, who started it, how each column was
   stored) — metadata only.
5. **No duplicates.** Every row has a stable key (the record's ID, or a
   fingerprint of the row, plus the metric), and a content fingerprint: a
   reload stores nothing new and a changed value updates just that row.
6. **Isolation.** Each load runs in one transaction that first sets
   `app.account_id` and names the account in every query; the RLS policy
   then only lets a login that doesn't bypass it see that account's rows.
7. **Same data for client and consultant.** "Use my stored data" rebuilds
   the stored file as the original table (`GET /api/warehouse/export`, with
   the client's own column names) and opens it like an upload, so the client
   (with their own login) and their consultant see the same dashboards and
   analysis — no separate code path per service.
8. **AI needs stored data.** The AI tool answers questions about stored data,
   so it can't be added to a package until the account has rows in
   `fact.account_data` (`billing.ai_ready`).

### 2. Guest first, account only at download

Every step up to the preview works without an account. Signup asks only for
what's needed: first name, last name, email, password (plus company name and
optional team members for businesses). This keeps the funnel short.

**Passwords** (`api/services/auth.py`): email + password, in-house to start.
With `SUPABASE_URL` / `SUPABASE_KEY` set, Supabase Auth stores and checks the
password (`sign_up`, `sign_in_with_password` from Supabase's Python guide) and
`accounts.auth_user_id` links the account to the Supabase user. Without them
(local development, tests) the API keeps a one-way scrypt hash in
`accounts.password_hash`. Either way the API then issues its own login token,
as before. Google / Apple sign-in can be added later through Supabase Auth with
no table changes.

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

### 5. Consultants: do it yourself, or done for you

The same platform serves clients who use the tools themselves and clients who
would rather hire a consultant. A **consultant** signs in with their own account
and sees a lavender "Consultant" badge and a **"Working on"** picker in the
workspace. Picking a client shows that client's workspace: every signed-in
request then carries `X-Acting-As: <client id>`.

**Who is a consultant (no access codes).** The `ADMIN_EMAILS` addresses are
admins and consultants from the start; the first one is the default consultant.
Anyone else ticks "I'd like to work as a consultant" (at sign-up, or in
Settings) and waits: an admin approves or denies in the workspace's **Admin**
panel (`api/routers/admin.py`). Approval creates their `dim.consultants` row at
once. Nobody can make themselves a consultant.

**Picking a consultant, and capacity.** A "done for you" client picks a
consultant by first name (or "whoever has room") and a **one-time analysis** or
**monthly help** (`accounts.consultant_plan`). Each consultant sets their
**capacity** in Settings (default 3, at most 10, `consultants.capacity`); the
picker shows a fill bar — green under half full, yellow from half, red when
full — and a full consultant can't be picked (`consultants.pick`, the capacity
rule). Clients who pick nobody go to the default consultant if they have room,
else to whoever has the most room, else wait.

**Signing in and up.** Sign-in has an **Account | Consultant** switch at the
top ("Consultant" is refused unless the account is an approved consultant).
Sign-up ends with **Signing up as: Client · Request to be a consultant · I'm an
admin** ("admin" is refused unless the email is in `ADMIN_EMAILS`).

**Each consultant's Cal.com.** A consultant's free and paid booking links and
their own webhook secret live on `dim.consultants`. Their clients see their
links (`consultations.links_for`: own consultant → default consultant →
`BOOKING_URL` / `PAID_BOOKING_URL`). Their Cal.com calls
`/api/consultations/calcom-webhook/<consultant id>`, signed with their secret,
and each booking is saved with their `consultant_id` — so paid sessions are paid
to them on Cal.com and counted per consultant here.

**Combining tables before storing.** Files with the same columns are stacked
automatically by the cleaning step. For the rest, the load preview offers
"Combine with another table": stack (append rows) or join on a shared ID
(`api/services/combine.py`; a key that repeats on both sides is refused).

**Keeping stored data current.** Settings → "Update your most recent upload"
replaces the latest stored file (same name; rows no longer in it are removed).
Connector data (QuickBooks, ...) is edited in that app, never here. "Delete my
account" removes the account and everything in it (a business owner's team
too); Stripe billing is canceled first.

**Sharing data with the consultant.** Files are never kept after a visit, so on
the guest flow's "Next steps" a consultant client is offered **Share your data
with <consultant>**: the Data Engineering load (preview, storage consent, accept)
into `fact.account_data`. Only then can the consultant open the same data from
their workspace.

**The access rule** (`api/services/consultants.py`, used through
`get_acting_context` in `api/core/security.py`):

| Caller | Can open |
|---|---|
| A client | Only their own account (and their team's shared plan) |
| A consultant | Their own account, and clients whose `accounts.consultant_id` is them (for a business, the team owner's) |
| Anyone else | 403 — never another consultant's client |

Data model (`db/schema.sql`): a `consultants` dimension table (one row per
consultant, linked to their account; `is_default` marks who new "do it for me"
clients go to), and on `accounts`: `wants_consultant` (boolean) and
`consultant_id` (NULL unless `wants_consultant` is true — enforced by a CHECK).
Clients can tick "I'd like a consultant to do this for me" at signup, or the
consultant creates their account ("Add a client"). **Admins** get every
service free (AI as a preview), in their own workspace and any client's, and
can open any client. **Everyone else pays for tools**: a consultant working on
a client gets exactly what that client's package pays for, and needs their own
package for their own data; a "done for you" client pays for the tools here and
for the consultant's time on Cal.com (Data Cleaning is always open). Projects picked on
"Your needs" are interest the consultant sees, not unlocks.
Downloads a consultant makes are billed to the client (`usage_events.consultant_id`
records who did it). **Client data is still never stored** — the consultant
works on files the client shares, in the same in-memory way (unless the client
opts in to managed storage, above).

Consultants also see services that aren't launched yet (AI) as a **preview**,
so all seven services can be tested; clients still see them as "Early access".

**Consultations** (`api/services/consultations.py`): the first 30-minute call is
free, then $80/hour. Booking happens on Cal.com, so the app learns about
bookings from Cal.com's signed webhook (`consultations` table: booked,
rescheduled, canceled, ended) and the consultant confirms the call happened.
Only that confirmation uses up the free call; from then on the client's
booking links point to `PAID_BOOKING_URL`.

### 6. Payments through Stripe, decided by the webhook

Plans (monthly or annual) are paid on Stripe's own
Checkout page; Stripe's signed webhook is the only thing that turns a plan on
or off (`subscriptions` table). Without Stripe keys the site runs in test
mode. Full design: [PAYMENTS.md](PAYMENTS.md); every table: [DATABASE.md](DATABASE.md).

### 7. Database optional in development

Without `DATABASE_URL`, accounts live in memory so you can run everything
locally in two commands. In production, always set `DATABASE_URL`.

## Engines: pandas now, PySpark later

pandas is the data engine for everything the platform does today: a whole
table in one machine's memory, simple and fast for uploads. PySpark is set up
in advance (`api/services/spark_engine.py`, off by default, not installed on
Render) for clients whose data is too big for pandas or lives in a cloud
warehouse. `choose_engine(rows, columns, source)` keeps pandas unless Spark is
enabled **and** the table is above `SPARK_MIN_CELLS` (50,000,000 cells) or the
source is BigQuery / Snowflake / Databricks / Redshift / a large Postgres. The
planned path is to run Spark jobs on a managed service near the client's
warehouse, not inside the web service. Details: [SPARK.md](SPARK.md).

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
