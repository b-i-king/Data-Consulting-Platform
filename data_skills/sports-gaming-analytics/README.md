---
name: sports-gaming-analytics
description: Sports and gaming analytics from the client's own box scores, player stats or game sessions — leaders by stat for chosen teams and positions, efficiency (per-36 minutes, true shooting %, eliminations per respawn, win rate, score per minute), game-to-game consistency, team totals and a player's season trend.
---

# Sports & gaming analytics

For teams, leagues, coaches, academies, game studios and esports organizers.
Built on the owner's NBA Player Stats Explorer
([b-i-king/NBA_Analysis](https://github.com/b-i-king/NBA_Analysis): scoring,
assist and rebound leaders of selected teams; a player's averages by year),
widened to any sport and to gaming. It runs on the **client's own table** —
nothing is scraped from other sites.

## Inputs

- One table: one row per player-game, per player-season (like
  Basketball-Reference's per-game table), or per gaming session / match.
- Found by name (or chosen): the **player**, **team**, **position**,
  **season / year** and game columns, and the **stat** columns —
  Basketball-Reference names work (`Player`, `Tm`, `Pos`, `MP`, `PTS`, `TRB`,
  `AST`, `STL`, `BLK`, `FGA`, `FTA`), as do plain ones (`points`, `eliminations`,
  `respawns`, `wins`, `score`, `playtime`). Columns named `kills` / `deaths`
  are recognized too, but results always say **eliminations** and **respawns**
  (family-friendly words, whoever reads the report).
- Optional: only some **teams** (`BOS, LAL`) or **positions** (`PG,SG`), the
  **player** for the season trend, how many leaders (`top`, default 5).

## Procedure

### 1. Recognize the data
Match columns by name; the domain is **sports** (points / rebounds / assists …)
or **gaming** (eliminations / respawns / wins / playtime, with no rebounds or assists).

### 2. Leaders
Each player's **average** of every key stat (per game, or the average of
per-season rows), the top N per stat — the original app's Scoring / Assist /
Rebound Leaders. Respawns aren't a "leader" stat (fewer is better; they're in eliminations per respawn).

### 3. Efficiency (when the columns allow it)
| Domain | Measure | Formula |
|---|---|---|
| Sports | per 36 minutes | total stat ÷ total minutes × 36 (points, rebounds, assists) |
| Sports | true shooting % | PTS ÷ (2 × (FGA + 0.44 × FTA)) × 100 |
| Gaming | eliminations per respawn | eliminations ÷ respawns |
| Gaming | win rate | wins ÷ matches (or sessions) × 100 |
| Gaming | score (or eliminations) per minute | total ÷ total minutes played |

### 4. Consistency
For the leaders in the main stat (points, else score, else eliminations), with 3+
rows each: average, standard deviation, and **steady** (coefficient of
variation under 0.3), **about average** (under 0.5) or **streaky**.

### 5. Team totals and season trend
Team totals of the main stat; the chosen player's (default: the top scorer's)
averages per season — the original Annual Player Statistics line chart.

## Notes
- Averages treat every row equally: a per-season table averages seasons, a
  per-game table averages games.
- Basketball-Reference's tables repeat their header rows and mark traded
  players as `TOT` / `2TM`: Data Cleaning removes the repeated headers; filter
  `TOT` rows out (or keep only them) before comparing teams.

## Related skills

- `trend-analysis` — growth over time for any number.
- `statistical-analysis` — distributions and comparisons.
- `predictive-modeling` — e.g. win probability from team stats.

## Run it (platform implementation)

This procedure is also runnable code: [`skill.py`](skill.py) in this folder (category: **Data Exploration & Analysis**).

```bash
python -m data_skills run sports-gaming-analytics samples/basketball_per_game.csv --param KEY=VALUE
```

Web: **Explore your data → Run a skill → Sports & gaming analytics** (unlocked when the client chooses **Data Exploration & Analysis**) · API: `POST /api/skills/sports-gaming-analytics/run`.
Input: one table.

| Option | Type | Default | What it does |
|---|---|---|---|
| `player_column` | column | auto | Player / gamer (auto) |
| `team_column` | column | auto | Team (auto) |
| `teams` | str | auto | Only these teams, comma-separated (default: all) |
| `positions` | str | auto | Only these positions, comma-separated, e.g. PG,SG (default: all) |
| `player` | str | auto | Player for the season trend (default: the top scorer) |
| `top` | int | 5 | How many leaders per stat |
