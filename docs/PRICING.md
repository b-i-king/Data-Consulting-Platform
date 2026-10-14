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
| AI | Early access | No charge yet | No — shown with an "Early access" badge |

Each service is also a **skill category**: the skills behind it
(`data_skills/catalog.json`) appear as "What's included" chips on its card.

Clients can pick several. If any selected service needs a consultant, the quote
includes your `BOOKING_URL`.

## Consulting: free 30 minutes, then hourly

| | Price | How it's paid |
|---|---|---|
| First consultation (any service except Data Strategy & Discovery) | **Free, 30 minutes** | Booked through `BOOKING_URL` |
| Consulting after that | **$80 / hour** (`CONSULTING_RATE_PER_HOUR_USD`) | Prepaid hours in Plan & billing (Stripe Checkout, price `dcp_consulting_hour`) |
| Data Strategy & Discovery | **$80 / hour from the first hour** | Same prepaid hours (it *is* the consulting) |
| "Done for you" (a consultant does the work) | Free 30-minute call, then hourly or a package | Hours, or a package price agreed on the call |

Code: `consultation_terms()` and `consulting_rate()` in `pricing.py`; every
service card shows its terms ("Free 30-minute consultation, then $80/hour").

## Subscriptions: build your own package (monthly or annual)

Clients pick the **tools** they need; each has a fixed monthly and annual
price and the total is the **sum**. **All tools** is a cheaper bundle. Code:
`SUBSCRIPTIONS`, `normalize_selection()` and `selection_price()` in
`api/services/pricing.py`; the selector is `web/components/billing/PackageBuilder.tsx`.
In Stripe each tool is a product with two prices, and one subscription holds
one item per tool ([PAYMENTS.md](PAYMENTS.md)).

| Tool (`id`) | Monthly | Annual | Rule |
|---|---|---|---|
| Data Cleaning (`data_cleaning`) | $19 | $190 | **Base** — in every package; downloads included |
| Data Exploration & Analysis (`data_exploration_analysis`) | $19 | $190 | |
| Dashboards (`data_visualization`) | $19 | $190 | |
| Data Engineering (`data_engineering`) | $39 | $390 | Managed storage when it launches |
| Data Science & Machine Learning (`data_science_ml`) | $39 | $390 | |
| AI (`ai`) | $29 | $290 | **Add-on: needs Data Engineering** (it answers questions from stored data); grayed out until Data Engineering is picked; **free until it launches** |
| **All tools** (`all_tools`, bundle) | **$99** | **$990** | Every tool, AI included at launch — $65/month less than picking all six |

Examples: Data Cleaning + Dashboards = $38/month. All six tools picked
separately = $164/month, so the selector suggests All tools ($99).

**Placeholder prices.** Annual = 10 × monthly ("2 months free", 17% off);
change both together, then re-run `scripts/sync_stripe.py`. **Changing tools**
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
- **Every available service** for consultants and consultant-managed ("done
  for you") clients (`entitlements.gets_everything`).
- **Nothing else.** "Projects with a consultant" picked on Your needs are
  recorded as **interest** (table `account_services`) and shown to the
  consultant ("Asked for help with: …"); they don't unlock tools. A locked
  tool's "Add to my package" opens Plan & billing with that tool ticked.
- In a business, the team **owner** holds the package; team members share it
  but can't change it.

## What is recorded

Each download writes a row to `usage_events` (account, dataset name, row count,
runtime, billable minutes, amount; $0 with a plan that includes cleaning).
Subscriptions and consulting hours are paid through Stripe ([PAYMENTS.md](PAYMENTS.md));
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
