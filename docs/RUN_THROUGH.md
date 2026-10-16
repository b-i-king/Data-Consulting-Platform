# Your first full run-through (as the consultant)

The goal: sign in as yourself (the consultant), see **all seven services**, run
each one on the sample bakery data, load data into the database (after
previewing it), and track a consultation from free to paid — **without Stripe**.
Plan on about an hour the first time.

The order matters: **the database, then YOUR consultant account, then clients.**
The `consultants` row is created the first time you sign in with your
consultant email, and "do it for me" clients are assigned to you only once it
exists.

---

## Step 1 — Supabase (database + passwords), about 10 minutes

1. Create a project at <https://supabase.com> (or open yours). Save the database
   password it asks for.
2. **Connection string** — click **Connect** (top of the dashboard) →
   **Transaction pooler** (port 6543) → copy it. Put your database password in
   place of `[YOUR-PASSWORD]`. This is `DATABASE_URL`.
3. **Project URL and key** — **Project Settings → API** (or **API Keys**): copy
   the **Project URL** (`SUPABASE_URL`) and the **anon / publishable** key
   (`SUPABASE_KEY`). Never use the `service_role` / secret key here.
4. **Email sign-in** — **Authentication → Sign In / Providers → Email**: make
   sure Email is enabled. For today's run, switch **"Confirm email" OFF** so you
   can sign in right after signing up. (Switch it back ON before real clients;
   the app already handles "check your email". Supabase's built-in email
   sender is rate-limited, so add your own SMTP under Authentication → Emails
   before launch.)

## Step 2 — Your `.env` file

In the project folder: copy `.env.example` to `.env` and fill in:

| Setting | What to put |
|---|---|
| `DATABASE_URL` | the connection string from Step 1.2 |
| `JWT_SECRET_KEY` | run `python -c "import secrets; print(secrets.token_urlsafe(48))"` and paste the result |
| `SUPABASE_URL`, `SUPABASE_KEY` | from Step 1.3 |
| `ADMIN_EMAILS` | **your** email: you're the admin and the default consultant |
| `BOOKING_URL` | your Cal.com **free** 30-minute consultation link |
| `PAID_BOOKING_URL` | your Cal.com **paid** session link (Step 8) — can wait |
| `CALCOM_WEBHOOK_SECRET` | leave empty for now (Step 8) |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | **leave empty** = test mode: packages switch on without charging |

And for the website: copy `web/.env.example` to `web/.env.local` (the defaults
point at `http://localhost:8000`; set `NEXT_PUBLIC_BOOKING_URL` to your free link).

## Step 3 — Create the tables

```bash
.venv\Scripts\activate                 # Windows (source .venv/bin/activate on Mac)
pip install -r requirements.txt
python scripts/setup_database.py
```

It runs `db/schema.sql`, which creates two schemas — `dim` (the platform: 9
tables) and `fact` (client data: `account_data` and `field_map`) — then lists
every table with its row count. You should see `ok` next to all 11 tables;
`field_map` already has 94 rows (the shared field names). Safe to re-run anytime.

## Step 4 — Start both servers

```bash
uvicorn api.main:app --reload          # terminal 1 -> http://localhost:8000
cd web && npm install && npm run dev   # terminal 2 -> http://localhost:3000
```

Open <http://localhost:8000/health>. You want:
`{"status": "ok", "database": "connected", "auth": "supabase"}`.
(`in-memory` means DATABASE_URL isn't being read; `local` means the Supabase
settings are empty.)

## Step 5 — Create YOUR consultant account (before anything else)

1. Go to <http://localhost:3000/signin?mode=signup>.
2. Fill in your name, **the email from `ADMIN_EMAILS`**, and a password
   (8+ characters, then **Confirm password**).
3. Tick **I agree to the Terms of Service and Privacy Policy**.
4. **Create account** → you land in the workspace with a lavender **Consultant**
   badge, plus **⚙️ Settings** (your capacity, default 3 clients) and
   **🛡️ Admin** (people waiting to become consultants: Approve / Deny).

Check it worked: `python scripts/setup_database.py --check` ends with
`Consultants: you@... (default)`. In Supabase → Table Editor → `accounts`, your
row has `auth_user_id` filled and `password_hash` empty (Supabase holds the
password, not us).

Next time: **Sign in** with email + password. To test a second consultant, sign
up in a private window with another email and tick **I'd like to work as a
consultant**; approve them in **Admin**, and their workspace switches to
consultant mode on its next load.

## Step 6 — Make test clients

