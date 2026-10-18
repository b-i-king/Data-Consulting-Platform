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

More in **🛡️ Admin**:

- **Consultants** — **Deactivate** someone (optional note): their clients move
  to a consultant with room, they can't be picked, and they sign in as a client.
  **Reactivate** switches them back on (and gives them anyone waiting).
- **Prices** — change a tool's monthly / annual price and **Save**. The plans
  page and the package builder show it at once (check `dim.plan_prices` in
  Supabase: the row has your name in `updated_by`). With Stripe connected, a new
  Stripe price appears under the same lookup key.
- **Testers** — search a friend's account and **Make tester**: they can pick any
  package for free (it says "Tester account"), even after Stripe is live.
- **Account deletions** — anonymous counts of deleted accounts and the reasons
  people picked.
- **Prices → trial days** — e.g. 14 on All tools: a brand-new client's first
  package then says "Starts with a 14-day free trial" and starts as `trialing`.
- **Seasonal sales** — set a window that includes today and **Switch on**: Plan &
  billing shows the sale, and the checkout confirmation takes the percent off the
  first payment. Switch it back off (or set the real dates) when you're done.

**Teams:** sign up a business with a colleague, then Settings → Account → **Your
team**: change their role (Developer / Member (view only)), remove them, or add
people up to 5. Sign in as a Member: the workspace says "View only", and storing
or connecting data is refused.

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
- **Sign in as Consultant** — the **Account | Consultant** switch at the top of
  sign-in. "Consultant" works only for approved consultants; a client (or
  someone still waiting for approval) gets a clear message.
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

**Each consultant's own Cal.com (recommended once you have consultants).**
A new consultant sees **Finish setting up** in their workspace → **⚙️ Settings**:

1. **Your booking links** — they paste their own free 30-minute link and paid
   session link (their Cal.com, with Cal.com's Stripe app on the paid event, so
   they're paid directly). Their clients now see these links.
2. **Your Cal.com webhook** — they press **Make my webhook secret**, then in
   Cal.com → Settings → Developer → Webhooks → **New**, paste the
   **Subscriber URL** shown (`…/api/consultations/calcom-webhook/<their id>`)
   and the **secret**, tick the triggers listed, and save (Cal.com's **Ping
   test** should say OK). Bookings through it are saved with their
   `consultant_id`.

**One webhook covers both events.** Create it once at the *account* level
(Settings → Developer → Webhooks), not inside each event type — it fires for
the free and the paid event alike. (If you already added it inside an event
type, use the same URL and secret on the other one.) The app tells free from
paid by the link it showed the client, or — for bookings made straight on
Cal.com — by the booked event's name (`data-consultation` vs
`paid-data-consultation`), matched against your saved links.

**No environment variables needed.** Once you (the first `ADMIN_EMAILS`
address) save your links in Settings, guests and every client without their
own consultant's links see yours. `BOOKING_URL`, `PAID_BOOKING_URL` and
`CALCOM_WEBHOOK_SECRET` are only a fallback and can stay empty.

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

## Step 8b — Settings, passwords, integrations, dashboard tabs

- **Settings** is at the bottom left of the workspace. It opens Account, Plan &
  billing, Integrations, Consultant (consultants) and Administration (admins).
- **Forgot password:** sign out → Sign in → "Forgot password?" → your email.
  With Supabase on, the email's link opens `/reset-password` (add that address
  to Supabase → Authentication → URL Configuration → Redirect URLs). Without
  Supabase, the link is printed in the API's terminal. Signed in: Settings →
  Account → **Reset password**.
- **Supabase database integration:** Settings → Integrations → Supabase database
  → paste a connection string (Supabase → Project → Connect; a read-only user is
  best) → its tables appear → **Open in workspace**. Data Cleaning shows a
  report only, and Data Engineering's "Load into database" is off for it.
- **Dashboard tabs:** Settings → Account → "About your business" (what you do,
  goals, key questions). Then Dashboard → **+ Add tab** → pick suggested
  questions (yours, asked often, for your industry) → Save.

## Step 8c — Get Started paths, stored sync, customers across sources

1. **Files path:** `/start` → Files on my computer → Upload → Clean & preview → Your needs → Next steps (unchanged).
2. **Cloud path:** `/start` → Cloud storage → **Supabase / PostgreSQL** ("connect now") → create the
   account → paste a connection string (or Sign in with Supabase) → "Preview this table" → the
   read-only report → Your needs ("Just download" is grayed out, with a note) → Next steps →
   "Open your workspace" opens on that table.
3. **Business app path** (needs Xero or QuickBooks keys): `/start` → Business apps → **Xero** →
   account → Connect (Xero's sign-in, then back to `/start`) → Quick look (last 12 months) →
   optionally preview a record type → Your needs → Next steps → with Data Engineering,
   "Turn on the stored sync".
4. **Add another source:** in the workspace, with a file open → "Add another source" → a database
   table or Xero records → both open as tabs, with "can be matched by name".
5. **Customers once:** load a file with customer names; sync QuickBooks with the same customers →
   Settings → Integrations shows no duplicates of them; a near-match ("Corner Market ATL") appears
   under "Is this the same customer?". In SQL: `SELECT * FROM fact.entities` — one row each.
6. **Moving around:** every page but the first has "← Previous"; from the third page on there is
   "« First step" (answers kept); every page but the first and the last has "Skip to download".
   With a cleaned file it goes straight to Next steps (just download); on the cloud or app path,
   or before uploading, it goes to Upload first and then straight to the download.
7. **Finding a client (consultants):** with two or more clients, the purple "Working on" box has
   "Help with" (the projects clients asked for, with counts), "One-time / Monthly" and "A to Z /
   Recently active" above the search box; clicking the box lists everyone, typing narrows it
   (names, email, location, or what they need help with).
8. **Beacon (AI allowance):** open a table, then the AI page: a free client sees "0 / 20" and a gold bar
   (amber from 70%, red from 90%), suggestions and the table's columns; the 21st question is
   refused until the 1st. An admin sees "no limit" and can switch to "Simulate: Free". A
   consultant on a client sees "Counts toward <client>'s allowance".
9. **Duplicates:** upload an export of invoices QuickBooks already synced → the preview says
   "N of these records are already stored from QuickBooks Online"; accept with "load only the new
   records" → only the new ones are stored.

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
