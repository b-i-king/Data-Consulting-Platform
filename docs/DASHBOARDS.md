# Dashboards: KPIs, filters, date ranges and comparisons

How the dashboard (the `build-dashboard` skill and the **Dashboard** tab in the
app) is built and how it slices time — the same ideas Copilot Money uses for
spending. Code: `data_skills/build-dashboard/skill.py` (the dashboard),
`data_skills/_time.py` (the time calculations) and
`web/components/explore/Dashboard.tsx` (the screen).

## Built around the business (tabs and context)

The workspace's Dashboard page (`web/components/explore/DashboardTabs.tsx`,
`api/services/dashboards.py`) has layers:

1. **Business context** at the top: the industry (from sign-up) and "About your
   business" (Settings → Account: what they do, their goals, the questions they
   ask most).
2. **Overview** — the automatic dashboard described below.
3. **Their own tabs** — each tab is a list of saved plain-English questions
   ("total amount by store", "top 5 product by amount in 2025"), answered again
   on the open data every time (the ask-your-data skill, read-only). "+ Add tab"
   suggests questions from their key questions, the questions they ask most
   (`dim.question_log`), and their **industry** — written with the table's own
   column names (retail: best sellers, sales by store, how customers pay...).
   Up to 10 tabs, 12 questions each; adding tabs needs Data Visualization.

## When does the dashboard appear?

Whenever the cleaned table has a date column (real dates, or text where 80%+
of values parse as dates). It is part of the **Data Visualization** service:
until the client chooses that service it shows as a locked card with an **Add**
button. Without dates, **Explore your data** opens on **Run a skill** instead.

## Layout (top to bottom) — built from building blocks

The look comes from the Canva design "SaaS Dashboard UI Desktop Prototype"
(7 pages), in the site's own colors (navy-black / white, gold, Fira Code
numbers), light and dark. There is **no fixed template**: the blocks in
`data_skills/build-dashboard/templates/layouts.json` are picked and filled for
each table by `layout.py`, like a newspaper's front page — the most important
things **above the fold**.

1. **Header bar** — the business name (and industry), the dashboard tabs
   (Overview, plus any tabs the client added — keep it to one when you can:
   each tab re-runs its questions) and "Hello! <first name>". With no business
   name (individuals), the left shows the **data's name** (the file's name for one
   file, else the table's label like "Transactions"), tidied in the browser
   (`prettyDataName` in `web/lib/format.ts`): no extension, no underscores, each
   word capitalized — `sales_2026.csv` → "Sales 2026", `CUSTOMER_LIST.xlsx` →
   "Customer List" — and cut with "…" when too long. It's never saved.
2. **Heading, subtitle and the Filter button.** The subtitle is written from
   what this dashboard shows (`_subtitle` in `skill.py`), so it fits a business
   or an individual: "Total amount, transactions and average per transaction
   over time, by order type, product and 2 more"; with industry KPIs it starts
   with the sector ("Professional, Scientific, and Technical Services KPIs: …").
   (Data with no date column has no Overview, so "over time" is always true.)
   The heading is "At a glance",
   not the tab's name, so it doesn't repeat "Overview". **Rename** changes it;
   the new heading is kept only in that browser (localStorage, one per data
   set) — it is never sent to the server or saved in the database. Names
   (heading, business, viewer) aren't sent with the dashboard request at all;
   the browser puts them into the download itself. The filters (WHO the numbers are
   about: customer type, store, age group…) live in a panel that opens next to
   the button — hidden until wanted, so the dashboard stays clean. The button
   shows how many are on; Esc or a click outside closes it. Download (HTML) sits
   beside it.
3. **KPI row — above the fold.** 1 to 4 cards (Canva pages 1–7), best first:
   1. the main total (the **hero** card, in gold),
   2. the **industry KPIs** this table can compute (below),
   3. transactions, 4. average per transaction.
   Each shows the change vs. the previous range as a colored pill — **green**
   good, **gray** about the same (under 0.5%), **red** bad — using the metric's
   direction: a cost, refund or days-in-A/R going *down* is green. Industry KPIs
   show their benchmark under the number. With a single KPI, a trend chart sits
   beside it in the same row (Canva page 7).
