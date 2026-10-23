"""
prompt_library — sample Beacon questions by industry (the AI page's "Sample questions").

Beacon answers plain-English questions in a few shapes it can always do
safely (data_skills/ask-your-data): "total X by Y", "average X by Y",
"top 5 Y by X", "how many rows by Y", "max X". This library gives each
industry a short list of those questions, written with ROLES ({metric},
{product}, {place}, {customer}, {channel}, {player}, {team}, {game}) that are
filled with the open table's own column names (dashboards.ROLE_HINTS, plus the
sports / gaming roles below). A question whose columns aren't in the table is
still shown, with a plain stand-in word ("amount", "product") and
ready = False, so the client can edit it before asking.

Industries: the NAICS sectors from sign-up (the same codes as
dashboards.INDUSTRY_QUESTIONS), plus "sports" and "gaming" (sports teams and
leagues, game studios and esports — the platform's sports & gaming analytics).
The client's own industry is picked by default; any other can be browsed.
"""

from __future__ import annotations

import re

from api.services.dashboards import ROLE_HINTS, industry_label

# Extra roles for sports and gaming tables (column-name hints, matched as words).
EXTRA_ROLES = {
    "player": ["player", "athlete", "gamer", "username", "user_id", "user", "name"],
    "team": ["team", "club", "squad", "opponent", "franchise"],
    "game": ["game", "title", "match", "map", "mode", "season", "event"],
    "stat": ["points", "pts", "score", "goals", "eliminations", "elims", "kills", "wins", "rebounds", "reb", "assists", "ast",
             "minutes", "playtime", "level", "xp", "revenue", "spend"],
}
ALL_ROLES = {**ROLE_HINTS, **EXTRA_ROLES}
# The word shown when a table has no column for a role ("top 5 product by amount").
STAND_IN = {"metric": "amount", "product": "product", "place": "location", "customer": "customer_type",
            "channel": "channel", "player": "player", "team": "team", "game": "game", "stat": "points"}

# (why it matters, question). Order = most useful first.
LIBRARY: dict[str, list[tuple[str, str]]] = {
    "44-45": [("Best sellers", "top 5 {product} by {metric}"), ("Which store earns most", "total {metric} by {place}"),
              ("Average sale by customer type", "average {metric} by {customer}"),
              ("How customers pay", "how many rows by {channel}"), ("Biggest single sale", "max {metric}")],
    "72": [("Top menu items", "top 5 {product} by {metric}"), ("Sales by location", "total {metric} by {place}"),
           ("Dine-in vs. takeout", "total {metric} by {channel}"),
           ("Average check by guest type", "average {metric} by {customer}"), ("Orders by item", "how many rows by {product}")],
    "54": [("Revenue by service", "total {metric} by {product}"), ("Top clients", "top 5 {customer} by {metric}"),
           ("Revenue by office", "total {metric} by {place}"), ("Average project value", "average {metric} by {product}")],
    "62": [("Visits by service", "how many rows by {product}"), ("Revenue by location", "total {metric} by {place}"),
           ("Patients by type", "how many rows by {customer}"), ("Average charge by service", "average {metric} by {product}")],
    "31-33": [("Output value by product", "total {metric} by {product}"), ("Cost by site", "total {metric} by {place}"),
              ("Highest-cost products", "top 5 {product} by {metric}")],
    "42": [("Top products", "top 5 {product} by {metric}"), ("Top customers", "top 5 {customer} by {metric}"),
           ("Sales by region", "total {metric} by {place}"), ("Orders by channel", "how many rows by {channel}")],
    "23": [("Spend by project type", "total {metric} by {product}"), ("Spend by site", "total {metric} by {place}"),
           ("Largest jobs", "top 5 {product} by {metric}")],
    "48-49": [("Revenue by route or region", "total {metric} by {place}"), ("Loads by customer", "how many rows by {customer}"),
              ("Average revenue per load", "average {metric} by {place}")],
    "52": [("Balances by product", "total {metric} by {product}"), ("Top clients", "top 5 {customer} by {metric}"),
           ("Accounts by branch", "how many rows by {place}")],
    "53": [("Rent by property", "total {metric} by {place}"), ("Average rent by unit type", "average {metric} by {product}"),
           ("Top tenants", "top 5 {customer} by {metric}")],
    "61": [("Enrollment by program", "how many rows by {product}"), ("Revenue by program", "total {metric} by {product}"),
           ("Students by campus", "how many rows by {place}")],
    "71": [("Attendance by event", "how many rows by {product}"), ("Revenue by venue", "total {metric} by {place}"),
           ("Top events", "top 5 {product} by {metric}")],
    "81": [("Revenue by service", "total {metric} by {product}"), ("Customers by type", "how many rows by {customer}"),
           ("Average ticket", "average {metric} by {product}")],
    "sports": [("Top scorers", "top 5 {player} by {stat}"), ("Team totals", "total {stat} by {team}"),
               ("Average per game", "average {stat} by {player}"), ("Games played", "how many rows by {player}"),
               ("Best single game", "max {stat}"), ("Results by opponent", "average {stat} by {team}")],
    "gaming": [("Top players", "top 5 {player} by {stat}"), ("Playtime by game", "total {stat} by {game}"),
               ("Average per session", "average {stat} by {player}"), ("Sessions by game mode", "how many rows by {game}"),
               ("Spend by player type", "total {metric} by {customer}"), ("Best session", "max {stat}")],
}
GENERIC = [("Biggest contributors", "top 5 {product} by {metric}"), ("By location", "total {metric} by {place}"),
           ("By customer type", "average {metric} by {customer}"), ("By channel", "how many rows by {channel}"),
           ("Largest value", "max {metric}")]
