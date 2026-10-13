# Dashboards: date ranges and comparisons

How the dashboard (the `build-dashboard` skill and the **Dashboard** tab in the
app) slices time — the same ideas Copilot Money uses for spending. Code:
`data_skills/_time.py` (the calculations) and
`data_skills/build-dashboard/skill.py` (the dashboard).

## When does the dashboard appear?

Whenever the cleaned table has a date column (real dates, or text where 80%+
of values parse as dates). Without dates, **Explore your data** opens on
**Run a skill** instead.

## What gets charted

By default, up to three number columns, best first: money (amount, revenue,
total, cost…), then counts (quantity, units…), then other numbers — plus
**Number of records** (e.g. orders). Per-unit columns (price, rate, score) are
never added up; if a table only has scores (CSAT, NPS), they're **averaged**.
Pick other columns with the `value_columns` option.

## The "as of" date

Every range ends on the **latest date in the data**, not today. A sales export
that ends on 10/10/2026 shows "month to date" as 10/01–10/10/2026 even if you
open it in November. (Override with the `as_of` option.)

## Date ranges — and what each is compared with

| Range | Covers (as of 10/10/2026) | Compared with |
|---|---|---|
| Last 4 weeks | 09/13 – 10/10/2026 (28 days) | the 28 days before: 08/16 – 09/12 |
| Last 3 months | 07/11 – 10/10/2026 | the 3 months before: 04/11 – 07/10 |
| Last year | 10/11/2025 – 10/10/2026 | the year before |
| Month to date | 10/01 – 10/10/2026 | **the same days** of last month: 09/01 – 09/10 |
| Year to date | 01/01 – 10/10/2026 | **the same days** of last year: 01/01 – 10/10/2025 |
| All time | everything | — |

Month-to-date near the end of a month never spills over: on 10/31 the
comparison is 09/01 – 09/30, not into October.

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
range and comparison built in — open it in any browser, offline, or email it
to a client. From the command line:
`python -m data_skills run build-dashboard yourfile.csv` → `outputs/build-dashboard/html_dashboard.html`.

## Colors

Gold (this period) and blue (previous period) were checked with the dataviz
palette validator in both themes: lightness, color strength, color-blind
separation, and contrast all pass. They live in `web/app/globals.css` as
`--color-chart-current` and `--color-chart-previous`.
