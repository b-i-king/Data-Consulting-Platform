# Payments (Stripe)

Code: [`api/services/billing.py`](../api/services/billing.py) (logic),
[`api/routers/billing.py`](../api/routers/billing.py) (endpoints),
[`scripts/sync_stripe.py`](../scripts/sync_stripe.py) (products and prices).
Table: `dim.subscriptions` (its tools in `items`; [DATABASE.md](DATABASE.md)). The tools
and prices live in `pricing.py` and Stripe (no plans table). Prices: [PRICING.md](PRICING.md).

## What clients can buy

| What | Stripe setup | Billed |
|---|---|---|
| A package of tools ("build your own") — Data Cleaning (base), Data Exploration & Analysis, Dashboards, Data Engineering, Data Science & ML, AI (needs Data Engineering **and stored data**: `billing.ai_ready`) — or the All tools bundle | One **product** per tool (and the bundle), two **prices** each: monthly and annual (lookup keys `dcp_<tool>_month`, `dcp_<tool>_year`). One subscription holds **one item per tool**, all on the same interval | Recurring; the total is the sum of the tools; change tools anytime (prorated); cancel anytime in the billing portal |
| Consulting sessions (after the free 30-minute consultation) | **Not in this app's Stripe.** Booked and paid on Cal.com (`PAID_BOOKING_URL`; Cal.com's own Stripe app takes the $80), usually one hour at a time | Per session, on Cal.com. Nothing about payment is stored here; `dim.consultations` only tracks whether the free call happened. |
| Cleaning downloads without a plan | Not in Stripe yet: recorded in `usage_events` (per started minute) | Future: Stripe usage-based billing or a monthly invoice |

Card details are typed on **Stripe's own page** (Checkout), so they never touch
our server. The webhook is the only thing that turns a plan on or off.

## Admins, consultants and deleted accounts

- **Admins** (`ADMIN_EMAILS`) never pay: every tool and every download is free,
  and `POST /api/billing/checkout` refuses them (`billing.ADMIN_FREE`).
- **Consultants** pay for their own package like any client; working on a
  client, the client's package applies.
- **Delete my account** (`DELETE /api/accounts/me`) calls
  `billing.cancel_everything` first: each live Stripe subscription is canceled
  at once and the Stripe customer deleted, so the card is never charged again.

## Test mode (today)

With no `STRIPE_SECRET_KEY`, the site runs in **test mode**: choosing a plan
switches it on at once, "Cancel anytime" switches it off, and nothing is
charged. A banner in Plan & billing says so.

## Setting up Stripe (about 20 minutes)

1. **Create a Stripe account** at dashboard.stripe.com and stay in **Test mode**
   (toggle at the top) until everything works.
2. **API key:** Developers → API keys → copy the **Secret key** (`sk_test_...`).
3. **Create the products and prices** from your computer (prices come from
   `api/services/pricing.py`, so set your real prices there first):
   ```bash
   STRIPE_SECRET_KEY=sk_test_... python scripts/sync_stripe.py --dry-run   # look first
   STRIPE_SECRET_KEY=sk_test_... python scripts/sync_stripe.py
   ```
   Nothing is saved in the database: the app finds each price by its lookup key.
   Re-run it whenever you change a price: it creates the new price, moves the
   lookup key to it and archives the old one (existing subscribers keep their
   old price until you move them in Stripe).
4. **Webhook:** Developers → Webhooks → Add endpoint
   - URL: `https://<your-api>.onrender.com/api/billing/webhook`
   - Events: `customer.subscription.created`, `customer.subscription.updated`,
     `customer.subscription.deleted`, `checkout.session.completed`
   - Copy the **Signing secret** (`whsec_...`).
5. **Billing portal:** Settings → Billing → Customer portal → turn on
   "Cancel subscriptions" (at end of billing period) and "Update payment methods".
6. **On Render → Environment:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
   and `SITE_URL` = your Vercel address (where Stripe sends people back). Save.
7. **Try it** with Stripe's test card `4242 4242 4242 4242` (any future date,
   any CVC): subscribe in Plan & billing → you return to the workspace → the
   plan shows within a few seconds. Cancel in "Manage billing".
8. **Go live:** switch Stripe to Live mode, repeat steps 2–6 with the live key
   and a live webhook, and run the sync script with the live key.

## How it works

```
Plan & billing ──► POST /api/billing/checkout {plan_ids, interval} ──► Stripe Checkout page
                    (already subscribed? the subscription's items change in place, prorated)
                                                      │ client pays
Stripe ──► POST /api/billing/webhook (signed) ──► dim.subscriptions row (items = the tools)
Workspace ──► GET /api/accounts/services ──► plan's services unlocked
"Manage billing" ──► POST /api/billing/portal ──► Stripe portal ──► cancel
Stripe ──► webhook (cancel_at_period_end, then deleted) ──► plan off at period end
```

- The **webhook signature** is checked with `STRIPE_WEBHOOK_SECRET`, so a fake
  "paid" message is rejected (`tests/test_billing.py` covers it).
- Stripe may send an event twice; saving is keyed on Stripe's ids, so a repeat
  changes nothing.
- A business's plan belongs to the **team owner**; only they (or their
  consultant, acting for them) can buy or cancel. Team members share it.
- Every package includes Data Cleaning, so subscribers' cleaning downloads are free.
- AI isn't charged until it launches (`billable` in `pricing.py`): it's left out
  of the Stripe line items until then.

## Fair use

- At most `DAILY_DOWNLOAD_LIMIT` (default 20) cleaned-data downloads per client
  account in a rolling `DOWNLOAD_WINDOW_HOURS` (24); over that the download
  endpoint answers 429 with the time they can try again.
- Downloading the exact same cleaned data again inside the window is free
  (`X-Repeat-Download: 1`, amount $0.00). Sameness is a one-way SHA-256
  fingerprint saved in `usage_events.data_fingerprint`, never the data.
- Connector refresh add-ons (hourly, live) are priced in `pricing.py` but not
  sold through Stripe until connectors launch ([PRICING.md](PRICING.md#connected-data-how-often-it-refreshes)).

Code: [`api/services/fair_use.py`](../api/services/fair_use.py), tests in
`tests/test_fair_use.py`.
