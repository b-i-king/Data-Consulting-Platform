# Deployment

Same hosting pattern as the Video_Game_Stats_Tracker template.

## A free test run ($0)

You can put the whole platform online for testing without paying anything and
**without Supabase**:

| Piece | Free option | What to know |
|---|---|---|
| Website | Vercel **Hobby** | Free for personal, non-commercial use. Move to Pro (about $20/month) before you charge clients through the site. |
| API | Render **Free** web service (instance type **Free**) | Sleeps after 15 minutes with no visits; the next visit waits about a minute while it wakes up (the site shows "Waking up the server…" and pings the API as soon as any page opens). 512 MB memory, so uploads are capped at **15 MB** (`MAX_UPLOAD_MB`): a 14 MB CSV peaks around 300–375 MB. 750 free hours a month. |
| Database | none: leave `DATABASE_URL` empty | Accounts and plans are kept in the API's memory, so they disappear whenever the free API sleeps or redeploys. The site then signs people out with a message to create their account again. Client files are never stored either way. |

Steps: skip section 1 below, deploy the API (section 2) with `DATABASE_URL`
empty, then the website (section 3). When you want accounts to persist, add
Supabase (its free plan has a 500 MB database and pauses after a week of no
use) by running `db/schema.sql` and setting `DATABASE_URL`.

### Checklist for the test

