---
name: ask-your-data
description: Answer a client's plain-English question about their cleaned data by translating it into a safe pandas (or SQL) computation, running it, and replying with the number, how it was computed, and its caveats. Defines the guardrails for the platform's AI feature. Use when a client asks "what were my top products last quarter?"-style questions, or when building/changing the AI question-answering feature.
---

# Ask Your Data (AI)

The AI category lets clients ask questions in everyday language and get
trustworthy answers from **their own data** using built-in models. This skill is
both the manual procedure and the spec for the platform feature.

## Guardrails (non-negotiable)

1. **Answer only from the data provided.** If the data cannot answer the
   question, say so and say what data would.
2. **Show your work.** Every answer includes the computation in one line
   (e.g. "sum of `amount` where `order_date` is in Q3 2024, grouped by `product`").
3. **Read-only.** Generated code may filter, group, aggregate, and sort. It may
   never write files, call the network, import modules, or modify the data.
4. **Respect sample size.** If a comparison rests on fewer than ~30 rows per
   group, add a caution (see `sample-size`).
5. **No PII in answers** unless the client asked for a specific record they
   own; run `pii-flag` on new datasets first.

## Procedure

### 1. Ground the question in the schema
List columns, types, and 3 example values per column (the platform's cleaning
report already has these). Map the client's words to columns: "sales" →
`amount`, "last quarter" → date filter on `order_date`. If a term is ambiguous,
ask one clarifying question instead of guessing.

### 2. Plan
Write the computation in words first: filter → group → aggregate → sort → limit.

### 3. Compute (pandas)
```python
q3 = df[(df.order_date >= "2024-07-01") & (df.order_date < "2024-10-01")]
answer = q3.groupby("product")["amount"].sum().nlargest(5)
```
For data in a warehouse, use `write-query` / `sql-queries` instead.

### 4. Check
Sanity-check totals against the full dataset; confirm row counts after
filters; watch for missing values that silently drop rows.

### 5. Reply
```
Your top product in Q3 2024 was Sourdough Loaf ($4,210, 18% of sales).
How: total `amount` by `product` for orders dated Jul 1–Sep 30, 2024 (1,204 orders).
Note: 37 orders had no product and were left out.
```
Offer one natural follow-up ("Want this by month?").

## Implementation notes for the platform feature

- Model: a current Claude model via the Anthropic API, with the schema summary
  (not the raw data) in the prompt, returning a structured plan.
- Execute the plan with a whitelist of pandas operations (filter, groupby,
  agg, sort, head) — never `eval` free-form generated code.
- Log question, plan, and row counts (not data values) for quality review.
- Use `data-context-extractor` to give the model each client's business
  vocabulary once they have recurring usage.

## Related skills
`setup-data-workspace` (local "talk to your data" setup with DuckDB) ·
`data-context-extractor` (client-specific vocabulary) · `pii-flag`.
