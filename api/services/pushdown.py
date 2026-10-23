"""
pushdown — do the math IN the client's data source, and bring back only the answer.

Pulling 20,000 raw rows to our server to add them up is slow, uses our
memory, and on warehouses like BigQuery or Snowflake can cost the client
money for every byte read. Asking THEIR source for the totals ("revenue by
month by store") returns a few hundred rows instead: faster, cheaper for
everyone, and the client's data never has to leave in bulk.

A summary request is a small, fixed shape — never free-form SQL typed by
anyone:

    Summary(table="public.orders", agg="sum", metric="amount",
            group_by=["store"], date_column="order_date", period="month", limit=500)

`validate` checks every name against the source's REAL column list (from its
catalog) and the kind of each column (numbers for sum / avg, dates for a
period); `build_sql` / `build_soql` then write the query for that source's
language, quoting every name, so nothing a person types ever runs as code:

    PostgreSQL / Supabase / Redshift  "public"."orders", date_trunc('month', "order_date")
    Snowflake                          "PUBLIC"."ORDERS",  DATE_TRUNC('MONTH', "ORDER_DATE")
    BigQuery                           `project.dataset.orders`, DATE_TRUNC(DATE(`order_date`), MONTH)
    Salesforce (SOQL)                  FROM Opportunity GROUP BY CALENDAR_YEAR(CloseDate), CALENDAR_MONTH(CloseDate)

The runners for each source are in api/services/warehouses.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from fastapi import HTTPException, status

AGGS = {"sum": "SUM", "avg": "AVG", "count": "COUNT", "min": "MIN", "max": "MAX"}
PERIODS = ("day", "week", "month", "quarter", "year")
MAX_GROUPS = 2           # group by at most two columns (plus the period)
MAX_LIMIT = 5_000        # a summary is meant to be small
DIALECTS = ("postgres", "snowflake", "bigquery")
# Salesforce's date functions for a period (SOQL has no date_trunc).
SOQL_PERIOD = {"day": ["CALENDAR_YEAR", "DAY_IN_YEAR"], "week": ["CALENDAR_YEAR", "WEEK_IN_YEAR"],
               "month": ["CALENDAR_YEAR", "CALENDAR_MONTH"], "quarter": ["CALENDAR_YEAR", "CALENDAR_QUARTER"],
               "year": ["CALENDAR_YEAR"]}
_SOQL_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")


@dataclass
class Summary:
    """One summary request (see the module notes). `table` is the source's own name for it."""

    table: str
    agg: str = "sum"
    metric: str | None = None                       # None only for agg = "count"
    group_by: list[str] = field(default_factory=list)
    date_column: str | None = None
    period: str | None = None                       # one of PERIODS, with date_column
    limit: int = 1_000


def _bad(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=detail)


def validate(raw: dict, table: str, columns: dict[str, str]) -> Summary:
    """
    Check a summary request against the table's real columns; return a clean Summary.

    Args:
        raw: dict, the request: {"agg", "metric", "group_by", "date_column", "period", "limit"}.
        table: str, the table's name as the source knows it (already checked to exist).
        columns: dict[str, str], column name -> kind ("number", "date" or "text").

    Raises:
        HTTPException: 400 for an unknown column, the wrong kind of column, or a bad option.

    Example:
        >>> validate({"agg": "sum", "metric": "amount", "group_by": ["store"]}, "public.orders",
        ...          {"amount": "number", "store": "text"}).group_by
        ['store']
    """
    agg = str(raw.get("agg") or "sum").lower()
    if agg not in AGGS:
        raise _bad(f"Choose one of: {', '.join(AGGS)}.")
    metric = raw.get("metric") or None
    if agg != "count":
        if metric not in columns:
            raise _bad("Choose the number column to add up.")
        if columns[metric] != "number":
            raise _bad(f"'{metric}' isn't a number column, so it can't be added up or averaged.")
    elif metric is not None and metric not in columns:
        raise _bad(f"Column '{metric}' wasn't found.")
    group_by = [str(g) for g in (raw.get("group_by") or [])][:MAX_GROUPS]
    for g in group_by:
        if g not in columns:
            raise _bad(f"Column '{g}' wasn't found.")
    if len(set(group_by)) != len(group_by) or (metric and metric in group_by):
        raise _bad("Use each column once.")
    period = raw.get("period") or None
    date_column = raw.get("date_column") or None
    if period:
        if period not in PERIODS:
            raise _bad(f"Choose a period: {', '.join(PERIODS)}.")
        if date_column not in columns or columns[date_column] != "date":
            raise _bad("Choose a date column for the period.")
    else:
        date_column = None
    try:
        limit = max(1, min(int(raw.get("limit") or 1_000), MAX_LIMIT))
    except (TypeError, ValueError):
        raise _bad("The row limit must be a number.")
    return Summary(table=table, agg=agg, metric=metric, group_by=group_by, date_column=date_column,
                   period=period, limit=limit)


