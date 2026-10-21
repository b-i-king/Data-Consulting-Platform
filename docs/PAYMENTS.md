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
  Only an anonymous count is kept (`dim.account_deletions`), including whether
  they had a paid package and the reason they picked, if any.

## Testers

`dim.accounts.is_tester` (Admin → Testers, `POST /api/admin/accounts/{id}/tester`)
lets someone test without paying while real clients pay.
`billing.charges_real_money(account)` = Stripe is on **and** the account isn't a
tester; checkout, "Cancel" and "Manage billing" use it instead of
`stripe_enabled()`, so a tester gets test mode: the package switches on at once
(a subscription row with no Stripe id) and "Cancel" switches it off. Downloads are
free for them (`entitlements.downloads_included`). The workspace says
"Tester account" instead of "Test mode" (`billing.tester` in
`GET /api/accounts/services`). Rules (`billing.set_tester`): plan holders only
(a business's colleagues follow the owner), not admins (already free), and not
while they have a live Stripe subscription (409 — cancel it first). Turning it
off cancels their free package.

## Stripe's customer portal ("Manage billing")

Turn on (Stripe → Settings → Billing → Customer portal): **cancel subscriptions**
(with **Collect cancellation reason**), **update payment methods**, invoice history.
Leave **switch plans OFF**: package changes (adding tools, All tools, monthly ↔
annual) happen on the site, where the rules live (AI needs Data Engineering,
the bundle replaces the tools, free tools, trials) — the portal knows none of them.
The webhook saves why a client canceled (`cancellation_details`: Stripe's reason
code, their comment, when) on `dim.subscriptions` (`cancel_feedback`,
`cancel_comment`, `canceled_at`); Admin → "Why clients cancel" counts them.

## Changing a package mid-cycle

A client has ONE subscription — one receipt with a line per tool, all monthly or
all annual (Stripe's rule; mixing would mean two subscriptions and two bills).
Changing it never starts a second checkout; `billing.start_checkout` edits that
subscription in place (`tests/test_package_changes.py` covers each case):

| Change | What Stripe gets |
|---|---|
| Add a tool | one new item at the tool's price for the current interval |
| Drop a tool | that tool's item removed |
| Switch to All tools | every tool's item removed, the bundle's added (and back the same way) |
| Monthly ↔ annual | every item swapped for the other interval's price |
| Same package again | nothing (409 "That's already your package") |

- `proration_behavior="always_invoice"`: the difference is billed (or credited)
  **right away**, so an annual client adding a tool in month 2 pays for the rest of
  that year now, not at next year's renewal.
- `cancel_at_period_end=False`: changing the package undoes a scheduled cancel.
- Tools that aren't charged yet (AI before launch) never go to Stripe; they ride
  in the subscription's metadata (`free_plans`) and come back with the webhook.

## Trials and seasonal sales

`api/services/offers.py` decides a first package's offer; `billing.start_checkout`
passes it to Stripe Checkout:

- **Trial** → `subscription_data.trial_period_days` (the longest trial among the
  tools picked). Stripe sends the subscription as `trialing`; the webhook marks
  `dim.accounts.trial_used_at`, so the account never gets a second trial. In test
  mode the package starts as `trialing` at once.
- **Sale** (no trial) → `discounts: [{"coupon": "dcp_sale_<id>_<percent>"}]`.
  `stripe_catalog.ensure_coupon` makes that coupon the first time (percent off,
  `duration: "once"` = the first invoice). Stripe allows a coupon OR the
  promotion-code box, so during a sale the box is hidden; otherwise
  `allow_promotion_codes` stays on.
- Changing an existing package gets neither.

## Changing a price

Admin → Prices (`PUT /api/admin/prices/{plan}`, `api/services/price_store.py`).
Stripe is updated first: `stripe_catalog.sync_plan` creates a new Stripe price
with the same lookup key (`transfer_lookup_key`) and archives the old one, so
checkout (`billing._price_id`, which looks up by key) charges the new amount at
once. Existing subscribers keep their old price (Stripe keeps billing an archived
price) until you move them in the Stripe dashboard. If Stripe returns an error,
the price is not saved (502), so the site and Stripe never disagree. Then
`dim.plan_prices` is saved and the running API uses the new price.
`scripts/sync_stripe.py` reads `dim.plan_prices` too: run it once when you
first connect Stripe, and after editing the table by hand.


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
     `customer.subscription.deleted`, `checkout.session.completed`, `charge.refunded`
   - Copy the **Signing secret** (`whsec_...`).
5. **Stripe Connect (paying consultants):** Settings → Connect → get started,
   choose **Express** accounts, platform country United States. Fill in your
   platform profile (Stripe asks what you do: "marketplace / platform for data
   consultants"). Then each consultant connects their own payout account from
   Settings → Consultant → Payouts (Stripe asks for their identity, bank and tax
   details; the app never sees them). Under Connect → Tax reporting you can let
   Stripe file and send consultants' 1099s. `sync_stripe.py` (step 3) also makes
   the two products consultant help is sold under ("Consultant setup", "Ongoing
   help from your consultant"); their prices are sent at checkout.
6. **Billing portal:** Settings → Billing → Customer portal → turn on
   "Cancel subscriptions" (at end of billing period) and "Update payment methods".
7. **On Render → Environment:** `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET`,
   and `SITE_URL` = your Vercel address (where Stripe sends people back). Save.
8. **Try it** with Stripe's test card `4242 4242 4242 4242` (any future date,
   any CVC): subscribe in Plan & billing → you return to Plan & billing in the
   workspace (`/workspace/settings/billing?checkout=success`; canceling returns
   with `?checkout=canceled`, and "Manage billing" returns there too) → the
   plan shows within a few seconds. Cancel in "Manage billing".
9. **Go live:** switch Stripe to Live mode, repeat steps 2–7 with the live key
   and a live webhook, and run the sync script with the live key.

## Paying consultants (Stripe Connect)

Code: `api/services/consulting_billing.py` (routes: `GET/POST /api/billing/consulting*`,
`POST /api/consultant/settings/payouts*`, `GET/PUT /api/admin/consulting-prices`).
Rows: `dim.consultant_engagements`; prices: `dim.consulting_prices`.

1. **Onboarding:** a consultant connects a Stripe **Express** account
   (`start_payouts` → Stripe's page → back to Settings → Consultant). It's ready
   when Stripe says the account's `transfers` capability is active
   (`payouts_status` asks Stripe on the next page load). Admins skip this.
2. **Checkout:** the client ticks the billing terms (stored as
   `terms_accepted_at`) and pays on the platform's Checkout page — setup as a
   one-time payment, ongoing help as its own subscription (separate from the
   tools package). The price is worked out on the server from the services
   picked and sent as Stripe `price_data` on the "consult_setup" /
   "consult_retainer" products. Each is a **destination charge**:
   `transfer_data.destination` = the consultant's account, and the platform
   keeps `platform_fee_percent` (`application_fee_amount` for setup,
   `application_fee_percent` for the subscription). Metadata `kind: consulting`.
3. **Webhook:** `checkout.session.completed` marks a setup paid (and saves its
   payment intent); `customer.subscription.*` with `kind: consulting` keeps
   ongoing help's status — never the tools package; `charge.refunded` marks a
   fully refunded setup "refunded".
4. **Ending ongoing help:** "End ongoing help" (or the Stripe portal) sets
   `cancel_at_period_end`: it stays on, and paid, to the period's end.
   Deleting the account cancels it at once.

### The rules for the gaps

| Situation | What happens |
|---|---|
| Change services or hours | One ongoing help at a time: end it, choose again once it has ended (changes happen at a period boundary) |
| Switch consultant while ongoing help is running | Refused (`can_switch`, 409) with the date it ends. Need it sooner? End the subscription in the Stripe dashboard — the webhook marks it ended — and refund if you choose |
| Consultant **deactivated** (Admin) or **deletes their account** | Their clients' ongoing help ends **at once** (`end_for_consultant`, `ended_reason`); the Admin notice lists who; the client sees why in Plan & billing |
| **Refunds** | In the Stripe dashboard: Payments → the payment → Refund, with **"Reverse transfer"** ticked so the consultant's share comes back too (and "Refund application fee" for yours). A fully refunded setup shows as "refunded" (webhook) |
| Client's package drops a tool their ongoing help covers | Ongoing help continues to the period's end; the consultant can only use the tools the client has |

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
- A tool whose service isn't launched (`available=False`, `billable` in
  `pricing.py`) is left out of the Stripe line items until it launches. AI
  launched in 10/2026 and is charged like the other tools.

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