- **A "done for you" client** — in the workspace's **Working on** picker, choose
  **Add a client** (e.g. business "Sunrise Bakery", a made-up email). Pick it in
  **Working on**: you're now in their workspace.
- **A do-it-yourself client** (to see what a paying client sees) — open a
  private browser window, sign up with another email of yours (Gmail's
  `you+client1@gmail.com` works), *without* ticking "I'd like ... to do this for
  me". You'll see only Data Cleaning open, the rest grayed out with prices, and
  "Book a free 30-minute call". Packages switch on free in test mode
  (Plan & billing → Subscribe (test mode)).

A client you added has no password yet: the password typed at their **first**
sign-in becomes theirs.

- **A guest who asks for a consultant** (the full flow, in a private window) —
  **Start as a guest** → upload `samples/sales.csv` → on **Your needs** pick
  **Help from a consultant**, choose **One-time analysis** or **Monthly help**,
  and pick yourself by name (the fill bar shows how full you are) → create the
  account → on **Next steps** use **Share your data with <you>**: check the
  preview, tick the storage consent, **Accept and load**. Back in *your*
  workspace, pick that client in **Working on**: Data Engineering → **Use my
  stored data** opens the same table. (Skip the share step and nothing is
  stored — files only live in the guest's browser tab.)
- **Capacity** — in **⚙️ Settings**, set your capacity to 1. Once you have one
  client, the picker shows you red/full and new clients can't pick you.

## Step 7 — Walk through all seven services

Work in Sunrise Bakery's workspace (or your own). Each page starts with
**Choose files**; use the files in `samples/`. Use the **Skill** menu at the top
of each page to try the others.

| # | Service (left pane) | File | Try | Check |
|---|---|---|---|---|
| 1 | 🧹 Data Cleaning | `sales.csv` | the cleaning report, missing values, rename a column, **Download CSV** | the download lands; Supabase `usage_events` gets a row |
| 2 | Data Strategy & Discovery | `sales.csv` + `customers.csv` | Data-source inventory & roadmap; "Is there enough data?" | a written brief |
| 3 | Data Exploration & Analysis | `sales.csv` | Dataset profiling; Key trends; Hypothesis testing | tables and findings |
| 4 | 📊 Dashboard (Visualization) | `sales.csv` | the time dashboard; Charts from your data | KPI cards, ranges |
| 5 | Data Engineering | `sales.csv`, then `expenses.csv`, `feedback.csv` | **Load into database (preview first)** — see below; ER diagrams; Data dictionaries | rows in `warehouse.*` |
| 6 | Data Science & ML | `sales.csv` | Forecasts & predictive models | model results in plain English |
| 7 | AI — **Preview** | `sales.csv` | Ask: `total amount by store`, `top 5 product by amount in 2025`, `how many orders by payment_method` | answers with the calculation shown |

AI shows a **Preview** badge: only consultants see it open; clients see it as
"Early access". It answers with the built-in plain-English parser today; a
language model comes later.

### The Data Engineering load (preview → accept)

1. Data Engineering opens on **Load into database (preview first)**.
2. Read the card top to bottom:
   - **What are these rows?** — guessed (sales → "Sales / income"; expenses →
     "Expenses"; feedback → "CSAT scores"). Change it if wrong.
   - **How each column is stored** — each column's role (metric, ID, kept as
     is), its shared name (`amount` → `line_amount`), and for numbers what they
     measure (type, subtype, unit) with how sure the guess is. Change any; the
     preview updates.
   - **What happens to your data, and why** — e.g. "Turn 3 number columns into
     metric rows... *Why:* a new kind of number is then just a new type".
   - **How it will look in the database** — the first rows of
     `fact.account_data`, and "Nothing is lost: rebuilding your table from these
     rows gives back all N cells exactly".
3. Tick **Store this data for Sunrise Bakery...** (consent, saved once) →
   **Accept and load**. Or **Don't load**: nothing is stored.
4. The **Stored for ...** card lists the file. Press Accept again: "0 new
   rows — already stored unchanged" (reloads never duplicate).
