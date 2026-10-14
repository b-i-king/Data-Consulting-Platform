# Payments (Stripe)

Code: [`api/services/billing.py`](../api/services/billing.py) (logic),
[`api/routers/billing.py`](../api/routers/billing.py) (endpoints),
[`scripts/sync_stripe.py`](../scripts/sync_stripe.py) (products and prices).
Tables: `subscription_plans`, `subscriptions`, `consulting_purchases`
([DATABASE.md](DATABASE.md)). Prices: [PRICING.md](PRICING.md).

## What clients can buy

| What | Stripe setup | Billed |
|---|---|---|
| A plan — Data Cleaning, Dashboards, Data Engineering, All tools | One **product** per plan, two **prices**: monthly and annual (lookup keys `dcp_<plan>_month`, `dcp_<plan>_year`) | Recurring; cancel anytime in the billing portal |
| Consulting hours (after the free 30-minute consultation) | Product "Consulting (1 hour)", price `dcp_consulting_hour` ($80) | One-time, any number of hours (1–40) |
| Cleaning downloads without a plan | Not in Stripe yet: recorded in `usage_events` (per started minute) | Future: Stripe usage-based billing or a monthly invoice |

Card details are typed on **Stripe's own page** (Checkout), so they never touch
our server. The webhook is the only thing that turns a plan on or off.

## Test mode (today)

With no `STRIPE_SECRET_KEY`, the site runs in **test mode**: choosing a plan
switches it on at once, "Cancel anytime" switches it off, buying hours records
them, and nothing is charged. A banner in Plan & billing says so.

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
   Add `DATABASE_URL=...` to also save the Stripe ids in `subscription_plans`.
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
Plan & billing ──► POST /api/billing/checkout ──► Stripe Checkout page
                                                      │ client pays
Stripe ──► POST /api/billing/webhook (signed) ──► subscriptions row (status active)
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
- A plan that includes Data Cleaning makes cleaning downloads free.
