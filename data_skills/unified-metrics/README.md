---
name: unified-metrics
description: Reshape a cleaned table into the unified (long) metric format — IDs, dates and categories kept as they are, every metric column turned into rows described by metric_type, metric_subtype, metric_unit and metric_value — and prove by round trip that no information was lost. A test bench for the future unified fact table; it stores nothing.
---

# Unified metrics (long format) test bench

Every client's spreadsheet names its numbers differently (`amount`, `unit_price`,
`shipping_cost`, `csat`, `hours_worked`). A unified table stores all of them the
same way, so one dashboard, query or model works for every client:

| Kept as it is | Added for every metric value |
|---|---|
| IDs (`order_id`, `customer_id`) | `metric_type` — what is measured: price, cost, amount, score, quantity… |
| Dates (`order_date`) | `metric_subtype` — which one (optional): unit, shipping, csat, nps… |
| Categories / demographics (`store`, `age_group`) | `metric_unit` — usd, count, score, percent, hours… |
| | `metric_value` — the number |
| | `source_column` — the original column name, so every value traces back |

One wide row with three metrics becomes three long rows. Nothing is stored —
this skill is the place to **test the shape on real client files before the
warehouse table is built** (proposal: `fact_metric`, docs/DATA_MODEL.md).

## Inputs

- One cleaned table.
- Optional: which columns are metrics, which are IDs.
- Optional: `metric_map` — your corrections, e.g.
  `{"amount": {"type": "cost", "subtype": "operating", "unit": "usd"}}`.

## Procedure

### Step 1 — Give every column a role

- **date**: holds dates (the same detection the dashboards use).
- **id**: the name looks like an identifier (`*_id`, `*_number`, `sku`, `zip`, `phone`).
- **metric**: a number column that isn't an ID.
- **category**: everything else — text and yes/no columns, including
  demographics. These stay exactly as they are.

Override with `metric_columns` / `id_columns` when a guess is wrong (a numeric
store code, say).

### Step 2 — Describe each metric

Split the column name into words and look for a known measure word:

| Word in the name | metric_type | metric_unit |
|---|---|---|
| csat, nps, rating, stars, score | score (subtype csat / nps / rating) | score |
| price | price | usd |
| cost, expense, spend, fee | cost | usd |
| amount, total, revenue, sales, income | amount | usd |
| salary, wage, pay | pay | usd |
| rate, pct, percent, margin | rate / margin | percent |
| hours, minutes, days | time | hours / minutes / days |
| quantity, qty, units / count, orders, visits | quantity / count | count |

The **other words** become the subtype: `shipping_cost` → cost / shipping,
`unit_price` → price / unit.

**Context:** in an expenses table (a `vendor`, `supplier`, `expense…` column),
an `amount` is money going out, so it becomes a **cost** — flagged for you to confirm.

**Confidence:** *high* (a known word, at most two other words), *medium*
(extra words, or a context-based guess), *low* (no known word — the type is just
the column name and the unit "number"). Medium and low are listed under
**Needs a person's decision**.

### Step 3 — Reshape (pandas `melt`)

Add `row_id` (1, 2, 3… in the original order), keep the id / date / category
columns on every row, and turn each metric column into rows. Blank metric
values are left out unless `keep_missing` is on (they carry no information,
and the round trip treats "left out" and "blank" the same).

### Step 4 — Round trip (the proof)

Pivot the long table back to one column per `source_column`, by `row_id`, and
compare every metric cell with the original (blank = blank). Report the number
of cells checked and any mismatch. A failure usually means a "number" column
holds text.

### Step 5 — Review loop

The result includes `json_metric_map` — the definitions it used. Edit any
type / subtype / unit, then run again with `metric_map` set to your edited
JSON. Repeat until nothing needs review. The result also proposes the
warehouse table (`sql_proposed_table`) — a proposal only, not applied.

## Notes

- Units are guessed from the type (money → usd). A file in another currency,
  or weights in kilograms, needs `metric_map`.
- A table with no number columns (a customer list) has nothing to unify — that's expected.
- Pairs with `data-dictionary-creator` (describe the columns) and `er-diagram`
  (how tables relate) when designing the warehouse.

## Run it (platform implementation)

This procedure is also runnable code: [`skill.py`](skill.py) in this folder (category: **Data Engineering**).

```bash
python -m data_skills run unified-metrics samples/sales.csv --param KEY=VALUE
```

Web: **Explore your data → Run a skill → Unified metrics test bench (long format)** (unlocked when the client chooses **Data Engineering**) · API: `POST /api/skills/unified-metrics/run`.
Input: one table.

| Option | Type | Default | What it does |
|---|---|---|---|
| `metric_columns` | columns | auto | Columns to treat as metrics (default: every number column that isn't an ID) |
| `id_columns` | columns | auto | Columns to keep as IDs (default: guessed from the names) |
| `metric_map` | json | auto | Fix the guesses, e.g. {"unit_price": {"type": "price", "subtype": "unit", "unit": "usd"}} |
| `keep_missing` | bool | no | Keep a row for blank metric values (default: leave them out) |
| `preview_rows` | int | 200 | How many unified rows to show |
| `full_table` | bool | no | Also save the whole unified table as CSV (command line only) |
