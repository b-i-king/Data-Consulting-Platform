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
2. SQL Editor → run `db/schema.sql`.
3. Copy the **Transaction pooler** connection string (port 6543).

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
| `JWT_SECRET_KEY` | a long random string — generate one with `python -c "import secrets; print(secrets.token_urlsafe(48))"`; keep it secret |
| `ALLOWED_ORIGINS` | your Vercel URL, e.g. `https://your-site.vercel.app` (comma-separate several; a trailing `/` is ignored) |
| `ALLOWED_ORIGIN_REGEX` | optional, e.g. `https://your-site-.*\.vercel\.app` for Vercel preview links |
| `MAX_UPLOAD_MB` | `15` on the Free / 512 MB plan; raise on bigger plans |
| `DATABASE_URL` | Supabase pooler connection string — **leave it out for the free test** (accounts are kept in memory) |
| `BOOKING_URL` | optional: your Calendly / Cal.com link |
| `PRICE_PER_RUNTIME_MINUTE_USD` | optional: your rate (default `0.50`) |

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

4. Deploy. Add the resulting URL to `ALLOWED_ORIGINS` on Render.

## Checklist before real clients

- [ ] Replace email-only sign-in with verified sign-in (see ROADMAP → Security).
- [ ] Set real prices in `api/services/pricing.py` and env vars.
- [ ] Set `BOOKING_URL`.
- [ ] Add Terms of Service and Privacy Policy pages (the template repo has examples).
- [ ] Move Vercel to a commercial plan once you charge clients.
- [ ] Turn on Supabase backups.
