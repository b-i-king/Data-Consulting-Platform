# Dashboards: KPIs, filters, date ranges and comparisons

How the dashboard (the `build-dashboard` skill and the **Dashboard** tab in the
app) is built and how it slices time — the same ideas Copilot Money uses for
spending. Code: `data_skills/build-dashboard/skill.py` (the dashboard),
`data_skills/_time.py` (the time calculations) and
`web/components/explore/Dashboard.tsx` (the screen).

## When does the dashboard appear?

Whenever the cleaned table has a date column (real dates, or text where 80%+
of values parse as dates). It is part of the **Data Visualization** service:
until the client chooses that service it shows as a locked card with an **Add**
button. Without dates, **Explore your data** opens on **Run a skill** instead.

## Layout (top to bottom)

1. **Filters** — dropdowns for WHO the numbers are about (below), plus
   **Download dashboard (HTML)**.
2. **Overview** — two cards, **Total amount** and **Transactions**; click one to
   chart it over time ("Transactions over time"), with a values-by-period table.
3. **KPIs** — one card and one line chart per KPI (below).
4. **Breakdown** — the top 8 values of a category column (e.g. product) vs. the
   previous period.
5. **Time bar** — two separate, slightly see-through (frosted) panels floating
   at the bottom of the screen while the dashboard is in view, so the chart
   behind stays visible: date ranges on top, comparisons below
   (`web/components/explore/TimeBar.tsx`). When the full labels don't fit the
   width available — a phone, a narrow window, a zoomed-in browser — both panels
   switch to short labels. This is measured, not tied to a screen size:

   | Full | Short | | Full | Short |
   |---|---|---|---|---|
   | Last week | 1W | | | |
   | Last 4 weeks | 4W | | Day over day | DoD |
   | Last 3 months | 3M | | Week over week | WoW |
   | Last year | 1Y | | Month over month | MoM |
   | Month to date | MTD | | | |
   | Year to date | YTD | | | |
   | All time | All | | | |

   Short labels come from `data_skills/_time.py` (`RANGE_SHORT`, `COMPARISON_SHORT`);
   screen readers and hover tooltips always give the full label.

## KPIs come from transaction types

Transaction data almost always has a column saying what **kind** of record each
row is. The dashboard finds it automatically, best match first:

1. an exact name: `event_type`, `transaction_type`, `txn_type`, `type`,
   `event_subtype`, `record_type`, `entry_type`, `activity_type` (the platform
   warehouse uses `event_type` / `event_subtype`, see `db/warehouse.sql`);
2. any `*_type`, `*_kind`, `*_class` or `*_category` column — except ones about
   the customer (`customer_type` is a filter, not a KPI);
3. a column named `category`, `kind` or `class`.

It must have 2–50 distinct values. The **3–4 values with the most rows** become
the KPIs (`top_kpis`, default 4). For the sample bakery: In-store, Online pickup,
Delivery, Catering. Each KPI's number is that type's total of the money column
(`amount`; or its average for score columns, or its row count if there's no
number column), and its line chart compares this period with the previous one.

The KPI list is ranked on **all** rows, so the cards don't jump around when you
filter. Choose a different column with `kpi_column`, or the measure with
`value_column`. **No type column?** The best number columns become the KPIs:
money (amount, revenue, total, cost…) first, then counts (quantity, units…).
Per-unit columns (price, rate, score) are never added up; if a table only has
scores (CSAT, NPS), they're **averaged**.

## Filters: customer type and demographics

Filters narrow the dashboard to a group of customers. Offered automatically
(up to 4, each with 2–30 values), best group first:

| Group | Column names containing… |
|---|---|
| Customer groupings | customer, client, member, segment, tier, loyal, cohort, persona |
| Demographics | gender, sex, age / age_group, generation, income, education, ethnicity, race, marital, household, language |
| Place | region, state, city, county, zip, country, location, store, branch, channel, market |

Picking a value asks the server to rebuild the dashboard on matching rows only
(the files are re-sent from browser memory; nothing is stored), keeping the
range and comparison you picked. Dropdown choices come from all rows, so they
don't shrink as you filter. Options: `filters` (e.g. `{"customer_type": "Wholesale"}`)
and `filter_columns`.

**Up to 9 filters**, in one "Filters" container, in rows of **at most 3**
kept as even as possible (rows differ by at most one filter), so the box always
looks tidy (`filterRows` in `web/components/explore/Dashboard.tsx`):

