# Skills used on this project

Skills are instruction files Claude Code loads when a task matches. They keep
the way we clean, categorize, and assess data **consistent and reproducible**,
whether the work is done by the web app, by you, by Claude, or by a future
consultant on the team.

## Project skills (in this folder)

| Skill | What it covers | Code it documents |
|---|---|---|
| [`flat-file-intake`](flat-file-intake/SKILL.md) | One file vs. many; same structure split by year vs. different kinds of data; join keys | `api/services/categorizer.py` |
| [`data-cleaning`](data-cleaning/SKILL.md) | The platform's cleaning standard: duplicates, blanks, missing-value report | `api/services/cleaning.py` |
| [`sample-size`](sample-size/SKILL.md) | "Is there enough data to answer this question?" — adapted from Daniel Rosehill's plugin (MIT) | `api/services/sample_size.py` |

**Rule:** a skill and the code it documents must change together. If you edit
`cleaning.py`, update `data-cleaning/SKILL.md` and `docs/DATA_CLEANING.md` in
the same commit.

## Anthropic "data" plugin (v1.1.0)

These come from the Claude Code **data** plugin (by Anthropic) and are enabled
in your Claude account, not stored in this repo. Use them alongside the project
skills:

| Skill | Use it on this project for… |
|---|---|
| `data:explore-data` | Deeper profiling of a client file than the automatic report |
| `data:analyze` | Answering a client's business questions (Data Exploration & Analysis service) |
| `data:statistical-analysis` | Trends, outliers, hypothesis tests — after `sample-size` says the data is adequate |
| `data:validate-data` | QA an analysis before sending it to a client |
| `data:create-viz` / `data:data-visualization` | Charts for reports (Python: matplotlib/seaborn/plotly) |
| `data:build-dashboard` | Prototype dashboards for the Data Visualization service |
| `data:sql-queries` / `data:write-query` | SQL for Supabase/Postgres, Snowflake, BigQuery when connectors arrive |
| `data:data-context-extractor` | Build a per-client skill capturing their terminology and metrics |

To invoke one in Claude Code, type its name with a slash, e.g. `/data:explore-data sales.csv`.

## Typical order on a client project

```
flat-file-intake ─► data-cleaning ─► sample-size ─► data:explore-data
                                                  └► data:analyze / data:statistical-analysis
                                                     └► data:create-viz / data:build-dashboard
                                                        └► data:validate-data ─► deliver
```

## Adding a new skill

1. Create `.claude/skills/<name>/SKILL.md` with front matter:
   ```
   ---
   name: <name>
   description: <one or two sentences: what it does AND when to use it>
   ---
   ```
2. Write the procedure as numbered steps a new consultant could follow.
3. If it documents code, link the file and add a test.
4. If it is adapted from someone else's work, keep their license notice in the folder.
