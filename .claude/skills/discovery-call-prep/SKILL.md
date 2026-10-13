---
name: discovery-call-prep
description: Turn a prospect's intake answers (individual or business, where their data lives, services selected, cleaning report) into a one-page brief and draft data roadmap before a Data Strategy & Discovery call. Use when a client books a consultation, when preparing questions for a discovery call, or when drafting the analytics roadmap after one.
---

# Discovery Call Prep (Data Strategy & Discovery)

Goal: walk into every discovery call already knowing what the client has, what
they asked for, and the three questions that matter most — so the call is spent
on their business, not on logistics. Then leave with a roadmap draft.

## Inputs

- Intake answers from the platform: account type, data location (+ specific
  system, e.g. Snowflake / QuickBooks), selected services.
- The cleaning report and categorization from `/api/files/analyze`, if they
  uploaded files (groups, relationships, missing-value hot spots, sample size).
- Anything the client wrote in the booking form.

## Procedure

### 1. Data-source inventory

Build a table of every source mentioned or uploaded:

| Source | System | Owner | Refresh | Access method | Status |
|---|---|---|---|---|---|
| Sales | Shopify export (CSV) | Owner | Monthly, manual | Upload | ✅ received |
| Payroll | Gusto | Bookkeeper | Bi-weekly | Connector (planned) | ❓ ask |

Flag sources that are **mentioned but not seen** — those are the first ask.

### 2. Readiness snapshot

From the cleaning report: rows, % missing, duplicates removed, and the
sample-size verdicts. One line each, plain English. Note which business
questions the data **can** and **cannot** support yet.

### 3. Draft business objectives

Translate the services they ticked into candidate objectives, e.g.
"Dashboards" → "See weekly sales by location without building a spreadsheet".
Mark each as *confirm on call*.

### 4. The three questions

Write the three questions that most change the scope. Good defaults:
1. "What decision would you make differently if you had this answer today?"
2. "Where does the data that isn't in these files live, and who can export it?"
3. "Who will look at the results, and how often?"

### 5. Roadmap draft (fill in after the call)

| Phase | Weeks | Outcome | Service |
|---|---|---|---|
| 1. Foundation | 1–2 | Sources connected, cleaned, documented | Data Cleaning, Data Engineering |
| 2. Answers | 2–4 | Key questions answered, trends identified | Data Exploration & Analysis |
| 3. Visibility | 4–6 | Dashboard the team checks weekly | Data Visualization |
| 4. Foresight | 6+ | Forecast / prediction where data supports it | Data Science & ML |

Only include phases the data and budget support. Small businesses often stop
at phase 2 or 3 — that is a success, not a shortfall.

## Output

`outputs/discovery/<client>-brief.md` (git-ignored): inventory, readiness,
objectives, three questions, roadmap draft. Keep it to one page.

## Related skills

`sample-size` (feeds the readiness snapshot) · `data-enrichment` (when a key
question needs data the client does not have).
