# Pricing

Code: `api/services/pricing.py`. Rates: environment variables (see `.env.example`).

> **All dollar amounts in the code are placeholders.** Set your real prices
> before launch.

## Services ("What does your company need help with?")

| Service | Model | Placeholder | Needs a consultant? |
|---|---|---|---|
| Data Cleaning | Runtime (per minute) | $0.50 / min | No — fully automated, always included |
| Data Strategy & Discovery | Hourly | $80 / hour from the first hour (no free call) | Yes |
| Data Exploration & Analysis | Package | from $250 | Optional |
| Data Visualization | Package | from $500 | Optional |
| Data Engineering | Package | from $750 | Yes |
| Data Science & Machine Learning | Package | from $1,500 | Yes |
| AI | Free questions + add-on | Free monthly questions; more with the AI add-on ($29/month) | No — launched 10/2026 (set `available=False` in `pricing.py` to make a service "Early access" again) |

Each service is also a **skill category**: the skills behind it
(`data_skills/catalog.json`) appear as "What's included" chips on its card.

Clients can pick several. If any selected service needs a consultant, the quote
includes your `BOOKING_URL`.

## Beacon (AI questions): a free and a premium allowance

**Beacon** is the platform's data assistant (the AI page, `api/services/ai_assistant.py`).
It works like Bolt:

| Who | Questions a month | Planned by (with `GEMINI_API_KEY`) |
|---|---|---|
| **Free** — every client | `AI_FREE_MONTHLY` (20) | Google `gemini-2.5-flash-lite` (`AI_MODEL_FREE`) |
| **Premium** — a client with the AI package | `AI_PREMIUM_MONTHLY` (200) | Google `gemini-2.5-flash` (`AI_MODEL_PREMIUM`) |
| **Admins** | no limit (can simulate free / premium) | `gemini-2.5-flash` |

- The allowance belongs to the client (the plan holder): a team shares it, and
  a **consultant uses the client's allowance and tier** while working on them.
- It starts over on the 1st of each month (UTC), counted in `dim.ai_usage`.
- Without a Gemini key, the built-in parser answers (no cost), and the
  allowance still applies, so clients see the same product either way.
- Charging for premium = the AI package's price (below), billed once AI is
  launched (`available` / `billable` in `api/services/pricing.py`).

**What a question costs YOU.** Beacon sends about 700 tokens (instructions,
the column list, the question) and gets back a ~60-token plan; "thinking" is
off, and the platform does the math, not the model. At list prices (check
ai.google.dev/gemini-api/docs/pricing):

| Model | Per 1M tokens (in / out) | Per question | 20 a month | 200 a month |
|---|---|---|---|---|
| Gemini 2.5 Flash-Lite (free tier) | $0.10 / $0.40 | ~$0.0001 | ~$0.002 | ~$0.02 |
| Gemini 2.5 Flash (premium) | $0.30 / $2.50 | ~$0.0004 | ~$0.01 | ~$0.07 |
| (for comparison) Claude Haiku | $1 / $5 | ~$0.001 | ~$0.02 | ~$0.20 |
| (for comparison) Claude Sonnet | $3 / $15 | ~$0.003 | ~$0.06 | ~$0.60 |

**Client data and Google's free tier.** Google's free Gemini tier may use
prompts to improve its products (and people may review them); the PAID tier
does not. Beacon sends column names and category values (store names,
product types), so **turn on billing for the key's Google Cloud project**
before real clients use it — at these prices it stays pennies a month.

## Consulting: free 30 minutes, then hourly

