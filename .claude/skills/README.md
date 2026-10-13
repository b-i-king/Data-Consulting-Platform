# Skills used on this project

Skills are instruction files Claude Code loads when a task matches. They keep
the way we clean, analyze, and deliver data **consistent and reproducible**,
whether the work is done by the web app, by you, by Claude, or by a future
consultant on the team.

All 30 skills live in this folder (editable copies), so the project works the
same on any machine. Each skill belongs to **exactly one** service category.
The assignment lives in [`catalog.json`](catalog.json), and
`tests/test_skills_catalog.py` fails if a skill is missing or listed twice.
The website shows each category's skills as "What's included" chips on the
service cards.

**Sources:** *project* = written for this platform · *Rosehill* =
[Claude-Data-Analyst-plugin](https://github.com/danielrosehill/Claude-Data-Analyst-plugin)
v1.2.0 (MIT) · *Anthropic* = Claude Code **data** plugin v1.1.0 (Apache-2.0).
License texts: [`/licenses`](../../licenses).

## By category

### 1. Data Cleaning — *make the data trustworthy*
| Skill | Source | Use it to… |
|---|---|---|
| [`data-cleaning`](data-cleaning/SKILL.md) | project | Remove duplicates/blank rows, report missing values (the platform standard) |
| [`flat-file-intake`](flat-file-intake/SKILL.md) | project | Sort multiple files: same structure, split by year, or different entities |
| [`type-consistency-sweep`](type-consistency-sweep/SKILL.md) | Rosehill | Find mixed types and mismatched key types across files |
| [`anomaly-analysis`](anomaly-analysis/SKILL.md) | Rosehill | Flag outliers and impossible values (negative ages, future dates) |
| [`forensic-sweep`](forensic-sweep/SKILL.md) | Rosehill | Detect data that was already imputed/smoothed before it reached you |
| [`pii-flag`](pii-flag/SKILL.md) | Rosehill | Flag personal information before data is shared or analyzed |

### 2. Data Strategy & Discovery — *decide what to do and whether the data can support it*
| Skill | Source | Use it to… |
|---|---|---|
| [`discovery-call-prep`](discovery-call-prep/SKILL.md) | project | Brief + roadmap draft from a client's intake answers |
| [`sample-size`](sample-size/SKILL.md) | project (adapted from Rosehill) | "Is there enough data to answer this question?" |
| [`data-enrichment`](data-enrichment/SKILL.md) | Rosehill | Find data gaps and external sources that could fill them |

### 3. Data Exploration & Analysis — *answer questions and find trends*
| Skill | Source | Use it to… |
|---|---|---|
| [`explore-data`](explore-data/SKILL.md) | Anthropic | Profile a new dataset in depth |
| [`analyze`](analyze/SKILL.md) | Anthropic | Answer business questions, from quick lookups to full reports |
| [`trend-analysis`](trend-analysis/SKILL.md) | Rosehill | Growth rates, seasonality, segment shifts |
| [`correlation-analysis`](correlation-analysis/SKILL.md) | Rosehill | What moves together (Pearson/Spearman/Kendall) |
| [`statistical-analysis`](statistical-analysis/SKILL.md) | Anthropic | Descriptive stats, outliers, significance |
| [`standard-deviation`](standard-deviation/SKILL.md) | Rosehill | Spread and variability, by group |
| [`hypothesis-testing`](hypothesis-testing/SKILL.md) | Rosehill | Test a specific claim against the data |
| [`write-query`](write-query/SKILL.md) | Anthropic | Turn a question into SQL |
| [`validate-data`](validate-data/SKILL.md) | Anthropic | QA an analysis before it goes to the client |

### 4. Data Visualization — *make it visible*
| Skill | Source | Use it to… |
|---|---|---|
| [`build-dashboard`](build-dashboard/SKILL.md) | Anthropic | Interactive HTML dashboards with KPIs and filters |
| [`create-viz`](create-viz/SKILL.md) | Anthropic | Publication-quality charts in Python |
| [`data-visualization`](data-visualization/SKILL.md) | Anthropic | Chart choice, color, accessibility principles |
| [`data-reporting`](data-reporting/SKILL.md) | Rosehill | Shareable PDF report about a dataset |

### 5. Data Science & Machine Learning — *predict*
| Skill | Source | Use it to… |
|---|---|---|
| [`predictive-modeling`](predictive-modeling/SKILL.md) | project | Forecasts and predictive models with baseline, CV, plain-English results |
| [`multivariate-analysis`](multivariate-analysis/SKILL.md) | Rosehill | Controlled effects, multicollinearity, dimensionality reduction |

### 6. Data Engineering — *structure and document*
| Skill | Source | Use it to… |
|---|---|---|
| [`er-diagram`](er-diagram/SKILL.md) | project | Verified ER diagrams (Mermaid) with key checks |
| [`data-dictionary-creator`](data-dictionary-creator/SKILL.md) | Rosehill | Column-by-column documentation |
| [`sql-queries`](sql-queries/SKILL.md) | Anthropic | Correct, performant SQL across warehouse dialects |

### 7. AI — *ask questions about your data*
| Skill | Source | Use it to… |
|---|---|---|
| [`ask-your-data`](ask-your-data/SKILL.md) | project | Guardrails + procedure for plain-English Q&A (spec for the AI feature) |
| [`setup-data-workspace`](setup-data-workspace/SKILL.md) | Rosehill | Local "talk to your data" workspace (DuckDB) |
| [`data-context-extractor`](data-context-extractor/SKILL.md) | Anthropic | Teach the AI a client's vocabulary and metrics |

## How overlaps were resolved

Some skills could fit two categories. The rule: **put a skill where its main
output is delivered.**

| Skill | Could also be | Placed in | Why |
|---|---|---|---|
| `anomaly-analysis` | Analysis | Cleaning | Impossible values are a data-quality problem to fix before analysis |
| `sample-size` | Analysis | Strategy | It decides *whether* to take on an analysis — a scoping call |
| `write-query` vs `sql-queries` | — | Analysis vs Engineering | `write-query` answers a question; `sql-queries` is dialect-correct, production SQL |
| `validate-data` | Visualization | Analysis | It checks conclusions, whatever format they are delivered in |
| `multivariate-analysis` | Analysis | Data Science & ML | Its main use here is feature selection before modeling |
| `data-context-extractor` | Strategy | AI | Its output is a skill that makes the AI understand a client |
| `setup-data-workspace` | Engineering | AI | Its purpose is "talk to your data" |

## Typical order on a client project

```
Cleaning ─► Strategy & Discovery ─► Exploration & Analysis ─► Visualization
                                  └► Data Science & ML ──────┘
Engineering runs alongside once data needs a permanent home; AI sits on top of clean data.
```

## Updating vendored skills

The Rosehill and Anthropic skills are copies. To pick up upstream changes,
re-copy the folder and re-apply any local edits (keep a note at the top of a
skill when you change it). If you also have the Anthropic data plugin enabled
in Claude Code, its skills appear twice (`data:analyze` and `analyze`); you can
turn off the plugin and use these copies.

## Adding a new skill

1. Create `.claude/skills/<name>/SKILL.md` with front matter (`name` must match the folder):
   ```
   ---
   name: <name>
   description: <what it does AND when to use it>
   ---
   ```
2. Add it to **one** category in `catalog.json` with a client-friendly `label`.
3. Run `pytest -q` — the catalog tests confirm it's placed exactly once.
4. If it's adapted from someone else's work, keep their license notice.
