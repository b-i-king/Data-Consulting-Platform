---
name: flat-file-intake
description: Figure out how a set of uploaded flat files relate before cleaning — one file vs. many, same structure split by year, or different kinds of data (transactions, customers, products, payroll...) that link on shared keys. Use when a client sends multiple CSV/Excel files, when changing api/services/categorizer.py, or when deciding how to stack or join client files.
---

# Flat-File Intake (multi-file categorization)

A consultant's first question about a pile of files: *"Are these the same thing
split up, or different things?"* This skill is the written procedure;
`api/services/categorizer.py` is the code. **Keep them in sync.**

## Decision tree

```
How many tables? (each Excel sheet counts as one)
├── 1  ──────────────────────────────► go straight to `data-cleaning`
└── 2+ ─► group tables by column overlap (Jaccard ≥ 0.8)
          ├── a group with 1 table ─────► stand-alone; clean on its own
          └── a group with 2+ tables
              ├── different years? ─────► "partitioned by year": stack + keep source_file
              └── same/unknown years ───► "same structure": stack + keep source_file
          Then, between groups:
              shared ID-like column? ───► suggest a relationship (join key)
              no ID, but names/emails/phones? ► suggest a fuzzy link (review matches)
```

## Procedure

1. **Normalize column names for comparison only** — lowercase, spaces/dashes →
   `_` (`"Customer ID"` → `customer_id`). Never rename the client's columns in
   the output except to line up stacked files.
2. **Group by schema similarity.** Jaccard = shared columns ÷ all columns.
   ≥ 0.8 means "same structure". (Tune `SCHEMA_MATCH_THRESHOLD` if clients often
   add/remove one column between years.)
3. **Look for years** — first in the file name (`sales_2023.csv`), then in
   date-like columns (≥ 80% of values parse as dates).
4. **Guess the business entity** from column-name keywords (`ENTITY_KEYWORDS`):
   transactions, customers, products, employees/payroll, marketing,
   finance/accounting, operations, or "general". This is a guess — always show
   it as such and let a human correct it.
5. **Find join keys between groups** — shared columns whose names end in `id`,
   `key`, `number`, `code`, `email`, `sku`. If there is **no** shared key but both
   sides have names, emails, or phones, suggest a **fuzzy** link instead
   (`api/services/entity_matching.py`: exact email → exact phone → fuzzy name;
   92+ auto, 80–91 needs a person to review).
6. **Stack** each multi-table group with `pd.concat`, adding a `source_file`
   column first so year/origin is never lost.
7. Hand every group to `data-cleaning`.

## Extending the keyword list

When a new client's files are mis-labeled, add their column vocabulary to
`ENTITY_KEYWORDS` in `categorizer.py` and add a test in
`tests/test_services.py` using a tiny DataFrame with those columns.

## Limits to tell the client about

- Files exported with different column names for the same thing
  (`cust_no` vs `customer_id`) will not match automatically.
- Excel sheets with a title row above the header need the header fixed first.
- Relationships are suggestions; a consultant confirms them during
  Data Strategy & Discovery.
