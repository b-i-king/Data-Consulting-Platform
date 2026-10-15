# Data skills

Each skill is **two files in one folder**:

| File | What it is | Who uses it |
|---|---|---|
| `README.md` | The procedure in plain English: steps, rules, client wording | Claude Code, consultants |
| `skill.py` | The same procedure as runnable Python (pandas, scipy, scikit-learn) | The web app, the API, the command line |

Each procedure is named `README.md` so GitHub shows it as soon as you open the
skill's folder. The procedures keep
the way we clean, analyze, and deliver data **consistent and reproducible**,
whether the work is done by the web app, by you, by Claude, or by a future
consultant on the team.

All 31 skills live in this folder (editable copies), so the project works the
same on any machine. Each skill belongs to **exactly one** service category.
The assignment lives in [`catalog.json`](catalog.json), and
`tests/test_skills_catalog.py` fails if a skill is missing or listed twice, and
`tests/test_data_skills.py` runs every `skill.py` on the sample data.
The website shows each category's skills as "What's included" chips on the
service cards.

**Sources:** *project* = written for this platform · *Rosehill* =
[Claude-Data-Analyst-plugin](https://github.com/danielrosehill/Claude-Data-Analyst-plugin)
v1.2.0 (MIT) · *Anthropic* = Claude Code **data** plugin v1.1.0 (Apache-2.0).
License texts: [`/licenses`](../../licenses).

## Run a skill

```bash
python -m data_skills list                                    # all 30, by category
python -m data_skills describe trend-analysis                 # its options
python -m data_skills run build-dashboard samples/sales.csv   # -> outputs/build-dashboard/
```

- **Web:** after **Clean my data**, scroll to **Explore your data → Run a skill**
  (or the **Dashboard** tab when the data has dates).
- **API:** `GET /api/skills` lists them; `POST /api/skills/{skill_id}/run` runs
  one on uploaded files (form fields `files`, `params` as JSON, optional `group_id`).
- **Python:** `from data_skills.registry import run_skill; run_skill("analyze", df, {})`.

Files are cleaned first (the platform standard) unless the skill works on the
raw upload (`data-cleaning`, `type-consistency-sweep`, `forensic-sweep`,
`discovery-call-prep`). Each `README.md` ends with a generated **Run it** section
listing the skill's options. Try everything on the sample data: see
[`docs/TESTING.md`](../docs/TESTING.md).

### How the code is organized

| File | Purpose |
|---|---|
| `_core.py` | `SkillSpec` (id, options), `SkillResult` (summary, findings, tables, charts, artifacts), shared helpers |
| `_time.py` | Date ranges (last 4 weeks … year to date) and DoD/WoW/MoM math — see [`docs/DASHBOARDS.md`](../docs/DASHBOARDS.md) |
| `registry.py` | Finds and loads each `skill.py`, checks options, runs it |
| `__main__.py` | The command line (`python -m data_skills …`) |
| `<skill>/skill.py` | One skill: `SKILL = SkillSpec(...)` and `run(data, **options) -> SkillResult` |

**Why the folder is here:** skills used to live in `.claude/skills/`. They are
now product code (the web app and API run them), so they live in `data_skills/`,
and each procedure is a `README.md` (GitHub displays it automatically).
Claude Code only auto-loads skill folders whose procedure file is named
`SKILL.md`; to use one as a Claude Code skill in your own session, copy its
folder into `.claude/skills/` and rename `README.md` to `SKILL.md` there (not committed).

**In the web app** a skill is offered only when the client has chosen its
service; Data Cleaning is always included. The others show as locked badges
with a price and an **Add** button.

## By category

### 1. Data Cleaning — *make the data trustworthy*
| Skill | Source | Use it to… |
|---|---|---|
| [`data-cleaning`](data-cleaning/README.md) | project | Remove duplicates/blank rows, report missing values (the platform standard) |
| [`flat-file-intake`](flat-file-intake/README.md) | project | Sort multiple files: same structure, split by year, or different entities |
| [`type-consistency-sweep`](type-consistency-sweep/README.md) | Rosehill | Find mixed types and mismatched key types across files |
| [`anomaly-analysis`](anomaly-analysis/README.md) | Rosehill | Flag outliers and impossible values (negative ages, future dates) |
| [`forensic-sweep`](forensic-sweep/README.md) | Rosehill | Detect data that was already imputed/smoothed before it reached you |
| [`pii-flag`](pii-flag/README.md) | Rosehill | Flag personal information before data is shared or analyzed |

### 2. Data Strategy & Discovery — *decide what to do and whether the data can support it*
| Skill | Source | Use it to… |
|---|---|---|
| [`discovery-call-prep`](discovery-call-prep/README.md) | project | Brief + roadmap draft from a client's intake answers |
| [`sample-size`](sample-size/README.md) | project (adapted from Rosehill) | "Is there enough data to answer this question?" |
| [`data-enrichment`](data-enrichment/README.md) | Rosehill | Find data gaps and external sources that could fill them |

### 3. Data Exploration & Analysis — *answer questions and find trends*
| Skill | Source | Use it to… |
|---|---|---|
| [`explore-data`](explore-data/README.md) | Anthropic | Profile a new dataset in depth |
| [`analyze`](analyze/README.md) | Anthropic | Answer business questions, from quick lookups to full reports |
| [`trend-analysis`](trend-analysis/README.md) | Rosehill | Growth rates, seasonality, segment shifts |
| [`correlation-analysis`](correlation-analysis/README.md) | Rosehill | What moves together (Pearson/Spearman/Kendall) |
| [`statistical-analysis`](statistical-analysis/README.md) | Anthropic | Descriptive stats, outliers, significance |
| [`standard-deviation`](standard-deviation/README.md) | Rosehill | Spread and variability, by group |
| [`hypothesis-testing`](hypothesis-testing/README.md) | Rosehill | Test a specific claim against the data |
| [`write-query`](write-query/README.md) | Anthropic | Turn a question into SQL |
| [`validate-data`](validate-data/README.md) | Anthropic | QA an analysis before it goes to the client |

### 4. Data Visualization — *make it visible*
| Skill | Source | Use it to… |
|---|---|---|
| [`build-dashboard`](build-dashboard/README.md) | Anthropic | Interactive HTML dashboards with KPIs and filters |
| [`create-viz`](create-viz/README.md) | Anthropic | Publication-quality charts in Python |
| [`data-visualization`](data-visualization/README.md) | Anthropic | Chart choice, color, accessibility principles |
| [`data-reporting`](data-reporting/README.md) | Rosehill | Shareable PDF report about a dataset |

### 5. Data Engineering — *structure and document*
| Skill | Source | Use it to… |
|---|---|---|
| [`er-diagram`](er-diagram/README.md) | project | Verified ER diagrams (Mermaid) with key checks |
| [`data-dictionary-creator`](data-dictionary-creator/README.md) | Rosehill | Column-by-column documentation |
| [`sql-queries`](sql-queries/README.md) | Anthropic | Correct, performant SQL across warehouse dialects |
| [`unified-metrics`](unified-metrics/README.md) | Project | Test bench: metric columns → rows (type, subtype, unit, value), round-trip check that nothing is lost |

### 6. Data Science & Machine Learning — *predict*
| Skill | Source | Use it to… |
|---|---|---|
| [`predictive-modeling`](predictive-modeling/README.md) | project | Forecasts and predictive models with baseline, CV, plain-English results |
| [`multivariate-analysis`](multivariate-analysis/README.md) | Rosehill | Controlled effects, multicollinearity, dimensionality reduction |

### 7. AI — *ask questions about your data*
| Skill | Source | Use it to… |
|---|---|---|
| [`ask-your-data`](ask-your-data/README.md) | project | Guardrails + procedure for plain-English Q&A (spec for the AI feature) |
| [`setup-data-workspace`](setup-data-workspace/README.md) | Rosehill | Local "talk to your data" workspace (DuckDB) |
| [`data-context-extractor`](data-context-extractor/README.md) | Anthropic | Teach the AI a client's vocabulary and metrics |

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

1. Create `data_skills/<name>/README.md` with front matter (`name` must match the folder):
   ```
   ---
   name: <name>
   description: <what it does AND when to use it>
   ---
   ```
2. Add `data_skills/<name>/skill.py` with `SKILL = SkillSpec(...)` and
   `run(data, **options) -> SkillResult` (copy a similar skill as a starting point).
3. Add it to **one** category in `catalog.json` with a client-friendly `label`
   (each category marks exactly one skill `"primary": true` — the one its workspace
   page opens on).
4. Add a case to `CASES` in `tests/test_data_skills.py` that runs it on the sample data.
5. Run `python scripts/sync_skill_docs.py` to add its "Run it" section to README.md.
6. Run `pytest -q` — the tests confirm it's placed exactly once, has a
   `skill.py`, runs, and its docs match the code.
7. If it's adapted from someone else's work, keep their license notice.
