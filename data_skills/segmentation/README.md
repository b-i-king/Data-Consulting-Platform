---
name: segmentation
description: Customer segments with K-means — group similar customers, products or clients by how often they buy, how much they spend and how recently, choose the number of groups by the silhouette score, name each group by what sets it apart and suggest an action for it.
---

# Customer segments (K-means groups)

Answer "who are my customers, really?" by putting similar ones together: the
regulars who spend the most, the light buyers, the ones who haven't been back
in a while. Each group gets a plain-English name and a suggested next step.
Works for products or clients too: anything you can describe with numbers.

## Inputs

- A table: sales lines (rolled up per customer) or one row per customer /
  product / client.
- **Who to group** (`group_by`): a customer / client / member column is found
  automatically; without one, the rows are grouped as they are.
- Optional: the **number columns** to use (default: all measures), the
  **purchase date** (auto), a fixed **number of groups** `k`, or the most to try
  (`max_k`, default 6).

## Procedure

### 1. One row per member
With a `group_by` column, roll the rows up per member ("RFM"):
- **purchases** — how many rows (frequency);
- **total_<measure>** — the sum of each amount or quantity (monetary);
  price-, rate- and score-like columns are averaged instead (`avg_<measure>`);
- **days_since_last** — days from their last purchase to the newest date in the
  data (recency), when there's a date column.

### 2. Prepare the numbers
- Drop columns that are the same for everyone (they separate no one).
- **Log-scale** skewed non-negative columns (skew > 1) so one very large
  customer doesn't pull every group toward them.
- **Standardize** every feature (mean 0, standard deviation 1) so dollars don't
  outweigh counts.

### 3. Choose the number of groups (K)
Fit K-means for K = 2 … `max_k` (10 restarts each) and keep the K with the best
**silhouette score**: how clearly each member sits in its own group rather than
the next one (1 = perfectly separate, 0 = overlapping). It's measured on at
most 3,000 members to keep compute low. A fixed `k` skips the search.

| Silhouette | What it means |
|---|---|
| 0.50 or more | Clearly separate groups |
| 0.25 – 0.50 | Reasonably distinct |
| below 0.25 | Overlapping — treat the groups as rough tiers |

### 4. Describe each group
- Segment 1 is the biggest. Each gets its size, share and the average of every
  feature, in the original units.
- **Name**: the two features where the group differs most from the overall
  average ("High total amount, many purchases"; "Not seen lately").
- **Action** (customer groups): not seen lately → a win-back offer; high spend
  → keep them (loyalty perks); low spend → a bundle or second-visit offer;
  otherwise → keep them engaged.

### 5. Recommendation
Lead with the groups and their actions; end with the disclaimer: the groups come
from patterns in the data, not from labels, so check each makes sense for the
business before acting on it.

## Compute

K-means on the rolled-up table: a few seconds for thousands of customers (the
search fits up to `max_k − 1` models).

## Related skills

- `predictive-modeling` — predict a number or a category from other factors.
- `reorder-planning` — just-in-time purchasing for products.
- `multivariate-analysis` — how many factors relate to each other.

## Run it (platform implementation)

This procedure is also runnable code: [`skill.py`](skill.py) in this folder (category: **Data Science & Machine Learning**).

```bash
python -m data_skills run segmentation samples/sales.csv --param KEY=VALUE
```

Web: **Explore your data → Run a skill → Customer segments (K-means groups)** (unlocked when the client chooses **Data Science & Machine Learning**) · API: `POST /api/skills/segmentation/run`.
Input: one table.

| Option | Type | Default | What it does |
|---|---|---|---|
| `group_by` | column | auto | Who to group: one row per value (auto: a customer / client column; if there is none, the rows are grouped as they are) |
| `features` | columns | auto | Number columns to group by (default: all measures) |
| `date_column` | column | auto | Purchase date, for days since the last purchase (auto) |
| `k` | int | auto | Number of groups (default: chosen by the silhouette score) |
| `max_k` | int | 6 | Most groups to try when choosing K (2 to 10) |
