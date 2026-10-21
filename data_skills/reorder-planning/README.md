---
name: reorder-planning
description: Just-in-time purchasing from sales history — per product, the average daily demand and its variation, safety stock, reorder point, and (with stock on hand) the date of the next purchase and how much to order; plus when products sell (weekday pattern, fast and slow movers, trend).
---

# Just-in-time purchasing (reorder planning)

Help an operations business buy **as late as possible without running out**:
less cash tied up in stock, less waste, no empty shelves. Works from the sales
history the business already has (a POS export, QuickBooks / Xero sales lines,
stored data).

**Operational data only.** It runs when the table has a product-like column
(product / item / SKU …) and a quantity-like column (qty / quantity / units …),
or when those columns are named; anything else (a survey, a customer list) is
refused with a plain message rather than planned.

## Inputs

- A sales table: one row per sale (or sales line) with a **date**, a
  **product** (item / SKU) and the **quantity sold**. Auto-detected when not named.
- Optional **stock on hand**: a column (the latest value per product is used),
  or values per product (`{"Sourdough Loaf": 40}`).
- **Lead time** (days from ordering to having it — default 7), **how often
  orders are placed** (default every 7 days), the **service level** (how sure
  to be of not running out — default 95%), and the **history window** used for
  demand (default the last 8 weeks).

## Procedure

### 1. Demand per product
Units sold per product per day over the history window, **counting days with no
sales as 0**. Average units a day (`d`) and the day-to-day standard deviation (`σ`).

### 2. Safety stock
`z × σ × √(lead time)` — the cushion for a busier-than-usual stretch while an
order is on its way. `z` comes from the service level (95% → 1.645, 99% → 2.326).

### 3. Reorder point
`d × lead time + safety stock`: place the order when stock on hand falls to this.

### 4. With stock on hand: the next purchase
- **Days of stock left** = on hand ÷ `d`.
- **Next purchase date** = the day stock reaches the reorder point.
- **Status**: *Order now* (at or below the reorder point), *Order this week*
  (it gets there within one ordering cycle), or *OK*.
- **Order quantity** = enough for the lead time + one ordering cycle, plus the
  safety stock, minus what's on hand.

Without stock on hand, give the reorder point and a typical order size
(`d × (lead time + cycle) + safety stock`) to plan around.

### 5. When it sells
- Units by **weekday** (all products): have stock in before the busiest day.
- **Slow movers**: no sales in 30+ days — buy only on demand.
- **Trend**: the last 4 weeks vs. the 4 before, per product.

### 6. Recommendation
Lead with what to order now and this week; end with the disclaimer: these are
recommendations based on recent sales, assuming the coming weeks look like them
and the lead time is right — adjust for promotions, seasons and supplier delays.

## Compute

A few pandas group-bys: well under a second for tens of thousands of rows.

## Related skills

- `predictive-modeling` — forecasting a number from other factors.
- `trend-analysis` — trends over time in any metric.

## Run it (platform implementation)

This procedure is also runnable code: [`skill.py`](skill.py) in this folder (category: **Data Science & Machine Learning**).

```bash
python -m data_skills run reorder-planning samples/sales.csv --param KEY=VALUE
```

Web: **Explore your data → Run a skill → Just-in-time purchasing (reorder planning)** (unlocked when the client chooses **Data Science & Machine Learning**) · API: `POST /api/skills/reorder-planning/run`.
Input: one table.

| Option | Type | Default | What it does |
|---|---|---|---|
| `date_column` | column | auto | Sale date |
| `item_column` | column | auto | Product / item / SKU |
| `quantity_column` | column | auto | Units sold |
| `on_hand_column` | column | auto | Optional: stock on hand (the latest value per product is used) |
| `on_hand` | json | auto | Optional: stock on hand per product, e.g. {"Sourdough Loaf": 40} |
| `lead_time_days` | int | 7 | Days from placing an order to having it on the shelf |
| `review_days` | int | 7 | How often you place orders, in days |
| `service_level` | float | 0.95 | How sure you want to be of not running out (0.5 to 0.999) |
| `history_days` | int | 56 | Recent days used to estimate demand |
| `as_of` | str | auto | Plan as of this date (YYYY-MM-DD; default: the last sale date) |
