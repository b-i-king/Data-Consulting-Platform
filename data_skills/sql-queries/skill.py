"""
sql-queries — warehouse-ready SQL for a table (Data Engineering).

Python implementation of README.md in this folder (Anthropic data plugin,
Apache-2.0). Generates, per dialect (PostgreSQL / Supabase, BigQuery, Snowflake):

  1. CREATE TABLE — column types mapped from pandas (integers, decimals with
     NUMERIC(14,2) for money, dates/timestamps, booleans, text), NOT NULL where
     the data is never empty, a PRIMARY KEY if one column is unique and complete
  2. Data-quality checks — row count, null counts per column, duplicate keys
  3. Starter analysis queries — monthly totals with a window-function running
     total, and top categories — using the right date function per dialect
"""

from __future__ import annotations

import re

import pandas as pd

from data_skills._core import Param, SkillResult, SkillSpec, categorical_columns, date_columns, numeric_columns

SKILL = SkillSpec(
    id="sql-queries",
    title="Warehouse-ready SQL",
    description="CREATE TABLE, data-quality checks, and starter analysis queries for Postgres, BigQuery, or Snowflake.",
    params={
        "table_name": Param("str", "my_table", "Table name"),
        "dialect": Param("choice", "postgres", "SQL dialect", ("postgres", "bigquery", "snowflake")),
    },
)

TYPES = {
    "postgres": {"int": "BIGINT", "money": "NUMERIC(14, 2)", "float": "DOUBLE PRECISION", "bool": "BOOLEAN",
                 "date": "DATE", "ts": "TIMESTAMPTZ", "text": "TEXT"},
    "bigquery": {"int": "INT64", "money": "NUMERIC", "float": "FLOAT64", "bool": "BOOL",
                 "date": "DATE", "ts": "TIMESTAMP", "text": "STRING"},
    "snowflake": {"int": "NUMBER(38, 0)", "money": "NUMBER(14, 2)", "float": "FLOAT", "bool": "BOOLEAN",
                  "date": "DATE", "ts": "TIMESTAMP_TZ", "text": "VARCHAR"},
}


def ident(name: str) -> str:
    """Safe snake_case identifier: 'Order Date' -> order_date (no quoting needed)."""
    s = re.sub(r"[^a-z0-9]+", "_", str(name).strip().lower()).strip("_") or "col"
    return f"c_{s}" if s[0].isdigit() else s


def sql_type(col: str, s: pd.Series, is_date: bool, dialect: str) -> str:
    t = TYPES[dialect]
    if is_date:
        return t["date"]
    if pd.api.types.is_bool_dtype(s):
        return t["bool"]
    if pd.api.types.is_integer_dtype(s):
        return t["int"]
    if pd.api.types.is_float_dtype(s):
        return t["money"] if re.search(r"amount|price|cost|total|revenue|balance|fee", col, re.I) else t["float"]
    return t["text"]


def run(df: pd.DataFrame, table_name="my_table", dialect="postgres") -> SkillResult:
    tbl = ident(table_name)
    dates = set(date_columns(df))
    pk = next((c for c in df.columns if df[c].notna().all() and df[c].is_unique and re.search(r"id$", str(c), re.I)), None)
    cols = []
    for c in df.columns:
        line = f"    {ident(c)} {sql_type(str(c), df[c], str(c) in dates, dialect)}"
        if df[c].notna().all():
            line += " NOT NULL"
        cols.append(line)
    if pk is not None:
        cols.append(f"    PRIMARY KEY ({ident(pk)})" + (" NOT ENFORCED" if dialect == "bigquery" else ""))
    ddl = f"CREATE TABLE IF NOT EXISTS {tbl} (\n" + ",\n".join(cols) + "\n);"

    checks = [f"-- Row count\nSELECT COUNT(*) AS row_count FROM {tbl};",
              "-- Missing values per column\nSELECT\n" + ",\n".join(
                  f"    COUNT(*) - COUNT({ident(c)}) AS {ident(c)}_nulls" for c in df.columns) + f"\nFROM {tbl};"]
    if pk is not None:
        checks.append(f"-- Duplicate keys (should return nothing)\nSELECT {ident(pk)}, COUNT(*) FROM {tbl} "
                      f"GROUP BY {ident(pk)} HAVING COUNT(*) > 1;")

    analysis = []
    d, m = next(iter(sorted(dates)), None), next(iter(numeric_columns(df)), None)
    if d and m:
        month = f"DATE_TRUNC({ident(d)}, MONTH)" if dialect == "bigquery" else f"DATE_TRUNC('month', {ident(d)})"
        analysis.append(f"-- Monthly totals with a running total\nSELECT\n    {month} AS month,\n"
                        f"    SUM({ident(m)}) AS total_{ident(m)},\n"
                        f"    SUM(SUM({ident(m)})) OVER (ORDER BY {month}) AS running_total\n"
                        f"FROM {tbl}\nGROUP BY 1\nORDER BY 1;")
    cat = next(iter(categorical_columns(df)), None)
    if cat and m:
        analysis.append(f"-- Top categories\nSELECT {ident(cat)}, SUM({ident(m)}) AS total_{ident(m)}\nFROM {tbl}\n"
                        f"GROUP BY {ident(cat)}\nORDER BY 2 DESC\nLIMIT 10;")

    sql = f"-- {dialect.title()} SQL for {tbl}\n\n{ddl}\n\n" + "\n\n".join(checks + analysis) + "\n"
    return SkillResult(skill=SKILL.id, summary=f"{dialect.title()} DDL for {tbl} ({df.shape[1]} columns"
                       + (f", primary key {ident(pk)})." if pk is not None else ", no natural primary key)."),
                       findings=[f"{len(checks)} quality checks and {len(analysis)} starter queries included.",
                                 "Column names were converted to snake_case so no quoting is needed."],
                       artifacts={"sql_schema": sql})
