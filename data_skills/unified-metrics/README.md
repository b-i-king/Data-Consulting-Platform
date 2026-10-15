---
name: unified-metrics
description: Test a cleaned table against the unified data design (fact.account_data, the stored data table) — IDs, dates and categories kept as they are, every metric column turned into rows described by metric_type, metric_subtype, metric_unit and metric_value — and prove by round trip that no information was lost. Runs the same code that stores client data; it stores nothing.
---

# Unified metrics (long format) test bench

Every client's spreadsheet names its numbers differently (`amount`, `unit_price`,
`shipping_cost`, `csat`, `hours_worked`). The unified table
(`fact.account_data`) stores all of them the same way, so one dashboard,
query or model works for every client:

| Kept as it is (in `attributes`) | Added for every metric value |
|---|---|
| IDs (`order_id`, `customer_id`) | `metric_type` — what is measured: price, cost, amount, score, quantity… |
| Dates (`order_date`) | `metric_subtype` — which one (optional): unit, line, shipping, csat, nps… |
| Categories / demographics (`store`, `age_group`) | `metric_unit` — usd, count, score, percent, hours… |
| | `metric_value` — the number |
| | `field_name` — the metric's unified name (`amount` → `line_amount`) |
| | `source_field` — the original column name, so every value traces back |

One wide row with three metrics becomes three rows. This skill runs the
**same code that stores client data** (`api/services/unified.py`) and stores
nothing — it is the place to test real client files before loading them.
The design is explained in docs/DATA_MODEL.md ("Unified data").

## Inputs

- One cleaned table.
- Optional: which columns are metrics, which are IDs.
- Optional: `event` — what each row is (`transaction/expense`…); guessed if not given.
- Optional: `metric_map` — your corrections, e.g.
  `{"amount": {"type": "cost", "subtype": "operating", "unit": "usd"}}`.

## Procedure

### Step 1 — Give every column a role and a unified name

The shared field map comes first (`fact.field_map`, the same pairs as
`FILE_UPLOAD_FIELD_MAP` in `unified.py`): `order_date` → `transaction_date`,
`store` → `location_name`, `amount` → `line_amount` (a metric: amount / line / usd).
For columns the map doesn't know:

- **date**: holds dates (real dates, or ISO text in a column with a date-like name).
- **id**: the name looks like an identifier (`*_id`, `*_number`, `sku`, `zip`, `phone`).
- **metric**: a number column that isn't an ID.
- **category**: everything else — text and yes/no columns, including
  demographics. These stay exactly as they are.

An unknown column keeps its own (snake_case) name. Override roles with
`metric_columns` / `id_columns` when a guess is wrong (a numeric store code, say).

### Step 2 — Describe each metric

Mapped metrics take their description from the field map (confidence
*mapped*). For the others, split the column name into words and look for a
known measure word:

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

A unit word in the name sets the unit (`discount_pct` → discount in percent).
The **other words** become the subtype: `shipping_cost` → cost / shipping.

**Context:** in an expense, an `amount` is money going out, so it becomes a
**cost**. If you chose the event (`transaction/expense`), that's certain; if
it was guessed from a `vendor`, `supplier` or `expense…` column, it is
flagged for you to confirm.

**Confidence:** *mapped* (from the field map), *set by you* (from
`metric_map`), *high* (a known word, at most two other words), *medium*
(extra words, or a context-based guess), *low* (no known word — the type is
just the column name and the unit "number"). Medium and low are listed under
**Needs a person's decision**.

### Step 3 — Name each record, then reshape

Each record needs a stable ID: an ID column with no blanks and no repeats;
else an order / invoice / receipt number that repeats (one row per item —
the rows become lines 1, 2, 3 of that order); else a fingerprint of the row's
content (the same row uploaded twice gets the same ID, so it's stored once).

Then reshape (like pandas `melt`): each metric cell with a value becomes one
row; the non-metric columns ride along in `attributes`. Blank metric values
make no row (no row = no value).

### Step 4 — Round trip (the proof)

Pivot the unified rows back into the original columns and compare **every
cell of every column** with the original (blank = blank, 3 = 3.0, dates as
dates). Report the number of cells checked and any mismatch. A failure
usually means a "number" column holds text.

### Step 5 — Review loop

The result includes `json_metric_map` — the descriptions it used. Edit any
type / subtype / unit, then run again with `metric_map` set to your edited
JSON. Repeat until nothing needs review. Then `sql_field_map` holds ready-made
`fact.field_map` rows for this file's new columns: add them (and the same
pairs to `FILE_UPLOAD_FIELD_MAP`) and the next file with these headers is
named and described automatically.

## Notes

- Money is assumed to be US dollars. A file in another currency, or weights
  in kilograms, needs `metric_map`. (QuickBooks records carry their currency,
  so the loader sets the unit from it.)
- A table with no number columns (a customer list) has nothing to unify — that's expected.
- Pairs with `data-dictionary-creator` (describe the columns) and `er-diagram`
  (how tables relate) when designing what to store.

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
| `event` | choice (auto, transaction/income, transaction/expense, transaction/refund, invoice/sent, invoice/received, invoice/paid, feedback/csat, feedback/rating, feedback/nps, inventory/adjustment, payroll/gross_pay, list/records) | auto | What each row is (default: guessed from the columns) |
| `preview_rows` | int | 200 | How many unified rows to show |
| `full_table` | bool | no | Also save the whole unified table as CSV (command line only) |