4. **Container rows** — as many as there are visuals, mixed and matched from
   five patterns (6-column grid; phones stack them):

   | Pattern | Widths | Canva |
   |---|---|---|
   | `full` | 6 | — |
   | `half_half` | 3 + 3 | pages 2, 5, 6 |
   | `two_thirds_third` | 4 + 2 | page 1 |
   | `third_two_thirds` | 2 + 4 | page 4 |
   | `thirds` | 2 + 2 + 2 | page 3 |

   Visual priority: **trends as line charts** (the main total first; this range
   vs. the previous one), then **categories as horizontal bar charts** (name on
   the left, the value at the end of the bar, its change as a pill), then
   complementary visuals (the other KPIs' trends, more breakdowns). A trend
   takes the wide slot next to a bar; three bars share a row in thirds.
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

   **Phone turned sideways** (a screen under 500px tall): the two panels sit
   side by side in one slim row at the bottom (then "fits" means both panels'
   full labels together), and the site header scrolls away instead of staying
   pinned, so the KPI cards and charts keep most of the screen. Portrait
   phones get the stacked panels with short labels; KPI cards and charts go
   to one column under 760px wide. The downloaded HTML file behaves the same
   way (`templates/page.html`: the `max-height: 500px` rule and `fit()`).

## KPI card styles (one per dashboard)

The whole KPI row uses ONE style, so it looks consistent (`layout.kpi_style`,
`web/components/explore/KpiCard.tsx`, `templates/kpi_card.html`):

| Style | Shows | Chosen when |
|---|---|---|
| **number** | the title and the big number | no range has an earlier period to compare with |
| **change** | title, number, and the change vs. the previous range as a pill | the usual |
| **gauge** | a half-circle dial 0–100% with the healthy band shaded, "On target / Above / Below target", and the change | the data has 2+ percentage KPIs and at least one has a target band (`target` in `industry_metrics.json`, e.g. billable utilization 75–85%) |

In a gauge row only the percentage KPIs appear (a dial needs a 0–100 scale;
dollars don't have one) and the main total leads the containers as its trend.
`kpi_style` (auto / number / change / gauge) can force one; a forced gauge with
no percentages falls back to change. Percentages always change in **points**:
60% → 62% shows "▲ 2.0 pts" (gray under half a point), not "▲ 3.3%".
A daily-summary table (one row per date) leaves out "number of records" and
"average per transaction" — they'd only count days.

## Industry KPIs (and the generic fallback)

The client's industry (NAICS sector from sign-up) is sent as `industry`.
`data_skills/build-dashboard/industry_metrics.json` — readable copy
`INDUSTRY_METRICS.md`, from the "Metrics by industry" research — lists each
sector's KPIs as recipes: which columns they need (name hints) and how to
combine them (sum, mean, ratio of sums, share of rows, days between two dates,
per distinct person, net). The ones the table can compute become KPI cards and
trend containers ("Prime cost %" for a restaurant with food and labor cost
columns; "Days in A/R" for a clinic with service and paid dates). Ratios are
computed from their parts per period (30% of $100 and 50% of $10 is 32%, not
40%). **No match → the generic dashboard** (total, transactions, average per
transaction), with a line saying so. To teach it a new metric, add a recipe —
no code (`tests/test_dashboard_layout.py` checks the file).

## KPIs come from transaction types

Transaction data almost always has a column saying what **kind** of record each
row is. The dashboard finds it automatically, best match first:

1. an exact name: `event_type`, `transaction_type`, `txn_type`, `type`,
   `event_subtype`, `record_type`, `entry_type`, `activity_type` (the platform
   stored data uses `event_type` / `event_subtype`, see `db/schema.sql`);
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

## "How this was calculated" (the query behind a chart)

Every line and bar chart (not the KPI cards) has a **`</>` Query** button —
clicking the chart does the same. It opens a panel under the chart with:

- **the equivalent SQL** for what's on screen: the metric's math, the date
  range, the filters, and the grouping (by day / week / month for a line;
  the top 8 categories for a bar) — with a **Copy SQL** button — and an
  **SQL | pandas** switch: the pandas view is a simplified version of what the
  dashboard really runs, for developers who check numbers in Python;
- **the data source** (the files, stored table or connected-app records sent
  with the request) and the table's rows after cleaning;
- **the fields used**, and a note for industry KPIs naming the real columns
  their parts add up.

The dashboard is calculated with pandas, not a database, so the SQL is the
equivalent query, for checking the numbers — not a log of what ran. The skill
returns each metric's expression in `data.lineage`
(`build-dashboard/skill.py` `_lineage`); `web/components/explore/QueryPanel.tsx`
builds the query for the chart on screen. Hidden for view-only team members
(`showQuery`). Next step (not built): clicking a bar to filter the whole
dashboard, Tableau-style.

## Download

**Download (HTML)** saves a single self-contained file with every range and
comparison built in, for the filters chosen at the time (in its Filter panel),
in the same layout — header, title, Filter, KPI row, containers, time bar — with
a Light / Dark / System switch. It's put together from
`templates/page.html` + `kpi_card.html` + `container.html` (`render_html` in
`skill.py`). Open it in any browser, offline, or email it to a client.
In the web app the file is built **in the browser**, with no server call: the
dashboard on screen already holds every number, so `web/lib/dashboardFile.ts`
fetches the empty template (`web/public/dashboard-template.html`, a static
file Vercel serves) and pastes the title and data in, just as `render_html`
does. A download uses no Render time and no Supabase reads, and dashboard
responses stay about half the size (`include_html: false`). That template is a
copy: after changing anything in `templates/`, run
`python scripts/sync_dashboard_template.py` (pytest fails until you do). From the
command line:
`python -m data_skills run build-dashboard yourfile.csv` → `outputs/build-dashboard/html_dashboard.html`.

## Colors

Gold (this period) and blue (previous period, dashed) were checked with the dataviz
palette validator in both themes: lightness, color strength, color-blind
separation, and contrast all pass. Changes: green (`--color-good`) good, gray
(`--color-ink-muted`) about the same, red (`--color-bad`) bad — always with ▲ / ▼
too, so it never relies on color alone. They live in `web/app/globals.css` as
`--color-chart-current` and `--color-chart-previous`.
