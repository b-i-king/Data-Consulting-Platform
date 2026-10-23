"""
sports-gaming-analytics — leaders, efficiency, consistency and season trends (Data Exploration & Analysis).

Python implementation of README.md in this folder. Built on the owner's NBA
Player Stats Explorer (github.com/b-i-king/NBA_Analysis: scoring / assist /
rebound leaders of selected teams, and a player's averages by year), widened to
any sport and to gaming. It works on the CLIENT's own table (box scores,
per-game stats, match or session logs) — nothing is scraped.

Steps:
  1. Recognize the data — find the player, team, position, season and game
     columns, and the stat columns, by name (Basketball-Reference style names
     like PTS / AST / TRB / MP / Tm work, as do points / assists / eliminations ...).

     FAMILY-FRIENDLY WORDS: whatever the columns are called, results say
     "eliminations" (not kills) and "respawns" (not deaths) — columns named
     kills / deaths are still recognized, just never echoed back.
     The domain is "sports" or "gaming" from the stats present.
  2. Leaders — each player's AVERAGE of every key stat (one row per game or
     per season averages the same way), the top N per stat, for the teams and
     positions chosen (like the original Scoring / Assist / Rebound Leaders).
  3. Efficiency, when the columns allow it:
       sports  per-36-minute points / rebounds / assists (needs minutes, MP),
               true shooting % = PTS / (2 x (FGA + 0.44 x FTA))
       gaming  eliminations per respawn = eliminations / respawns,
               win rate = wins / matches, score (or eliminations) per minute
  4. Consistency — each leader's game-to-game spread (standard deviation and
     coefficient of variation): steady vs. streaky.
  5. Trends — the chosen player's (default: the top scorer's) averages per
     season / year (like the original Annual Player Statistics chart), and
     team totals.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from data_skills._core import (
    Param, SkillInputError, SkillResult, SkillSpec, bar_chart, fmt_num, line_chart, records, require_column,
)

SKILL = SkillSpec(
    id="sports-gaming-analytics",
    title="Sports & gaming analytics",
    description="Leaders by stat, efficiency (per-36, true shooting %, eliminations per respawn, win rate), "
                "consistency, and a player's "
                "season trend — from your box scores, player stats or game sessions.",
    params={
        "player_column": Param("column", None, "Player / gamer (auto)"),
        "team_column": Param("column", None, "Team (auto)"),
        "teams": Param("str", None, "Only these teams, comma-separated (default: all)"),
        "positions": Param("str", None, "Only these positions, comma-separated, e.g. PG,SG (default: all)"),
        "player": Param("str", None, "Player for the season trend (default: the top scorer)"),
        "top": Param("int", 5, "How many leaders per stat"),
    },
)

# Column-name hints (lowercase, matched as whole words / underscore parts).
ROLES = {
    "player": ["player", "player_name", "athlete", "gamer", "username", "name"],
    "team": ["tm", "team", "club", "squad", "franchise"],
    "position": ["pos", "position", "role"],
    "season": ["season", "year", "yr"],
    "game": ["game_id", "match_id", "game", "match", "date", "game_date", "session_id"],
}
# Key stats per domain: canonical name -> column-name hints, and a friendly label.
SPORTS_STATS = {"points": (["pts", "points", "goals", "runs"], "Points"),
                "rebounds": (["trb", "reb", "rebounds", "total_rebounds"], "Rebounds"),
                "assists": (["ast", "assists"], "Assists"),
                "steals": (["stl", "steals"], "Steals"), "blocks": (["blk", "blocks"], "Blocks")}
# Gaming results always use family-friendly words (eliminations, respawns), even when the
# client's columns say kills / deaths.
GAMING_STATS = {"eliminations": (["eliminations", "elims", "takedowns", "knockouts", "kills"], "Eliminations"),
                "respawns": (["respawns", "deaths", "knocked_out", "times_eliminated"], "Respawns"),
                "score": (["score", "points"], "Score"), "wins": (["wins", "win", "victories"], "Wins"),
                "playtime": (["playtime", "minutes_played", "time_played", "duration"], "Minutes played")}
EXTRA = {"minutes": ["mp", "min", "minutes"], "fga": ["fga"], "fta": ["fta"], "matches": ["matches", "games_played", "g"]}


def _norm(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")


def _find(df: pd.DataFrame, hints: list[str], numeric: bool | None, used: set[str]) -> str | None:
    """The first column whose (normalized) name equals a hint, then one that contains it as a word part."""
    def is_number(c) -> bool:          # mostly numbers (text like "27.5" counts; names don't)
        return pd.to_numeric(df[c], errors="coerce").notna().mean() > 0.5

    cols = [c for c in df.columns if c not in used and (numeric is None or is_number(c) == numeric)]
    for h in hints:
        for c in cols:
            if _norm(c) == h:
                return c
    for h in hints:
        for c in cols:
            if h in _norm(c).split("_"):
                return c
    return None


def _numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _pick_list(raw) -> list[str]:
    return [p.strip() for p in str(raw).split(",") if p.strip()] if raw else []


def run(df, player_column=None, team_column=None, teams=None, positions=None, player=None, top=5) -> SkillResult:
    """
    Sports & gaming analytics on a player-stats / box-score / session table.

    Args:
        df: pd.DataFrame, one row per player-game, player-season or session.
        player_column, team_column: str | None, the player and team columns (auto).
        teams: str | None, only these teams ("BOS, LAL").
        positions: str | None, only these positions ("PG,SG").
        player: str | None, the player for the season trend (default: the top scorer).
        top: int, leaders per stat (default 5).

    Returns:
        SkillResult: summary; findings (the leader in each stat, efficiency and
        consistency notes); tables "leaders" (one per stat: rank, player, average),
        "efficiency", "consistency", "team_totals", "trend"; charts (top scorers,
        the trend); data {"domain", "columns", "stats"}.

    Raises:
        SkillInputError: no player column, or no stat columns.

    Example:
        >>> res = run(nba_per_game, teams="BOS")
        >>> res.tables["leaders"][0]["player"]
        'Jayson Tatum'
    """
    used: set[str] = set()
    cols: dict[str, str | None] = {}
    cols["player"] = require_column(df, player_column, "player", [c for c in df.columns]) if player_column \
        else _find(df, ROLES["player"], False, used)
    if not cols["player"]:
        raise SkillInputError("No player column found — choose the column with the player (or gamer) names.")
    used.add(cols["player"])
    cols["team"] = team_column if team_column in df.columns else _find(df, ROLES["team"], False, used)
    for role in ("position", "season", "game"):
        cols[role] = _find(df, ROLES[role], None, used | {c for c in cols.values() if c})
    used |= {c for c in cols.values() if c}

    stats: dict[str, str] = {}
    labels: dict[str, str] = {}
    for table in (SPORTS_STATS, GAMING_STATS):
        for key, (hints, label) in table.items():
            if key in stats or (key == "score" and "points" in stats):
                continue
            c = _find(df, hints, True, used)
            if c:
                stats[key], labels[key] = c, label
                used.add(c)
    extra = {k: _find(df, h, True, used) for k, h in EXTRA.items()}
    if not stats:
        raise SkillInputError("No stat columns found (points, rebounds, assists, eliminations, score, wins …).")
    gaming_keys = {"eliminations", "respawns", "wins", "playtime"}
    domain = "gaming" if gaming_keys & set(stats) and not {"rebounds", "assists"} & set(stats) else "sports"

    data = df.copy()
    data[cols["player"]] = data[cols["player"]].astype(str).str.strip()
    for c in [*stats.values(), *[c for c in extra.values() if c]]:
        data[c] = _numeric(data[c])
    filters = []
    if teams and cols["team"]:
        keep = {t.lower() for t in _pick_list(teams)}
        data = data[data[cols["team"]].astype(str).str.lower().isin(keep)]
        filters.append(f"teams {teams}")
    if positions and cols["position"]:
        keep = {p.upper() for p in _pick_list(positions)}
        data = data[data[cols["position"]].astype(str).str.upper().str.split("-").map(lambda ps: bool(set(ps) & keep))]
        filters.append(f"positions {positions}")
    if data.empty:
        raise SkillInputError("No rows left after the team / position filter.")

    # 2. Leaders: each player's average per stat (the original app's Points / Assist / Rebound Average).
    per_player = data.groupby(cols["player"])[list(stats.values())].mean()
    top = max(1, min(int(top), 25))
    leaders, findings = [], []
    for key, col in stats.items():
        if key == "respawns":
            continue                                  # fewer is better: shown in eliminations per respawn instead
        best = per_player[col].dropna().sort_values(ascending=False).head(top)
        for rank, (name, value) in enumerate(best.items(), 1):
            leaders.append({"stat": labels[key], "rank": rank, "player": name, "average": round(float(value), 2)})
        if not best.empty:
            findings.append(f"{labels[key]} leader: {best.index[0]} ({fmt_num(best.iloc[0], 1)} per game).")

    # 3. Efficiency.
    eff = pd.DataFrame(index=per_player.index)
    sums = data.groupby(cols["player"]).sum(numeric_only=True)
    if domain == "sports":
        if extra["minutes"]:
            mins = sums[extra["minutes"]].replace(0, np.nan)
            for key in ("points", "rebounds", "assists"):
                if key in stats:
                    eff[f"{key}_per_36"] = (sums[stats[key]] / mins * 36).round(1)
        if "points" in stats and extra["fga"] and extra["fta"]:
            attempts = 2 * (sums[extra["fga"]] + 0.44 * sums[extra["fta"]])
            eff["true_shooting_pct"] = (sums[stats["points"]] / attempts.replace(0, np.nan) * 100).round(1)
    else:
        if "eliminations" in stats and "respawns" in stats:
            eff["eliminations_per_respawn"] = (sums[stats["eliminations"]]
                                               / sums[stats["respawns"]].replace(0, np.nan)).round(2)
        if "wins" in stats:
            games = sums[extra["matches"]] if extra["matches"] else data.groupby(cols["player"]).size()
            eff["win_rate_pct"] = (sums[stats["wins"]] / games.replace(0, np.nan) * 100).round(1)
        per_key = "score" if "score" in stats else "eliminations" if "eliminations" in stats else None
        if per_key and "playtime" in stats:
            eff[f"{per_key}_per_minute"] = (sums[stats[per_key]] / sums[stats["playtime"]].replace(0, np.nan)).round(2)
    eff = eff.dropna(how="all")
    efficiency = records(eff.reset_index().rename(columns={cols["player"]: "player"}), limit=200) if not eff.empty else []
    for col in eff.columns:
        best = eff[col].dropna()
        if not best.empty and (domain == "gaming" or col == "true_shooting_pct"):
            findings.append(f"Best {col.replace('_', ' ').replace('pct', '%')}: "
                            f"{best.idxmax()} ({fmt_num(best.max(), 2)}).")

    # 4. Consistency of the main stat (only with several rows per player).
    main_key = next(k for k in ("points", "score", "eliminations", *stats) if k in stats)
    main_col = stats[main_key]
    spread = data.groupby(cols["player"])[main_col].agg(["mean", "std", "count"])
    spread = spread[(spread["count"] >= 3) & (spread["mean"] > 0)]
    consistency = []
    if not spread.empty:
        spread["cv"] = spread["std"] / spread["mean"]
        leaders_main = per_player[main_col].sort_values(ascending=False).head(top).index
        for name in [n for n in leaders_main if n in spread.index]:
            r = spread.loc[name]
            consistency.append({"player": name, "average": round(float(r["mean"]), 2), "std_dev": round(float(r["std"]), 2),
                                "games": int(r["count"]), "steadiness": "steady" if r["cv"] < 0.3 else
                                "about average" if r["cv"] < 0.5 else "streaky"})
        if consistency:
            steady = min(consistency, key=lambda x: x["std_dev"] / max(x["average"], 1e-9))
            findings.append(f"Most consistent top {labels[main_key].lower()} producer: {steady['player']} "
                            f"(±{fmt_num(steady['std_dev'], 1)} around {fmt_num(steady['average'], 1)}).")

    # 5. Team totals and the season trend.
    team_totals = []
    if cols["team"]:
        tt = data.groupby(cols["team"])[main_col].sum().sort_values(ascending=False)
        team_totals = [{"team": t, f"total_{main_key}": round(float(v), 1)} for t, v in tt.items()]
    trend, charts = [], []
    top_main = per_player[main_col].sort_values(ascending=False).head(10)
    charts.append(bar_chart(f"Top {labels[main_key].lower()} (average)", list(top_main.index),
                            [round(float(v), 2) for v in top_main.values], labels[main_key].lower()))
    who = player or (top_main.index[0] if not top_main.empty else None)
    if cols["season"] and who:
        mine = data[data[cols["player"]].str.lower() == str(who).lower()]
        if mine.empty:
            raise SkillInputError(f"No rows for player '{who}'.")
        show = [k for k in ("points", "assists", "rebounds", "score", "eliminations") if k in stats][:3]
        by_season = mine.groupby(mine[cols["season"]].astype(str))[[stats[k] for k in show]].mean().sort_index()
        trend = [{"season": s, **{labels[k].lower(): round(float(row[stats[k]]), 2) for k in show}}
                 for s, row in by_season.iterrows()]
        if len(trend) >= 2:
            charts.append(line_chart(f"{who}: averages by season", list(by_season.index),
                                     {labels[k]: [round(float(v), 2) for v in by_season[stats[k]]] for k in show},
                                     "season", "per game"))

    n_players = per_player.shape[0]
    summary = (f"{'Sports' if domain == 'sports' else 'Gaming'} stats for {n_players:,} player"
               f"{'s' if n_players != 1 else ''}" + (f" ({'; '.join(filters)})" if filters else "") +
               f": leaders in {len({row['stat'] for row in leaders})} stat(s)"
               + (", efficiency" if efficiency else "") + (", season trend" if trend else "") + ".")
    return SkillResult(
        skill=SKILL.id, summary=summary, findings=findings,
        tables={"leaders": leaders, "efficiency": efficiency, "consistency": consistency,
                "team_totals": team_totals, "trend": trend},
        charts=charts,
        data={"domain": domain, "columns": {k: v for k, v in cols.items() if v},
              "stats": {labels[k]: c for k, c in stats.items()}, "trend_player": who if trend else None},
    )
