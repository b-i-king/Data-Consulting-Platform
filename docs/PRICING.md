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
| Data Science & Machine Learning | Package | from $1,500 | Yes |
| Data Engineering | Package | from $750 | Yes |
| AI | Early access | No charge yet | No — shown with an "Early access" badge |

Each service is also a **skill category**: the skills behind it
(`.claude/skills/catalog.json`) appear as "What's included" chips on its card.

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