1. **Render** → New → **Web Service** → this repo (branch `main`). Fill in the
   settings exactly as in [section 2](#2-api--render). For the test: leave
   `DATABASE_URL` out; set `ALLOWED_ORIGINS` after step 3 (a placeholder is fine
   for now); `BOOKING_URL` optional.
2. Wait for the deploy (the first build installs pandas/scikit-learn: ~5–10
   minutes), then open `https://<your-api>.onrender.com/health` →
   `{"status":"ok","database":"in-memory"}`.
3. **Vercel** → Add New → Project → this repo, **Root Directory `web`**, and set
   `NEXT_PUBLIC_API_URL` = your Render URL (no trailing slash needed). Deploy.
4. Back in Render → Environment: `ALLOWED_ORIGINS` = your Vercel URL (e.g.
   `https://data-consulting-platform.vercel.app`). Optional: `ALLOWED_ORIGIN_REGEX`
   = `https://data-consulting-platform-.*\.vercel\.app` so Vercel's preview links work too.
   Save (Render redeploys).
5. Open the Vercel URL → **Start as a guest** → upload `samples/sales.csv` (or your
   own file up to 15 MB) → **Clean my data**.

If the site says it "isn't connected to its API yet", `NEXT_PUBLIC_API_URL` is
missing in Vercel (redeploy after adding it — Vercel bakes it in at build time).
If uploads fail with a network error but `/health` works, `ALLOWED_ORIGINS`
doesn't match the site's address exactly. Render's logs warn at startup when
`ALLOWED_ORIGINS` or `JWT_SECRET_KEY` is still at its development value.

## 1. Database — Supabase

1. Create a project (choose a US region, e.g. `us-east-1`).
2. SQL Editor → run `db/schema.sql` (or run
   `python scripts/setup_database.py` from your computer with `DATABASE_URL` in `.env`).
3. Copy the **Transaction pooler** connection string (port 6543).
4. **The API's own, limited login** ([SECURITY.md](SECURITY.md#the-database)):
   1. Find your **project ref**: the 20-letter id in your Supabase address
      (`supabase.com/dashboard/project/<project ref>`), also under Project Settings →
      General → Project ID, and in the pooler string's user name (`postgres.<project ref>`).
   2. On your computer, in `.env` (never on Render):
      `ADMIN_DATABASE_URL=` the Transaction pooler string as Supabase shows it (user
      `postgres.<project ref>`, your database password), and
      `API_DATABASE_PASSWORD=` a new long password, **letters and numbers only** (so it
      needs no escaping inside a URL).
   3. Run `python scripts/setup_database.py`: it updates the tables and creates the login
      `dcp_api` with that password.
   4. On Render, `DATABASE_URL` = the same pooler string with two changes: the user
      `postgres.<project ref>` becomes `dcp_api.<project ref>`, and the password becomes
      the `API_DATABASE_PASSWORD` value. Example:
      `postgresql://dcp_api.abcdefghijklmnopqrst:NewPass123@aws-0-us-east-1.pooler.supabase.com:6543/postgres`
   Render then has ONE database setting (`DATABASE_URL`, as `dcp_api`); the two above
   stay on your computer.
5. Passwords: Authentication → Sign In / Providers → Email on. Copy the Project
   URL and anon / publishable key into Render as `SUPABASE_URL` / `SUPABASE_KEY`.
   Step by step, including Cal.com: [RUN_THROUGH.md](RUN_THROUGH.md).

## 2. API — Render

Create a **Web Service** by hand (no Blueprint needed). Render dashboard →
**New → Web Service** → connect GitHub → pick this repo. Then fill in:

| Setting | Value |
|---|---|
| Name | anything, e.g. `data-consulting-api` (becomes `https://<name>.onrender.com`) |
| Language (runtime) | **Python 3** |
| Branch | `main` |
| Region | a US region, e.g. Oregon or Virginia (Ohio) |
| Root Directory | leave **blank** (the API's `requirements.txt` is at the repo root) |
| Build Command | `pip install --upgrade pip && pip install -r requirements.txt` |
| Start Command | `gunicorn api.main:app -w ${WEB_CONCURRENCY:-1} -k uvicorn.workers.UvicornWorker --timeout 120 --max-requests 200 --max-requests-jitter 50 --bind 0.0.0.0:$PORT` |
| Instance Type | **Free** for testing; Starter (paid) before real clients, so it never sleeps |
| Health Check Path | `/health` (under **Advanced**) |
| Auto-Deploy | Yes (redeploys on every push to `main`) |

What the start command means: **gunicorn** is the process manager; it runs
`app` from `api/main.py` with one **uvicorn** worker (`-k uvicorn.workers.UvicornWorker`,
needed because FastAPI is async). `--timeout 120` gives big files two minutes;
`--max-requests 200` restarts the worker every ~200 requests so memory used by
large uploads is returned; `--bind 0.0.0.0:$PORT` listens on the port Render
assigns. Copy it as one line.

**Environment variables** (Environment tab, or "Add Environment Variable" on
the create page):

| Variable | Value |
|---|---|
| `PYTHON_VERSION` | `3.12.7` (Render's default Python may be newer than the libraries support) |
| `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET` | optional: "Connect Google Sheets" (read-only). Setup steps in [CONNECTORS.md](CONNECTORS.md#google-sheets-live-read-only) |
| `SALESFORCE_CLIENT_ID`, `SALESFORCE_CLIENT_SECRET` | optional: "Connect Salesforce" (a Connected App; [CONNECTORS.md](CONNECTORS.md#warehouses-and-salesforce-summarize-where-the-data-lives)) |
| `BIGQUERY_MAX_BYTES` | optional: the most bytes one BigQuery summary may scan in a client's project (default 10 GB) |
| `SALES_OPEN` | leave unset during the beta (purchases closed); after that, flip it in Administration → Purchases instead (that switch wins) |
| `API_DOCS` | leave unset: `/docs`, `/redoc` and `/openapi.json` are hidden on Render and shown on your computer; `true` shows them on Render too |
| `JWT_SECRET_KEY` | a long random string — generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`; keep it secret |
| `ALLOWED_ORIGINS` | your Vercel URL, e.g. `https://your-site.vercel.app` (comma-separate several; a trailing `/` is ignored) |
| `ALLOWED_ORIGIN_REGEX` | optional, e.g. `https://your-site-.*\.vercel\.app` for Vercel preview links |
| `MAX_UPLOAD_MB` | `15` on the Free / 512 MB plan; raise on bigger plans |
| `DATABASE_URL` | Supabase pooler connection string **as `dcp_api`** (section 1, step 4) — **leave it out for the free test** (accounts are kept in memory) |
| `DATA_ENCRYPTION_KEY` | a long random string (like `JWT_SECRET_KEY`, but a different one): encrypts stored emails / phone numbers and two-step keys. **Back it up** — if it's lost, that data can't be read |
| `TRUSTED_PROXY_HOPS` | `1` on Render (the default there): how the per-IP limits find the visitor's address |
| `SENTRY_DSN` | optional: error monitoring with personal data stripped (sentry.io → a FastAPI project → Client Keys) |
| `BOOKING_URL` | optional: your Calendly / Cal.com link |
| `PRICE_PER_RUNTIME_MINUTE_USD` | optional: your rate (default `0.50`) |
| `CONSULTING_RATE_PER_HOUR_USD` | optional: consulting after the free 30 minutes (default `80`) |
| `STRIPE_SECRET_KEY` | optional: `sk_test_...` / `sk_live_...` — empty = test mode, nothing charged ([PAYMENTS.md](PAYMENTS.md)) |
| `STRIPE_WEBHOOK_SECRET` | with Stripe: the webhook's signing secret `whsec_...` |
| `SITE_URL` | with Stripe: your Vercel URL (where Stripe sends people back after paying) |
| `ADMIN_EMAILS` | your email. Admins are consultants too and approve everyone else who asks to be a consultant; the **first** is the default consultant |

Click **Deploy Web Service**. The first build installs pandas and
scikit-learn (~5–10 minutes). Then open `https://<your-api>.onrender.com/health`:
`{"status":"ok","database":"in-memory"}` without Supabase, or
`{"status":"ok","database":"connected"}` with it.

If the deploy fails: `gunicorn: command not found` → the Build Command didn't
run `requirements.txt` (check Root Directory is blank); "No open ports detected"
→ the Start Command is missing `--bind 0.0.0.0:$PORT`; health check failing →
the path must be exactly `/health`.

`render.yaml` in the repo lists the same settings in file form. You don't
need it for a manual Web Service; it's there as a reference (and works if you
ever use Render's Blueprint option instead).

## 3. Website — Vercel

1. Vercel → **Add New → Project** → import this repo.
2. **Root Directory:** `web`.
3. Environment variables:

| Variable | Value |
|---|---|
| `NEXT_PUBLIC_API_URL` | `https://<your-api>.onrender.com` |
| `NEXT_PUBLIC_BOOKING_URL` | your booking link (fallback) |
| `NEXT_PUBLIC_SITE_NAME` | your brand name |
| `NEXT_PUBLIC_SITE_URL` | your public address, e.g. `https://yourdomain.com` — used by the sitemap, link previews and Google (`web/lib/seo.ts`) |
| `NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION` | optional: Google Search Console → Add property → HTML tag → the `content="..."` value |

**Getting found on Google** (once the site is live): add the property in
[Google Search Console](https://search.google.com/search-console), verify with
the HTML-tag code above (redeploy), then submit `https://<your site>/sitemap.xml`.
Search wording, keywords and the business details Google reads (JSON-LD) are in
`web/lib/seo.ts`; social profiles in `web/components/SocialLinks.tsx`.

4. Deploy. Add the resulting URL to `ALLOWED_ORIGINS` on Render.

### Signing in as the consultant

After setting `ADMIN_EMAILS` on Render (and redeploying), open the site →
**Sign in** → **Create an account** with that email and a password (tick the
Terms box). The header shows a lavender **Consultant** badge, and the workspace
has a **Working on** picker, **Settings** (your capacity) and **Admin** (approve
or deny people who asked to be consultants). On a free test server without a database, sign up again
after each restart (accounts live in memory).

## Checklist before real clients

- [ ] Security ([SECURITY.md](SECURITY.md)): `DATABASE_URL` uses `dcp_api` (not `postgres`),
      `DATA_ENCRYPTION_KEY` is set and backed up, and you've turned on two-step sign-in
      (Settings → Account) — the Admin panel needs it. Optional: `SENTRY_DSN`. In GitHub,
      turn on Settings → Code security → Dependabot alerts.

- [ ] Passwords: set `SUPABASE_URL` / `SUPABASE_KEY`, turn Supabase "Confirm email"
      on with your own SMTP, and add `https://<your site>/reset-password` to
      Supabase → Authentication → URL Configuration → **Redirect URLs** (for
      "Forgot password"). Invite emails are still to do (ROADMAP → Security).
- [ ] Leave `ALLOW_PRIVATE_DB_HOSTS` unset (it's for local testing of the Supabase integration only).
- [ ] Vercel: `NEXT_PUBLIC_SITE_URL` = your site's address (e.g. `https://data-consulting-platform.vercel.app`)
      — without it Google's structured data, link previews and the logo point at localhost.
- [ ] Your logo on the QuickBooks / Xero / Supabase sign-in screens: download `https://<your site>/logo.png`
      (512 x 512) and upload it as the app's logo in each developer site.
- [ ] Beacon (optional): `GEMINI_API_KEY` (aistudio.google.com -> Get API key) lets Google Gemini
      plan each question (Flash-Lite for free, Flash for premium; `AI_MODEL_FREE` / `AI_MODEL_PREMIUM`).
      **Turn on billing** for that Google Cloud project before real clients use it (the free tier
      may use prompts to improve Google's products). Without a key the built-in parser answers at
      no cost. Allowances: `AI_FREE_MONTHLY` (20), `AI_PREMIUM_MONTHLY` (200).
- [ ] Sign-in lockout (optional, defaults are fine): `LOGIN_MAX_ATTEMPTS` (3 wrong passwords in a
      row) and `LOGIN_LOCK_MINUTES` (15). Each wrong password says how many tries are left; a password
      reset unlocks at once (`api/services/login_guard.py`).
- [ ] Stored sync schedule: Render → New → **Cron Job**, same repo and environment variables as the
      API, command `python scripts/run_syncs.py`, schedule `0 * * * *` (hourly; `*/15 * * * *` if you
      sell "live"). Without it, syncs only run when a client clicks "Sync now".
- [ ] After deploying this version: `python scripts/setup_database.py`, then once
      `python scripts/backfill_entities.py` (links data stored before customers were stored once).
- [ ] Wave (optional): `WAVE_CLIENT_ID` / `WAVE_CLIENT_SECRET` from developer.waveapps.com (the business
      needs Wave Pro); same redirect URI as below. Limits: `WAVE_MAX_CONNECTIONS` (9),
      `WAVE_DAILY_CALL_LIMIT` (500) — 9 × 500 stays under Wave's 5,000 calls a day per app.
- [ ] "Connect" buttons (optional, per tool): set `XERO_CLIENT_ID` / `XERO_CLIENT_SECRET`,
      `QUICKBOOKS_CLIENT_ID` / `QUICKBOOKS_CLIENT_SECRET` (and `QUICKBOOKS_ENVIRONMENT=production`
      with production keys), `SUPABASE_OAUTH_CLIENT_ID` / `SUPABASE_OAUTH_CLIENT_SECRET`, and register
      `https://<API>/api/connections/oauth/callback` in each developer site (docs/CONNECTORS.md).
- [ ] Keep `JWT_SECRET_KEY` fixed: it also encrypts clients' saved database logins (changing it means they reconnect).
- [ ] Run the latest `db/schema.sql` in Supabase (`python scripts/setup_database.py`).
- [ ] Cal.com: `BOOKING_URL` (free), `PAID_BOOKING_URL` (paid), webhook + `CALCOM_WEBHOOK_SECRET` ([RUN_THROUGH.md](RUN_THROUGH.md) step 8).
- [ ] Payments: set real prices, run `scripts/sync_stripe.py`, add the Stripe webhook and keys ([PAYMENTS.md](PAYMENTS.md)).
- [ ] Set real prices in `api/services/pricing.py` (plans, packages) and env vars.
- [ ] Set `BOOKING_URL`.
- [ ] Review `/terms`, `/privacy`, `/storage-terms`, `/data-deletion` (built from the template) with a lawyer.
- [ ] Vercel → Project → Settings → Deployment Checks: require the "lint" and "typecheck" checks (`.github/workflows/vercel_checks.yml`).
- [ ] Move Vercel to a commercial plan once you charge clients.
- [ ] Turn on Supabase backups.
