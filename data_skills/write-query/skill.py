"""
write-query — turn a question into SQL (Data Exploration & Analysis).

Python implementation of README.md in this folder (Anthropic data plugin,
Apache-2.0). Builds a readable, dialect-correct query from a structured
request (no free-form text → SQL guessing):

    metric, agg, group_by, filters, date_column + period, order, limit

Dialects: PostgreSQL / Supabase, BigQuery, Snowflake — they differ in
identifier quoting and date truncation, which this handles. The query is
validated against the table's columns, and when a table is provided the same
logic is ALSO run in pandas so you can see the expected result.
"""

from __future__ import annotations

import pandas as pd

from data_skills._core import Param, SkillInputError, SkillResult, SkillSpec, as_datetime, records

SKILL = SkillSpec(
    id="write-query",
    title="SQL for your questions",
    description="Build a dialect-correct SQL query (Postgres, BigQuery, Snowflake) and preview its result in pandas.",
    params={
        "table_name": Param("str", "my_table", "Table name in the database"),
        "metric": Param("column", None, "Column to aggregate (empty = count rows)"),
        "agg": Param("choice", "sum", "Aggregation", ("sum", "avg", "count", "min", "max")),
        "group_by": Param("columns", None, "Columns to group by"),
        "date_column": Param("column", None, "Date column to bucket by period"),
        "period": Param("choice", "month", "Date bucket", ("day", "week", "month", "quarter", "year")),
        "filters": Param("json", None, 'List of filters, e.g. [{"column":"store","op":"=","value":"Midtown"}]'),
        "dialect": Param("choice", "postgres", "SQL dialect", ("postgres", "bigquery", "snowflake")),
        "limit": Param("int", 100, "Maximum rows"),
    },
)

OPS = {"=", "!=", ">", ">=", "<", "<=", "in", "like"}


def quote(name: str, dialect: str) -> str:
    """
    Quote a table or column name for the dialect so spaces and odd characters are safe.

    BigQuery uses backticks; Postgres and Snowflake use double quotes, with any embedded
    double quote doubled.

    Args:
        name: str, the identifier.
        dialect: str, "postgres", "bigquery", or "snowflake".

    Returns:
        str: the quoted identifier.

    Example:
        >>> quote("store", "postgres")
        '"store"'
        >>> quote("store", "bigquery")
        '`store`'
    """
    return f"`{name}`" if dialect == "bigquery" else '"' + name.replace('"', '""') + '"'


def literal(v) -> str:
    """
    Write a Python value as a SQL literal.

    Ints and floats are written as-is (bools are not treated as numbers); everything else
    becomes a single-quoted string with embedded single quotes doubled, which prevents
    the value from breaking out of the string.

    Args:
        v: Any, the filter value.

    Returns:
        str: the SQL literal.

    Example:
        >>> literal(5)
        '5'
        >>> literal("O'Brien")
        "'O''Brien'"
    """
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def date_trunc(col: str, period: str, dialect: str) -> str:
    """
    Build the dialect's expression that truncates a date to the start of a period.

    BigQuery: DATE_TRUNC(col, MONTH). Postgres and Snowflake: DATE_TRUNC('month', col).

    Args:
        col: str, the already-quoted date column.
        period: str, "day", "week", "month", "quarter", or "year".
        dialect: str, "postgres", "bigquery", or "snowflake".

    Returns:
        str: the SQL expression.

    Example:
        >>> print(date_trunc('"order_date"', "month", "postgres"))
        DATE_TRUNC('month', "order_date")
    """
    if dialect == "bigquery":
        return f"DATE_TRUNC({col}, {period.upper()})"
    return f"DATE_TRUNC('{period}', {col})"


