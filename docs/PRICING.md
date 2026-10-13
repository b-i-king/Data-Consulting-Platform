# Pricing

Code: `api/services/pricing.py`. Rates: environment variables (see `.env.example`).

> **All dollar amounts in the code are placeholders.** Set your real prices
> before launch.

## Services ("What does your company need help with?")

| Service | Model | Placeholder | Needs a consultant? |
|---|---|---|---|
| Data Cleaning | Runtime (per minute) | $0.50 / min | No — fully automated, always included |
| Data Strategy & Discovery | Consultation | Free call, then quoted | Yes → booking link |
| Data Exploration & Analysis | Package | from $250 | Optional |
| Data Visualization | Package | from $500 | Optional |
| Data Engineering | Package | from $750 | Yes |
| Data Science & Machine Learning | Package | from $1,500 | Yes |
| AI | Early access | No charge yet | No — shown with an "Early access" badge |

Each service is also a **skill category**: the skills behind it
(`data_skills/catalog.json`) appear as "What's included" chips on its card.

Clients can pick several. If any selected service needs a consultant, the quote
includes your `BOOKING_URL`.

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

## What is recorded

Each download writes a row to `usage_events` (account, dataset name, row count,
runtime, billable minutes, amount). **No payment is collected yet** — this is
the ledger a payment integration will charge from.

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
