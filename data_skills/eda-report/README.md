---
name: eda-report
description: Produce an Exploratory Data Analysis (EDA) report for a client's table — overview, summary statistics, missing values, duplicates, distributions, univariate, bivariate and multivariate analysis, outliers and key takeaways — with charts and a plain-English explanation of what each chart shows for THEIR data. Use as the first deliverable of Data Exploration & Analysis, or whenever a client asks "what does my data look like?".
argument-hint: "<table or file>"
---

# /eda-report - Exploratory Data Analysis Report

Turn a client's table into a readable report that walks through the classic
EDA flow, one headlined section at a time. Every section has charts **and** a
short explanation written from the data itself ("amount is strongly
right-skewed: most sales are small, with a long tail of a few large ones"),
so a business owner can read it without a data background.

**Inspiration:** the section order follows the well-known walk-through in
GeeksforGeeks, *"EDA - Exploratory Data Analysis in Python"*: inspect the data
→ summary statistics → missing values → duplicates → distributions →
univariate → bivariate → multivariate (correlation) → outliers with the
1.5 × IQR rule. This skill adds client-friendly wording, edge-case handling,
and a downloadable HTML version.

`explore-data` (dataset profiling) is still the right tool for a
column-by-column technical profile; this skill is the client-facing summary.

## Usage

```
/eda-report <table or file>
```

## Rules for the whole report

- **Headlines, not step numbers.** Sections are titled by what they cover
  ("Missing Values"), never "Step 3".
- **Every section explains itself.** A paragraph says what the chart is and
  what it shows for this table; bullets give the specific numbers.
- **Keep every section.** If a section can't apply (no number columns, no
  category columns, a one-row table), keep its headline and write
  "Not applicable because …".
- **Measures, not IDs.** Number columns whose names look like identifiers
  (order_id, zip, sku…) are not summarized or charted as measures.
- **US formats** in text: `1,234.56`, dates as `MM/DD/YYYY`.

## Procedure (one section each, in this order)

### Data Overview
- Count rows and columns; sort columns into Number, Category, Date,
  Identifier, Text and True/false. **Chart:** columns by type (bar).
- Say what the table appears to be about (from column names, e.g. "sales or
  transactions") and which dates it covers (first and last date, days spanned).
- Name the identifier column and whether it is one row per ID.

### Summary Statistics
- `describe()` on the number columns: count, mean, std, min, 25%, 50%
  (median), 75%, max — shown as a table.
- Explain what each statistic means once, then give the median, average and
  middle half for each column. Flag an average well above the median
  (a few large values pulling it up) and columns that never change.

### Missing Values
- Percentage of empty cells per column. **Chart:** bar chart of only the
  columns that have gaps, largest first. With no gaps, say so.
- Explain possible reasons (never collected, doesn't apply, lost) and flag
  columns more than half empty.

### Duplicate Values
- Count exact duplicate rows. **Chart:** unique vs. duplicate rows.
- Check whether the identifier column repeats, and explain when that is
  expected (several items per order) and when it is a problem.

### Data Distributions
- **Charts:** a histogram for up to 6 number columns that vary
  (bins = √rows, between 5 and 20).
- Describe each shape from its skewness: under 0.5 roughly symmetric;
  0.5–1 moderately skewed; over 1 strongly skewed (right = long tail of large
  values, left = long tail of small values). Recommend the median for skewed columns.

### Univariate Analysis
- **Charts:** top 10 values of up to 4 category columns (bar).
- Give each column's number of distinct values and its most and least common
  value; warn when one value covers 60% or more of rows.

### Bivariate Analysis
- **Box plot:** the main metric (a column named amount, revenue, sales,
  total… else the first number column) split by the category column that
  separates it best (highest eta squared, at most 20 groups), top 8 groups,
  sorted by median. Name the highest and lowest groups.
- **Scatter plot:** the pair of number columns with the strongest Pearson
  correlation, sampled to at most 500 rows (fixed seed), with r in words
  (|r| ≥ 0.7 strong, ≥ 0.4 moderate, ≥ 0.2 weak).

### Multivariate Analysis
- **Heatmap:** Pearson correlation of up to 10 number columns, on a
  diverging scale (cool = negative, warm = positive, pale = none).
- Name the strongest positive and negative pairs, note pairs above 0.9 that
  may carry the same information, and say plainly that **correlation is not causation**.

### Outliers
- **Charts:** a box plot per number column (up to 6) using the
  **1.5 × IQR rule**: values below Q1 − 1.5 × IQR or above Q3 + 1.5 × IQR are
  outliers. Table of fences and counts (high / low) for every number column.
- Explain that outliers can be errors **or** real rare events and should be
  checked with someone who knows the business, not removed automatically.

### Key Takeaways
- 3 to 6 bullets: the table's size, then the most notable result of each
  section (biggest gap, duplicates, strongest skew, most common value, group
  difference, strongest relationship, most outliers).

## Output

- **On screen:** each section as a card — headline, explanation, bullets,
  charts (two per row on wide screens), and its table.
- **Download:** the same report as one self-contained HTML file (inline SVG
  charts, light and dark mode, no scripts), ready to email to the client.

## Run it (platform implementation)

This procedure is also runnable code: [`skill.py`](skill.py) in this folder (category: **Data Exploration & Analysis**).

```bash
python -m data_skills run eda-report samples/sales.csv
```

Web: **Explore your data → Run a skill → EDA report (summary of your data)** (unlocked when the client chooses **Data Exploration & Analysis**) · API: `POST /api/skills/eda-report/run`.
Input: one table.