| | Price | How it's paid |
|---|---|---|
| First consultation (any service except Data Strategy & Discovery) | **Free, 30 minutes** | Booked through `BOOKING_URL` |
| Consulting after that | **$80 / hour** (`CONSULTING_RATE_PER_HOUR_USD`) | Booked and paid on Cal.com (`PAID_BOOKING_URL`), shown once the consultant confirms the free call happened |
| Data Strategy & Discovery | **$80 / hour from the first hour** | Same paid Cal.com link (it *is* the consulting) |
| "Done for you": **setup** | **$300 once** (about 15 hours × $20) | Plan & billing → "Your consultant" (Stripe; the consultant's share is sent to them automatically) |
| "Done for you": **ongoing help** | **By the services picked**, at $20/hour: Data Cleaning / Data Engineering 10 hrs a week (once), each other tool 2.5 (Exploration & Analysis, Visualization, Science & ML), AI 0 — 17.5 at most today — e.g. Cleaning + Engineering + Visualization = 12.5 hrs = **$1,083.33 a month**; a year = 10 months | Same: a Stripe subscription; cancel any time, it runs (and is paid) to the end of the period |

Code: `consultation_terms()` and `consulting_rate()` in `pricing.py`; every
service card shows its terms ("Free 30-minute consultation, then $80/hour").

### Paying a consultant through the platform: setup and ongoing help

Why two rates: $80/hour is hands-on consulting (Cal.com sessions). Setup and
ongoing help are **tool-assisted** — the platform does most of the work and the
consultant monitors it and answers questions — so they're priced at
**$20/hour**.

- **Setup:** **$300**, once (about 15 hours).
- **Ongoing help is priced by WHAT the client needs help with**, not a level
  they pick (so nobody just picks the cheapest):

  | Picked | Hours a week |
  |---|---|
  | Data Cleaning and/or Data Engineering (files, QuickBooks / Xero: the bulk) | **10** (counted once) |
  | each of Exploration & Analysis, Visualization, Science & ML | **+2.5** |
  | Strategy & Discovery | not offered: consulting only, $80/hour on Cal.com |
  | AI | 0 (the client asks it themselves) |
  | the most | **20** |

  A month = hours × 52 ÷ 12 × $20; a year = 10 months. Cloud-database clients
  usually skip Cleaning / Engineering (nothing to clean or load). Only tools in
  the client's package can be picked. The
  consultant may help beyond these hours at their discretion.
- **Platform fee: 20%** of each payment stays with the platform; Stripe sends
  the rest to the consultant (Stripe Connect). None when the consultant is an
  admin — it's all yours.
- **All six numbers live in the database** (`dim.consulting_prices`) and are
  changed in **Admin → Prices → "Consultant help"** — no code, no Stripe sync
  (each checkout sends Stripe its own price). New purchases use them at once;
  running ongoing help keeps the price it started at.
- **The rules:** the client accepts the billing terms before paying (cancel any
  time, paid to the end of the period, no refunds for unused time except when
  the consultant becomes unavailable; setup isn't refundable once started).
  One ongoing help at a time — to change it, end it and choose again when it
  ends. Switching consultant waits until ongoing help has ended. A consultant
  deactivated or leaving ends their clients' ongoing help at once (refund the
  unused time in Stripe). Details: [PAYMENTS.md](PAYMENTS.md).
- Code: `pricing.help_hours`, `pricing.retainer_price_usd`,
  `api/services/consulting_billing.py`, `api/services/price_store.py`.

## Subscriptions: build your own package (monthly or annual)

Clients pick a **bundle** (Starter, Growth, Pro), or build their own from the
**tools**; each item has a fixed monthly and annual price and the total is the
**sum**. Bundles are cheaper than the same tools one by one. **No double
purchases** (`normalize_selection`): at most one bundle (picking another
switches to it), a bundle's tools are never charged again, and tools it
doesn't cover can be added on (Starter + Data Science & ML). When what's
picked costs more than a bundle covering it, the selector offers that bundle
(`selection_price` → `cheaper_bundle`). A client's whole package is **one
row** in `dim.subscriptions` (its items in one JSON column), however many
tools it has; `dim.plan_prices` is the catalog (one row per item on sale,
about ten in all — not per client). Code:
`SUBSCRIPTIONS`, `normalize_selection()` and `selection_price()` in
`api/services/pricing.py`; the selector is `web/components/billing/PackageBuilder.tsx`.

**Changing a price: one place.** The live prices are in the database table
`dim.plan_prices`; change them in the workspace's **Admin → Prices** card. A
save (`api/services/price_store.py`):

1. updates **Stripe first** (when it's on) — a new Stripe price under the same
   lookup key, the old one archived (`api/services/stripe_catalog.py`). People
   already subscribed keep the price they signed up at; new checkouts get the
   new one. If Stripe refuses, nothing changes anywhere.
2. saves the row in `dim.plan_prices` (with who changed it and when);
3. swaps the price into the running site (`pricing.apply_prices`), so the
   plans page, the package builder, quotes and checkout all show it at once.

Rules: at least $0.50 (Stripe's minimum), the annual price no more than 12 ×
monthly, and a typo guard ($10,000 a month at most). The API reads the table at
startup; if you edit it by hand in Supabase, run `python scripts/sync_stripe.py`
(it reads the table) and restart the API. The numbers in the table below, and
in `pricing.py`, are the **defaults** (the table's starting rows).
In Stripe each tool is a product with two prices, and one subscription holds
one item per tool ([PAYMENTS.md](PAYMENTS.md)).

| Tool (`id`) | Monthly | Annual | Rule |
|---|---|---|---|
| Data Cleaning (`data_cleaning`) | $19 | $190 | **Base** — in every package; downloads included |
| Data Exploration & Analysis (`data_exploration_analysis`) | $19 | $190 | |
| Dashboards (`data_visualization`) | $19 | $190 | |
| Data Engineering (`data_engineering`) | $39 | $390 | Managed storage, scheduled QuickBooks / Xero syncs, ER diagrams |
| Data Science & Machine Learning (`data_science_ml`) | $39 | $390 | |
| AI (`ai`) | $29 | $290 | **Add-on: needs Data Engineering** (it answers questions from stored data); grayed out until Data Engineering is picked; charged like the other tools since launch (10/2026) |
| **Starter** (`starter`, bundle) | **$19** | **$190** | Data Cleaning + Dashboards — $19/month less than one by one ($38) |
| **Growth** (`growth`, bundle) | **$49** | **$490** | + Exploration & Analysis, Data Engineering, AI — $76/month less ($125) |
| **Pro** (`all_tools`, bundle) | **$99** | **$990** | Every tool, Data Science & ML included — $65/month less ($164) |

Examples: Data Cleaning + Dashboards = $38/month one by one, so the selector
suggests Starter ($19). Cleaning + Exploration + Dashboards = $57 → Growth
($49). Starter + Data Science & ML = $58 (an add-on; Pro, $99, isn't cheaper).
A cloud-database client who only needs Dashboards takes Starter.

**Default prices.** Annual = 10 × monthly ("2 months free", 17% off); the
Prices card shows how many months free each annual price gives. **Changing tools**
later updates the same subscription (Stripe prorates the difference).
**Cancel anytime** in Stripe's billing portal ("Manage billing"); access runs to
the end of the paid period. The packages in the services table above are
one-time projects a consultant does for you.

## Runtime billing for cleaning

```
billable increments = ceil(runtime_seconds / BILLING_INCREMENT_SECONDS), minimum 1
billable minutes    = increments × BILLING_INCREMENT_SECONDS / 60
amount              = max(billable minutes × PRICE_PER_RUNTIME_MINUTE_USD, MINIMUM_CHARGE_USD)
```

| Runtime | Billable (60 s increments) | At $0.50/min |
|---|---|---|
| 0.4 s | 1 min | $0.50 |
| 59 s | 1 min | $0.50 |
| 61 s | 2 min | $1.00 |

"Runtime" is the wall-clock time of the pandas pipeline (read → categorize →
clean), measured with `time.perf_counter()` on the server. Most small-business
files finish in well under a second, so in practice the first minute is a flat
fee — consider whether `MINIMUM_CHARGE_USD` or a free tier fits your goals of
keeping costs low for small businesses.

### Fair use (so nobody games the per-minute price)

Every download is rounded **up** to a whole minute, so splitting one job into
many small downloads costs more, never less. On top of that
([`api/services/fair_use.py`](../api/services/fair_use.py)):

- **Download limit:** at most `DAILY_DOWNLOAD_LIMIT` (default 20) downloads per
  client account in a rolling `DOWNLOAD_WINDOW_HOURS` (24). The next one gets a
  friendly "try again after 10/16/2026 9:15 AM UTC" message (HTTP 429). A
  consultant downloading for a client counts against that client. `0` = no limit.
- **Repeats are free:** downloading the exact same cleaned data again within
  the window costs $0.00 (it still counts toward the limit). "The same data"
  is decided by a one-way fingerprint (SHA-256) of the table, stored in
  `usage_events.data_fingerprint` — never the data itself.

## Business tools (QuickBooks, Xero, Wave) come with a paid plan

Connecting a business tool needs **any paid plan**: the tools' free API
allowances are shared by every client, so free accounts upload an export
instead (same analysis). Details and the "spots" rule: [CONNECTORS.md](CONNECTORS.md).
Data from Xero can't be used with the AI or Data Science & ML tools (Xero's terms).

## Enterprise contracts (custom, outside self-serve)

For larger organizations, by conversation (the home page's "Ask about an
Enterprise plan → Talk to us" link goes to your booking page). No Stripe checkout:

1. Agree the price, term, services and limits on a call.
2. **Administration → Enterprise clients**: find the client, tick *Enterprise
   client*, enter the price and billing (monthly, yearly or one time), the
   one-time setup fee, the contract dates, the included services and any raised
   limits (upload size up to 200 MB, team seats up to 500, rows per database
   table up to 1,000,000). Saved on their account; takes effect at once.
3. Their owner accepts the **Enterprise Agreement** in Settings → Account
   (`/enterprise-agreement`; the version, time and typed name are recorded).
4. Invoice them from your own tool (e.g. Wave) and note the invoice numbers in
   the contract's private notes.

While the contract is active (today within its dates) the services are unlocked
and it counts as a paid plan (business-tool connections, free downloads). After
the end date everything returns to the normal limits by itself. Raising uploads
past ~100 MB also needs a larger Render instance (memory). Have a lawyer review
the agreement text before the first enterprise client.

## Connected data: how often it refreshes

Connectors (QuickBooks first) pull a client's data into managed storage, so
they need **Data Engineering**. Each pull costs API calls and compute, so the
client chooses how often (Plan & billing → Connected data); prices are
**per connection per month**, placeholders in
[`pricing.py`](../api/services/pricing.py) `REFRESH_OPTIONS`:

| Choice | Pulls | "Refresh now" no sooner than | Price |
|---|---|---|---|
| Only when I ask (`manual`) | never on a schedule | 1 hour after the last pull | Included |
| Once a week (`weekly`) | every 7 days | 24 hours | Included |
| **Once a day (`daily`, default)** | every night | 4 hours | Included |
| Every hour (`hourly`) | every 60 minutes | 15 minutes | +$15.00/month |
| Live (`live`) | about every 15 minutes | 5 minutes | +$40.00/month |

The "Refresh now" gap stops anyone getting hourly data for the price of daily
by pressing the button all day. Hourly and live are **free until connectors
launch** (`billable=False`, like AI) and are not in Stripe yet; when they go
live, set `billable=True` and add Stripe prices in `scripts/sync_stripe.py`.
Pulls only bring **new and changed** records (QuickBooks' change data
capture), so a frequent schedule doesn't re-copy everything — see
[DATA_MODEL.md](DATA_MODEL.md) for how duplicates are kept out of storage.

## Estimates and confirmations (before anything is charged)

Every step that costs money asks first, with the amount on screen:

| Where | What the confirmation shows |
|---|---|
| **Your needs → Continue to download** | Each chosen service with its price, and the estimated total (from `POST /api/quote`). Nothing is charged on this step. |
| **Add** on a locked service (Explore badges, the dashboard card, the workspace's "Add to my plan") | The service's price and the plan's **new** total with it added. |
| **Download CSV / Excel** | Rows × fields, the estimated wait, and the total to pay. |

**How long will the download take?** `download_estimate()` in `pricing.py`,
returned per table by `POST /api/files/analyze` as `download_estimate`:

```
estimated seconds = cleaning time just measured (the download re-runs it)
                  + rows × fields / cells-per-second (CSV 300,000 · Excel 20,000)
                  + 3 seconds (re-sending the files, starting up)
```

The writing speeds are laptop measurements divided by about 3 for Render's
smaller free CPU, so the real wait should be shorter than promised. The wait is
shown as "under 10 seconds", "about 40 seconds" or "about 3 minutes"; past
**five minutes** the box warns the client to keep the page open (and suggests
CSV over Excel for big tables).

| Table | CSV | Excel |
|---|---|---|
| 18,210 rows × 13 fields (`samples/sales.csv`) | under 10 seconds | about 20 seconds |
| 100,000 rows × 20 fields (≈1 s of cleaning) | about 20 seconds | about 2 minutes |
| 400,000 rows × 20 fields (≈4 s of cleaning) | about 40 seconds | about 7 minutes |

**The charge never goes above what the client confirmed.** The confirmed amount
is sent with the download (`confirmed_amount_usd`), and the server records the
smaller of that and the real runtime charge. (The re-run could, rarely, cross a
minute boundary the analysis didn't — the client doesn't pay for that.)

## What unlocks a tool (the workspace)

Code: `api/services/entitlements.py`.

- **Data Cleaning** is always open.
- **The tools in a live subscription** (`active`, `trialing`, `past_due`) —
  a "build your own" package, or All tools (every available service).
- **Every service, free, for admins** (`ADMIN_EMAILS`), AI included as a
  preview — in their own workspace and any client's (`entitlements.gets_everything`).
  Admins can't buy a package (there's nothing to buy).
- **Consultants pay like clients.** Working on a client, a consultant gets
  exactly the client's package; for their own data they need their own package.
  A "done for you" client pays for the tools here, and for the consultant's
  time on Cal.com.
- **Nothing else.** "Projects with a consultant" picked on Your needs are
  recorded as **interest** (`dim.accounts.interested_services`) and shown to the
  consultant ("Asked for help with: …"); they don't unlock tools. A locked
  tool's "Add to my package" opens Plan & billing with that tool ticked.
- In a business, the team **owner** holds the package; team members share it
  but can't change it.

**The server enforces this, not just the screen** (`api/services/skill_access.py`,
`POST /api/skills/{id}/run`). A tool's FULL result goes only to a signed-in
request whose account (or, for a consultant, whose client) has that tool in
its package. Everyone else — guests in Get Started included — gets a
**preview**:

- **one per tool per visit**: a "visit" is the same IP address within
  `SKILL_PREVIEW_HOURS` (default 8). A second try at the same tool (any of its
  skills) is refused with 402 "Add <tool> to your package…". A run that fails
  (too few rows, a bad option) doesn't use the preview up.
- **small on purpose**: the one-line summary and the first finding — for the
  dashboard (Data Visualization), the first 4 headline KPIs instead. No charts,
  no tables, no data behind them, nothing to download; the preview says how
  much the full result has ("3 charts and 2 tables").
- Data Cleaning skills are never limited. Downloading still needs an account.
- "Compare over time" (beta, on the Dashboard page) has no preview: it needs
  sign-in and Data Visualization (403 otherwise).
- Previews are counted in memory per server process (like the request
  limits): a restart, or a second Render instance, counts separately.

## What is recorded

Each download writes a row to `usage_events` (account, dataset name, row count,
runtime, billable minutes, amount — $0 with a plan that includes cleaning or for
a repeat of the same data — and the table's one-way fingerprint for fair use).
Subscriptions are paid through Stripe ([PAYMENTS.md](PAYMENTS.md)); consulting sessions on Cal.com;
pay-per-minute downloads without a plan are recorded but not yet charged.

## What a download costs YOU (hosting)

**Short answer: almost nothing per download.** Checked October 2026 — verify
on each provider's pricing page before budgeting.

| Piece | Charged for | What one download uses |
|---|---|---|
| Render (API) | Server time: flat monthly fee, not per request. Outbound bandwidth: Hobby includes **5 GB/month**, Pro 25 GB, then **$0.15/GB** ([Render docs](https://render.com/docs/outbound-bandwidth); these were cut sharply in April 2026) | The report, a 5-row preview and the cleaned file leave the server |
| Vercel (website) | Bandwidth for the website pages | Nothing — the file goes straight from the API to the browser |
| Supabase (database) | Egress; free plan includes 5 GB/month | One tiny `usage_events` row (no data) |
| Uploads (inbound) | Free on Render | — |

Responses are **gzip-compressed** (`GZipMiddleware` in `api/main.py`). Measured
on a 1.1 MB, 20,000-row CSV: the preview JSON shrinks from 2.31 MB to 0.22 MB,
and the cleaned CSV download from 1.11 MB to 0.13 MB — about 0.35 MB of
outbound bandwidth in total. (That test file was repetitive; real client data
compresses somewhat less, so plan on 0.3–0.5 MB per 1 MB uploaded.)

Rough math: **5 GB covers roughly 10,000 users a month with ~1 MB files**. Past that, each extra 1 MB user costs well under a tenth of a
cent at $0.15/GB, compared with the $0.50 minimum runtime charge.

What *can* cost more: very large files (50 MB × many users), the server size you
pick so big files don't time out, and the Render workspace plan itself.

## Free trials and seasonal sales (a FIRST package)

`api/services/offers.py`. A first package gets ONE offer: a free trial when
they qualify, otherwise the sale running today. (A discount on the first
payment would land on the $0 trial invoice and be wasted, so they don't stack.)

- **Free trials** — set "trial days" per tool in Admin → Prices
  (`dim.plan_prices.trial_days`, 0–90; 0 = none). A first package gets the
  longest trial among the tools picked; Stripe charges nothing until it ends
  (Checkout's `trial_period_days`), and the tools are on meanwhile
  (status `trialing`). **One trial per account** (`dim.accounts.trial_used_at`),
  and only for someone who never had a package, so nobody cancels and starts
  over. Testers and admins don't get one.
- **Seasonal sales** — `dim.seasonal_sales`, Admin → Seasonal sales. Each is
  a calendar window written month-day (11/24 to 12/31) that **repeats every
  year by itself**, with a percent off the first payment (the first month, or
  the first year on annual billing). Switch one on once; nothing to set up in
  Stripe — the API makes the coupon the first time a checkout needs it. While
  a sale runs, checkout uses its coupon instead of the promotion-code box;
  otherwise promotion codes you make in Stripe still work.

Starting windows (all **switched off** until you turn them on), picked for when
US small businesses tend to spend on their business:

| Sale | Window | Off the first payment | Why then |
|---|---|---|---|
| New Year planning sale | 01/01–01/31 | 20% | Budgets and plans for the year get made; a fresh start for new tools |
| After-tax-season sale | 04/16–05/15 | 20% | Taxes are filed (Apr 15), the books are fresh in mind, and the SBA's National Small Business Week is in early May |
| Year-end sale | 11/24–12/31 | 25% | Black Friday / Small Business Saturday, and owners buy tools before Dec 31 to deduct them this tax year |

Not a sale: early March to April 15 (tax season — owners are busy and
cash-careful), and Oct 1, which starts the federal government's fiscal year
but not most small businesses' (they mostly run on the calendar year).

## Teams: 5 people, three roles

A business team holds **5 people, the owner included** (`TEAM_MAX_PEOPLE`).
When it's full, the team page says "Talk to us about an Enterprise plan" and
links to a paid call — set enterprise prices by hand until you know what
larger teams pay. Roles (`api/services/teams.py`): **Owner** (the team's admin:
billing, people, roles), **Developer** (uploads, connects and loads data, edits
dashboards) and **Member** (view and download only). One person is on one team.

## Testers (try the paid tools for free)

An admin can mark any plan holder as a **tester** (Admin → Testers;
`dim.accounts.is_tester`). A tester goes through the normal "pick a package"
flow, but it switches on at once with no charge — even when Stripe is live —
and their downloads are free. Turning the switch off ends their free package.
Someone paying through Stripe must cancel first (so nobody is charged for a plan
we're also giving away). Details: [PAYMENTS.md](PAYMENTS.md#testers).

## Open decisions for you

1. **Per download or per job?** Right now each download is recorded (CSV and
   Excel of the same data = two records). Alternative: charge once per analysis
   and make re-downloads free for 24 hours.
2. **Free tier?** E.g. first file free, or files under 1,000 rows free, to lower
   the barrier for very small businesses.
3. **Payment provider.** Stripe (Checkout or usage-based Billing) is the usual
   choice and works with FastAPI. See [ROADMAP.md](ROADMAP.md).
4. **Storage pricing.** Only needed if you later keep files (e.g. for Data
   Engineering clients) — then add a per-GB-month line.