EXTRA_LABELS = {"sports": "Sports (teams, leagues, athletes)", "gaming": "Gaming (studios, esports, players)"}


NUMERIC_TYPES = ("numeric", "integer", "float", "number")


def _roles(columns: list[dict]) -> dict[str, str]:
    """
    Which of the table's columns plays each role, each column used once.

    A role's hints are tried in order; "metric" and "stat" need a number column,
    the others a text column. Example: {"player": "player_name", "stat": "pts"}.
    """
    found, used = {}, set()
    for role, hints in ALL_ROLES.items():
        want_number = role in ("metric", "stat")
        for hint in hints:
            col = next((c["name"] for c in columns
                        if c["name"] not in used and (c.get("type") in NUMERIC_TYPES) == want_number
                        and re.search(rf"(^|_|){re.escape(hint)}", c["name"].lower())), None)
            if col:
                found[role] = col
                used.add(col)
                break
    return found


def industries() -> list[dict]:
    """Every industry with sample questions: [{"key", "label"}], NAICS sectors first."""
    out = [{"key": k, "label": industry_label(k) or k} for k in LIBRARY if k not in EXTRA_LABELS]
    return out + [{"key": k, "label": v} for k, v in EXTRA_LABELS.items()]


def questions_for(industry: str | None, columns: list[dict]) -> list[dict]:
    """
    The sample questions for one industry, filled with this table's columns.

    Args:
        industry: str | None, a key from industries() (None or unknown = the general list).
        columns: list[dict], the open table's columns [{"name", "type"}].

    Returns:
        list[dict]: [{"question", "why", "ready"}] — ready = every column it needs is in the table.

    Example:
        >>> questions_for("sports", [{"name": "player", "type": "text"}, {"name": "pts", "type": "numeric"}])[0]
        {'question': 'top 5 player by pts', 'why': 'Top scorers', 'ready': True}
    """
    roles = _roles(columns)
    out, seen = [], set()
    for why, template in LIBRARY.get(industry or "", GENERIC):
        needed = re.findall(r"{(\w+)}", template)
        filled = {r: roles.get(r, STAND_IN[r]) for r in needed}
        q = template.format(**filled)
        if q.lower() not in seen:
            seen.add(q.lower())
            out.append({"question": q, "why": why, "ready": all(r in roles for r in needed)})
    return out