| Filters | Rows |
|---|---|
| 1 / 2 / 3 | one row |
| 4 | 2 + 2 |
| 5 | 3 + 2 |
| 6 | 3 + 3 |
| 7 | 3 + 2 + 2 |
| 8 | 3 + 3 + 2 |
| 9 | 3 + 3 + 3 |

On phones each filter takes the full width. The skill picks the filters in
order: customer groupings, then demographics, then place (`MAX_FILTERS = 9`).

## The "as of" date

Every range ends on the **latest date in the data**, not today. A sales export
that ends on 10/10/2026 shows "month to date" as 10/01–10/10/2026 even if you
open it in November. (Override with the `as_of` option.)

## Date ranges — and what each is compared with

| Range | Covers (as of 10/10/2026) | Compared with |
|---|---|---|
| Last week | 10/04 – 10/10/2026 (7 days) | the 7 days before: 09/27 – 10/03 |
| Last 4 weeks | 09/13 – 10/10/2026 (28 days) | the 28 days before: 08/16 – 09/12 |
| Last 3 months | 07/11 – 10/10/2026 | the 3 months before: 04/11 – 07/10 |
| Last year | 10/11/2025 – 10/10/2026 | the year before |
| Month to date | 10/01 – 10/10/2026 | **the same days** of last month: 09/01 – 09/10 |
| Year to date | 01/01 – 10/10/2026 | **the same days** of last year: 01/01 – 10/10/2025 |
| All time | everything | — |

Month-to-date near the end of a month never spills over: on 10/31 the
comparison is 09/01 – 09/30, not into October.

## Only what the data can support

The time bar offers a range or comparison only when there's enough history for
it (`data_skills/_time.py` → `available_ranges`, `available_comparisons`):

| Offered when… | Ranges | Comparisons |
|---|---|---|
| the data covers the whole window | Last week (7 days), Last 4 weeks (28), Last 3 months, Last year | |
| it also covers the same days last month / last year (so the comparison is fair) | Month to date, Year to date | |
| there's **more than a year** of data | All time | |
| at least 2 days / 2 weeks / 2 months (in the data and in the range) | | Day / Week / Month over… |

| Data covers | Time bar shows |
|---|---|
| 1 day | nothing — no time bar and no line charts, just the totals and a note |
| 2–6 days | day over day (charts over all the data; no range panel) |
| 1–3 weeks | Last week · day over day (week over week from 2 weeks) |
| 4 weeks | + Last 4 weeks · week over week |
| about 6 weeks | + Month to date |
| 3 months | + Last 3 months · month over month |
| more than a year | + Last year · All time (and Year to date once last year's matching days exist) |

If the requested default range isn't available, the dashboard opens on the
longest rolling window the data has. Filtering (e.g. to one customer type) can
shorten the history, so the choices follow the filtered rows.

The KPI cards show each range's total (or average) and the % change vs. its
comparison, with ▲/▼ and the number — never color alone.

## Comparisons on the line chart

| Comparison | One point per | "% change" in the tooltip and table |
|---|---|---|
| Day over day (DoD) | day | vs. the day before |
| Week over week (WoW) | week, Monday–Sunday | vs. the week before |
| Month over month (MoM) | calendar month | vs. the month before |

Two lines are drawn:

- **This period** (gold) — the selected range.
- **Previous period** (blue) — the **previous range lined up point by point**:
  day 1 of the prior 4 weeks under day 1 of these 4 weeks; January last year
  under January this year (Year to date + Month over month = a year-over-year
  chart). The tooltip says which date each blue point is lined up with.

Day over day is offered only for ranges of 400 days or less (more points than
that is unreadable); the app switches to week over week automatically.

## Partial periods

If the data stops partway through a week or month — or starts partway through
one — that period is marked **partial**. The last one is drawn faded with a
hollow dot and labeled "(partial)" in the table, so a half-finished month
isn't mistaken for a drop. For a fair comparison of the current month, use
**Month to date**.

## Download

**Download dashboard (HTML)** saves a single self-contained file with every
range and comparison built in, for the filters chosen at the time (listed at
the top of the file) — open it in any browser, offline, or email it to a client.
The web app builds this file only when you click the button (`include_html`),
which keeps every other dashboard response about half the size. From the
command line:
`python -m data_skills run build-dashboard yourfile.csv` → `outputs/build-dashboard/html_dashboard.html`.

## Colors

Gold (this period) and blue (previous period) were checked with the dataviz
palette validator in both themes: lightness, color strength, color-blind
separation, and contrast all pass. They live in `web/app/globals.css` as
`--color-chart-current` and `--color-chart-previous`.
