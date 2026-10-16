"""
Dashboards built around the BUSINESS: context, suggested questions, saved tabs.

WHY THIS EXISTS
---------------
A dashboard is only useful when it answers the questions THIS business asks.
So the dashboard has layers:

  1. OVERVIEW — built from the data automatically (data_skills/build-dashboard):
     totals over time, comparisons, filters. Every client gets it.
  2. BUSINESS CONTEXT — "About your business" on the Account page (what they do,
     their goals, the questions they ask most) plus their industry (NAICS,
     from sign-up). Shown on the dashboard, and used to SUGGEST questions.
  3. THEIR OWN TABS — each tab is a list of saved plain-English questions
     ("total amount by store", "top 5 product by amount"), answered again on
     the current data every time the tab opens (the ask-your-data skill's
     safe parser). Tabs grow as the client asks more: questions asked again
     and again (dim.question_log) are suggested as new tabs.

Suggestions come from three places, best first: the client's own key
questions, their most-asked questions, then questions written for their
INDUSTRY using the columns their table actually has.

Pure rules here (no database, no HTTP): storage is account_store
(dim.dashboard_tabs, dim.question_log, accounts.business_context); endpoints
are api/routers/dashboards.py.
"""

from __future__ import annotations

import re

from api.services.intake import naics_tree

MAX_TABS = 10
MAX_QUESTIONS = 12
MAX_QUESTION_LENGTH = 200

# Column-name hints for the roles a question needs (lowercase, matched as words).
ROLE_HINTS = {
    "metric": ["amount", "revenue", "sales", "total", "price", "cost", "spend", "profit", "value", "fee", "income"],
    "product": ["product", "item", "service", "sku", "menu", "category", "plan"],
    "place": ["store", "location", "branch", "city", "region", "state", "site", "office", "warehouse"],
    "customer": ["customer_type", "segment", "customer", "client", "patient", "member", "tier", "age_group"],
    "channel": ["payment_method", "channel", "source", "order_type", "campaign", "medium"],
}

# Per industry (NAICS sector code): the business questions worth a dashboard,
# written as (why it matters, question template). {metric}/{product}/... are
# filled with the client's own column names; a template whose columns aren't
# in the table is skipped.
INDUSTRY_QUESTIONS: dict[str, list[tuple[str, str]]] = {
    "44-45": [("Best sellers", "top 5 {product} by {metric}"), ("Which store earns most", "total {metric} by {place}"),
              ("How customers pay", "how many rows by {channel}"), ("Average sale by customer type", "average {metric} by {customer}")],
    "72": [("Top menu items", "top 5 {product} by {metric}"), ("Sales by location", "total {metric} by {place}"),
           ("Dine-in vs. takeout", "total {metric} by {channel}"), ("Average check by guest type", "average {metric} by {customer}")],
    "54": [("Revenue by service", "total {metric} by {product}"), ("Top clients", "top 5 {customer} by {metric}"),
           ("Revenue by office", "total {metric} by {place}")],
    "62": [("Visits by service", "how many rows by {product}"), ("Revenue by location", "total {metric} by {place}"),
           ("Patients by type", "how many rows by {customer}")],
    "31-33": [("Output value by product", "total {metric} by {product}"), ("Cost by site", "total {metric} by {place}")],
    "42": [("Top products", "top 5 {product} by {metric}"), ("Top customers", "top 5 {customer} by {metric}"),
           ("Sales by region", "total {metric} by {place}")],
    "23": [("Spend by project type", "total {metric} by {product}"), ("Spend by site", "total {metric} by {place}")],
    "81": [("Revenue by service", "total {metric} by {product}"), ("Customers by type", "how many rows by {customer}")],
}
GENERIC_QUESTIONS = [("Biggest contributors", "top 5 {product} by {metric}"), ("By location", "total {metric} by {place}"),
                     ("By customer type", "average {metric} by {customer}"), ("By channel", "how many rows by {channel}")]


def industry_label(sector: str | None) -> str | None:
    """The NAICS sector's name ("44-45" -> "Retail Trade"), or None."""
    if not sector:
        return None
    return next((s["name"] for s in naics_tree()["sectors"] if s["code"] == sector), None)


def clean_questions(questions: list[str]) -> list[str]:
    """
    Tidy a list of questions: trim, squeeze spaces, drop blanks and repeats, cap length and count.

    Example:
        >>> clean_questions(["  total amount by store ", "TOTAL amount by store", ""])
        ['total amount by store']
    """
    out, seen = [], set()
    for q in questions:
        q = " ".join(str(q).split())[:MAX_QUESTION_LENGTH]
        if q and q.lower() not in seen:
            seen.add(q.lower())
            out.append(q)
    return out[:MAX_QUESTIONS]


def clean_context(about: str, goals: str, key_questions: list[str]) -> dict:
    """The "About your business" fields, tidied for storage."""
    return {"about": " ".join(about.split())[:1000], "goals": " ".join(goals.split())[:1000],
            "key_questions": clean_questions(key_questions)}


def _find(role: str, columns: list[dict], used: set[str]) -> str | None:
    """The first column matching a role's hints (metrics must be numeric, the rest text)."""
    want_number = role == "metric"
    for hint in ROLE_HINTS[role]:
        for c in columns:
            name = c["name"]
            numeric = c.get("type") in ("numeric", "integer", "float", "number")
            if name in used or numeric != want_number:
                continue
            if re.search(rf"(^|_|\b){re.escape(hint)}", name.lower()):
                return name
    return None


def suggest(columns: list[dict], sector: str | None, context: dict, frequent: list[dict], limit: int = 8) -> list[dict]:
    """
    Suggest dashboard questions for THIS business and THIS table.

    Args:
        columns: list[dict], the table's columns: [{"name": "amount", "type": "numeric"}, ...]
            (types from the cleaning report: numeric / text / date ...).
        sector: str | None, the NAICS sector code from sign-up ("44-45").
        context: dict, the business context ({"key_questions": [...]}).
        frequent: list[dict], the most-asked questions [{"question", "times"}].
        limit: int, how many to return.

    Returns:
        list[dict]: [{"question", "why", "source": "yours" | "asked" | "industry"}], no repeats.

    Example:
        >>> suggest([{"name": "amount", "type": "numeric"}, {"name": "store", "type": "text"}], "44-45", {}, [])[0]
        {'question': 'total amount by store', 'why': 'Which store earns most', 'source': 'industry'}
    """
    out: list[dict] = []
    for q in context.get("key_questions", []):
        out.append({"question": q, "why": "One of your key questions", "source": "yours"})
    for f in frequent:
        if f.get("times", 0) >= 2:
            out.append({"question": f["question"], "why": f"You've asked this {f['times']} times", "source": "asked"})
    roles: dict[str, str] = {}
    used: set[str] = set()
    for role in ROLE_HINTS:
        found = _find(role, columns, used)
        if found:
            roles[role] = found
            used.add(found)
    for why, template in INDUSTRY_QUESTIONS.get(sector or "", []) + GENERIC_QUESTIONS:
        needed = re.findall(r"{(\w+)}", template)
        if all(r in roles for r in needed):
            out.append({"question": template.format(**roles), "why": why, "source": "industry"})
    seen, unique = set(), []
    for s in out:
        key = s["question"].lower()
        if key not in seen:
            seen.add(key)
            unique.append(s)
    return unique[:limit]