5. In Supabase: `dim.load_runs` and `dim.storage_consents` have rows, and the
   data is in `fact.account_data` (the SQL Editor's login sees all of it):

   ```sql
   select field_name, metric_type, metric_unit, count(*), sum(metric_value)
   from fact.account_data where account_id = '<account id from dim.accounts>'
   group by 1, 2, 3;
   ```
6. **The client sees the same thing.** Sign in as that client (private window;
   for a client you added, their first sign-in sets their password). On any
   service's **Choose your data** card, press the file's button under **Use
   the data stored for ...**: the stored table opens exactly as the file did, so the dashboard, analysis
   and AI show what you see. Pick it in your own window too — same data,
   side by side.
7. **Download or delete.** The **Stored for ...** card has **Download as CSV**
   and **Delete stored data** (the client's owner or you; it deletes right away
   and withdraws the consent). The public pages `/privacy`, `/terms`,
   `/storage-terms` and `/data-deletion` are linked in the footer.

## Step 8 — Consultations: free first, then paid (Cal.com)

**How it works.** Booking happens on Cal.com, so the app can't see it on its
own. Two ways in, one rule:

- **Automatic:** Cal.com's webhook tells the app when a consultation is booked,
  rescheduled, canceled, or has ended.
- **Manual:** you confirm it in the client's workspace.
- **The rule:** the free consultation counts as *used* only when **you** press
  **Confirm it happened** (Cal.com saying "ended" isn't enough — only you know
  it really happened). From then on every booking link that client sees is the
  **paid** one. No-show and canceled keep the free call available.

**Set up Cal.com (once):**

1. Keep your free event (30 minutes) → `BOOKING_URL`.
2. Create a second event type, e.g. "Consulting session" (60 minutes) →
   `PAID_BOOKING_URL`. To collect the $80 at booking with no Stripe work in
   this app, install Cal.com's **Stripe** app and set the event's price there.
   Paid sessions live entirely on Cal.com — nothing about them is bought or
   stored in this app.
3. Webhook: Cal.com → **Settings → Developer → Webhooks → New**:
   - Subscriber URL: `https://<your Render API>/api/consultations/calcom-webhook`
   - Triggers: Booking Created, Booking Rescheduled, Booking Cancelled, Booking
     Rejected, Meeting Ended (and Booking No-show Updated if offered)
   - Secret: a long random string → put the same value in `CALCOM_WEBHOOK_SECRET`.
   - Cal.com can't reach `localhost`, so the webhook works once the API is on
     Render. Locally, use **Add a call by hand**.

**Test it:**

1. As the do-it-yourself client: click **Book a free 30-minute call** — Cal.com
   opens with the name and email already filled (the link also carries the
   account id, so the booking is matched to the right account).
2. As you, working on that client: the **Consultations** card shows the booking
   (with the webhook) → after the meeting, **Ended — please confirm** →
   **Confirm it happened**. (Without the webhook: **Add a call by hand → Free
   consultation done**.)
3. As the client again (refresh): the button now reads **Book a consulting
   session ($80/hour)** and opens `PAID_BOOKING_URL`.

Made a mistake? The card's **History** has **undo** on a confirmed free call.

## Step 9 — Is the database right?

```bash
python scripts/setup_database.py --check
```

…lists every table and its row count. After this run-through expect rows in
`accounts`, `teams` (business clients), `consultants` (you), `consultations`,
`storage_consents`, `load_runs`, `usage_events`, and the warehouse tables. In
Supabase → **Authentication → Users** you'll see the same people (Supabase's
side of the passwords). If someone forgets a password, send a reset from there
until "Forgot password" is built.

---

## Gaps to close (so clients really have a consultant at hand)

**Before real clients use it**
- **Forgot password** page, and Supabase **Confirm email** back on (with your own SMTP).
  *Waiting on your Supabase setup* — Supabase sends the reset email.
- **Invite emails** for colleagues and consultant-added clients (today the first
  sign-in with their email sets the password — sign them in promptly).
  *Also waiting on Supabase* (it needs the server-side service key).
- ✅ `/privacy`, `/terms`, `/storage-terms`, `/data-deletion` pages, and "Delete
  stored data". Have a lawyer review the wording before launch.
- On Render: set `JWT_SECRET_KEY`, all the settings above, and move off the free
  plan (it sleeps after 15 minutes). Supabase backups: daily backups come with
  the Pro plan.
- Stripe, when you're ready to charge (`python scripts/sync_stripe.py`, PAYMENTS.md).

**Consultant workflow**
- ✅ **Sharing your work:** load the client's data into managed storage; the
  client signs in with their own account and presses **Use my stored data** to
  see the same dashboards and analysis you do. Next: saved dashboards that
  refresh automatically with their subscription.
- Notes per client, reassigning clients, a list of Cal.com bookings from people
  without an account.
- Notifications (email when a booking arrives, when a load finishes).

**Product**
- QuickBooks connector (next — CONNECTORS.md), then dashboards and skills that
  read from the warehouse, and scheduled reloads.
- AI with a language model and usage caps (ROADMAP.md §7).
- Admin page (signups, plans, usage, hours) and intake analytics.
- Error monitoring (e.g. Sentry) and uptime alerts.