def run(df, table_name="my_table", metric=None, agg="sum", group_by=None, date_column=None, period="month",
        filters=None, dialect="postgres", limit=100) -> SkillResult:
    """
    Build a readable, dialect-correct SQL query from a structured request and preview it in pandas.

    Every referenced column is validated against the table first. The query selects the
    optional date bucket (date_trunc) and group-by columns, then agg(metric) or COUNT(*),
    applies filters (operators =, !=, >, >=, <, <=, in, like) as WHERE ... AND ..., groups,
    orders (by date ascending, else by the value descending), and limits. The same logic is
    then run in pandas (LIKE becomes a case-insensitive "contains" with % stripped) so you can
    see the expected result.

    Args:
        df: pd.DataFrame, the table (used for validation and the preview).
        table_name: str, table name in the database (default "my_table").
        metric: str | None, column to aggregate; None means count rows.
        agg: str, "sum" (default), "avg", "count", "min", or "max".
        group_by: list[str] | None, columns to group by.
        date_column: str | None, date column to bucket by period.
        period: str, "day", "week", "month" (default), "quarter", or "year".
        filters: list[dict] | None, e.g. [{"column": "store", "op": "=", "value": "Midtown"}];
            "in" expects a list value.
        dialect: str, "postgres" (default), "bigquery", or "snowflake".
        limit: int, maximum rows (default 100).

    Returns:
        SkillResult: summary with the dialect and preview row count; a finding on how to use it;
        table "preview" (pandas result); artifact "sql_query".

    Raises:
        SkillInputError: if a column is not in the table or a filter operator is unsupported.

    Example:
        >>> res = run(df, table_name="sales", metric="amount", group_by=["store"])
        >>> print(res.artifacts["sql_query"])
        SELECT
            "store",
            SUM("amount") AS "sum_amount"
        FROM "sales"
        GROUP BY "store"
        ORDER BY "sum_amount" DESC
        LIMIT 100;
    """
    group_by = group_by or []
    filters = filters or []
    for c in [metric, date_column, *group_by, *(f.get("column") for f in filters)]:
        if c and c not in df.columns:
            raise SkillInputError(f"Column '{c}' is not in the table.")

    q = lambda c: quote(c, dialect)  # noqa: E731
    select, group_exprs = [], []
    if date_column:
        expr = date_trunc(q(date_column), period, dialect)
        select.append(f"{expr} AS {q(period)}")
        group_exprs.append(expr)
    for g in group_by:
        select.append(q(g))
        group_exprs.append(q(g))
    value_name = f"{agg}_{metric}" if metric else "row_count"
    select.append(f"{agg.upper()}({q(metric)}) AS {q(value_name)}" if metric else f"COUNT(*) AS {q(value_name)}")

    where = []
    for f in filters:
        op = str(f.get("op", "=")).lower()
        if op not in OPS:
            raise SkillInputError(f"Unsupported filter operator '{op}'.")
        val = f.get("value")
        rhs = "(" + ", ".join(literal(v) for v in val) + ")" if op == "in" else literal(val)
        where.append(f"{q(f['column'])} {op.upper()} {rhs}")

    table = q(table_name) if dialect != "bigquery" else f"`{table_name}`"
    sql = ["SELECT", "    " + ",\n    ".join(select), f"FROM {table}"]
    if where:
        sql.append("WHERE " + "\n  AND ".join(where))
    if group_exprs:
        sql.append("GROUP BY " + ", ".join(group_exprs))
    sql.append(f"ORDER BY {group_exprs[0] if date_column else q(value_name) + ' DESC'}")
    sql.append(f"LIMIT {int(limit)};")
    query = "\n".join(sql)

    # Same logic in pandas, to preview the expected answer.
    d = df.copy()
    for f in filters:
        col, op, val = f["column"], str(f.get("op", "=")).lower(), f.get("value")
        mask = {"=": d[col] == val, "!=": d[col] != val, ">": d[col] > val, ">=": d[col] >= val,
                "<": d[col] < val, "<=": d[col] <= val}.get(op)
        if op == "in":
            mask = d[col].isin(val)
        elif op == "like":
            mask = d[col].astype(str).str.contains(str(val).strip("%"), case=False, na=False)
        d = d[mask]
    keys = []
    if date_column:
        d[period] = as_datetime(d[date_column]).dt.to_period({"day": "D", "week": "W", "month": "M", "quarter": "Q", "year": "Y"}[period]).dt.start_time
        keys.append(period)
    keys += group_by
    pagg = {"avg": "mean"}.get(agg, agg)
    if keys:
        preview = (d.groupby(keys)[metric].agg(pagg) if metric else d.groupby(keys).size()).rename(value_name).reset_index()
        preview = preview.sort_values(keys[0] if date_column else value_name, ascending=bool(date_column))
    else:
        preview = pd.DataFrame({value_name: [d[metric].agg(pagg) if metric else len(d)]})

    return SkillResult(skill=SKILL.id, summary=f"{dialect.title()} query built ({len(preview.head(limit)):,} result rows in the preview).",
                       findings=["Run the SQL in your warehouse; the preview shows what it should return for the uploaded file."],
                       tables={"preview": records(preview.head(limit), None)}, artifacts={"sql_query": query + "\n"})