def quote(name: str, dialect: str) -> str:
    """Quote one identifier for a SQL dialect, doubling any quote inside it (so a name can't break out)."""
    if dialect == "bigquery":
        return "`" + name.replace("`", "") + "`"
    return '"' + name.replace('"', '""') + '"'


def table_ref(table: str, dialect: str) -> str:
    """'public.orders' -> "public"."orders" (BigQuery: `project.dataset.table` as one quoted path)."""
    if dialect == "bigquery":
        return "`" + table.replace("`", "") + "`"
    return ".".join(quote(part, dialect) for part in table.split("."))


def _period_expr(col: str, period: str, dialect: str) -> str:
    c = quote(col, dialect)
    if dialect == "bigquery":
        return f"DATE_TRUNC(DATE({c}), {period.upper()})"
    if dialect == "snowflake":
        return f"DATE_TRUNC('{period.upper()}', {c})"
    return f"date_trunc('{period}', {c})"


def build_sql(s: Summary, dialect: str) -> str:
    """
    The summary as one SELECT in a SQL dialect ("postgres" also serves Supabase and Redshift).

    Example:
        >>> build_sql(Summary("public.orders", "sum", "amount", ["store"], "order_date", "month", 100), "postgres")
        'SELECT "store", date_trunc(\\'month\\', "order_date") AS "period", SUM("amount") AS "value" FROM "public"."orders" GROUP BY 1, 2 ORDER BY 2, 3 DESC LIMIT 100'
    """
    if dialect not in DIALECTS:
        raise ValueError(f"Unknown SQL dialect: {dialect}")
    select = [quote(g, dialect) for g in s.group_by]
    if s.period:
        select.append(f"{_period_expr(s.date_column, s.period, dialect)} AS {quote('period', dialect)}")
    value = "COUNT(*)" if s.agg == "count" and not s.metric else f"{AGGS[s.agg]}({quote(s.metric, dialect)})"
    select.append(f"{value} AS {quote('value', dialect)}")
    sql = f"SELECT {', '.join(select)} FROM {table_ref(s.table, dialect)}"
    n_groups = len(s.group_by) + (1 if s.period else 0)
    if n_groups:
        sql += " GROUP BY " + ", ".join(str(i) for i in range(1, n_groups + 1))
        # With a period: in time order (then biggest first); without: biggest first.
        order = (f"{n_groups}, {n_groups + 1} DESC" if s.period else f"{n_groups + 1} DESC")
        sql += f" ORDER BY {order}"
    return sql + f" LIMIT {s.limit}"


def build_soql(s: Summary) -> str:
    """
    The summary in Salesforce's SOQL (an aggregate query on one object).

    SOQL names can't be quoted, so every name must be a plain field name
    (letters, digits, underscores) — and they were already checked against the
    object's real fields (describe).

    Example:
        >>> build_soql(Summary("Opportunity", "sum", "Amount", ["StageName"], "CloseDate", "month", 200))
        'SELECT StageName, CALENDAR_YEAR(CloseDate) y, CALENDAR_MONTH(CloseDate) p, SUM(Amount) value FROM Opportunity GROUP BY StageName, CALENDAR_YEAR(CloseDate), CALENDAR_MONTH(CloseDate) ORDER BY CALENDAR_YEAR(CloseDate), CALENDAR_MONTH(CloseDate) LIMIT 200'
    """
    for name in [s.table, s.metric, s.date_column, *s.group_by]:
        if name and not _SOQL_NAME.match(name):
            raise _bad(f"'{name}' isn't a Salesforce field name.")
    select, groups = list(s.group_by), list(s.group_by)
    if s.period:
        funcs = SOQL_PERIOD[s.period]
        aliases = ["y", "p"] if len(funcs) == 2 else ["y"]
        for f, a in zip(funcs, aliases):
            select.append(f"{f}({s.date_column}) {a}")
            groups.append(f"{f}({s.date_column})")
    value = f"COUNT(Id)" if s.agg == "count" and not s.metric else f"{AGGS[s.agg]}({s.metric})"
    select.append(f"{value} value")
    q = f"SELECT {', '.join(select)} FROM {s.table}"
    if groups:
        q += f" GROUP BY {', '.join(groups)}"
        q += " ORDER BY " + (", ".join(groups[len(s.group_by):]) if s.period else f"{value} DESC")
    return q + f" LIMIT {min(s.limit, 2000)}"   # Salesforce returns at most 2,000 aggregate rows
