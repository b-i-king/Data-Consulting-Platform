"""
Optional big-data engine: PySpark, ready for when pandas is not enough.

WHAT THIS FILE DOES
-------------------
pandas is the data engine (CLAUDE.md). It holds a whole table in ONE machine's
memory, which is perfect for the uploads this platform handles today. Some
future clients will have tables too big for that, or data that already lives
in a cloud warehouse (BigQuery, Snowflake, Databricks, Redshift, a large
Postgres). For them, this module provides a second engine, Apache Spark,
driven from Python through PySpark. See docs/SPARK.md for the full story.

It gives the rest of the backend six small building blocks:

  * is_available()       — is PySpark installed AND switched on (SPARK_ENABLED)?
  * choose_engine()      — "pandas" or "spark" for a given table size / source
  * get_spark_session()  — the one shared SparkSession (Spark's entry point)
  * read_jdbc()          — read a table or query from a cloud database
  * to_pandas_safely() / from_pandas() — move data between the two engines,
                           with a row cap so a huge table never blows memory
  * clean_with_spark()   — the first pandas cleaning steps, written for Spark
  * melt_to_unified()    — "one row per field" (the warehouse.unified_data shape)

WHY PYSPARK IS OPTIONAL
-----------------------
PySpark is large (~300 MB), needs Java 17 and several GB of RAM, so it is NOT
in requirements.txt and is OFF by default. This file therefore never imports
pyspark at module load: every pyspark import is inside a function, so the API
starts (and the tests run) on machines without it. Install with
`pip install -r requirements-spark.txt` and set SPARK_ENABLED=true.

A NOTE FOR PANDAS USERS
-----------------------
A Spark DataFrame looks like a pandas DataFrame but is LAZY: calls such as
`.withColumn()` or `.dropDuplicates()` only build a plan. Nothing is computed
until an "action" asks for results (`.count()`, `.toPandas()`, `.write`).
That is how Spark handles data far bigger than memory: it streams through it
in partitions, possibly on many machines.
"""

from __future__ import annotations

import importlib.util
import logging
import re
import threading
from typing import TYPE_CHECKING, Iterable, Literal

import pandas as pd

from api.core.config import Settings, get_settings
from api.services.cleaning import clean_column_names

if TYPE_CHECKING:  # only for type hints; never imported at runtime
    from pyspark.sql import DataFrame as SparkDataFrame
    from pyspark.sql import SparkSession

log = logging.getLogger(__name__)

Engine = Literal["pandas", "spark"]

# Sources that ARE a cloud warehouse / large database. Their data is already
# remote and usually large, so Spark (reading in parallel over JDBC or a native
# connector) is the right engine whenever it is available, whatever the size.
CLOUD_WAREHOUSE_SOURCES = frozenset({
    "bigquery",
    "snowflake",
    "databricks",
    "redshift",
    "postgres_large",
})

# How to install Spark, repeated in every SparkUnavailable message.
INSTALL_HELP = (
    "PySpark is an optional engine. To use it: install Java 17, run "
    "`pip install -r requirements-spark.txt`, and set SPARK_ENABLED=true. "
    "See docs/SPARK.md."
)


class SparkUnavailable(RuntimeError):
    """
    Raised when Spark is asked for but PySpark is missing or switched off.

    Callers can catch it and fall back to pandas. The message always says how
    to install and enable Spark.

    Example:
        >>> try:
        ...     get_spark_session(Settings(spark_enabled=False))
        ... except SparkUnavailable as e:
        ...     print("fall back to pandas")
        fall back to pandas
    """


# ── Availability and engine choice ──────────────────────────────────────────

def pyspark_installed() -> bool:
    """
    Check whether the pyspark package can be imported, WITHOUT importing it.

    `importlib.util.find_spec` only looks the package up on disk, so this is
    instant and does not start Java.

    Returns:
        bool: True if `import pyspark` would succeed.

    Example:
        >>> isinstance(pyspark_installed(), bool)
        True
    """
    return importlib.util.find_spec("pyspark") is not None


def is_available(settings: Settings | None = None) -> bool:
    """
    True when Spark can be used: SPARK_ENABLED is on AND pyspark is installed.

    The setting is checked first, so on a normal deployment (Spark off) this
    never even looks for the package.

    Args:
        settings: Settings | None: settings to read; defaults to `get_settings()`.

    Returns:
        bool: True if Spark jobs may run on this machine.

    Example:
        >>> is_available(Settings(spark_enabled=False))
        False
    """
    settings = settings or get_settings()
    return bool(settings.spark_enabled) and pyspark_installed()


def choose_engine(
    rows: int,
    columns: int,
    source: str | None = None,
    *,
    available: bool | None = None,
    min_cells: int | None = None,
) -> Engine:
    """
    Pick the engine for one job: "pandas" (the default) or "spark".

    Decision rules, in order:
      1. Spark not available (off or not installed)      -> "pandas"
      2. `source` is a cloud warehouse (CLOUD_WAREHOUSE_SOURCES:
         bigquery, snowflake, databricks, redshift, postgres_large) -> "spark"
      3. rows × columns > min_cells (SPARK_MIN_CELLS, default 50,000,000)
                                                          -> "spark"
      4. otherwise                                        -> "pandas"

    Pass `available` and `min_cells` explicitly to make this a pure function
    (handy in tests); left as None they are read from the settings.

    Args:
        rows: int: number of rows (an estimate is fine, e.g. from a COUNT(*)).
        columns: int: number of columns.
        source: str | None: where the data comes from, e.g. "upload",
            "quickbooks", "snowflake". Case-insensitive.
        available: bool | None: whether Spark can run; None = `is_available()`.
        min_cells: int | None: cell threshold; None = SPARK_MIN_CELLS.

    Returns:
        Engine: "pandas" or "spark".

    Example:
        >>> choose_engine(1_000, 10, "upload", available=True, min_cells=50_000_000)
        'pandas'
        >>> choose_engine(1_000, 10, "Snowflake", available=True, min_cells=50_000_000)
        'spark'
        >>> choose_engine(10_000_000, 10, "upload", available=True, min_cells=50_000_000)
        'spark'
        >>> choose_engine(10_000_000, 10, "snowflake", available=False)
        'pandas'
    """
    if available is None:
        available = is_available()
    if not available:
        return "pandas"                       # rule 1: pandas always works
    if source and source.strip().lower() in CLOUD_WAREHOUSE_SOURCES:
        return "spark"                        # rule 2: data already in a warehouse
    if min_cells is None:
        min_cells = get_settings().spark_min_cells
    cells = max(int(rows), 0) * max(int(columns), 0)
    if cells > min_cells:
        return "spark"                        # rule 3: too big for pandas memory
    return "pandas"                           # rule 4: the default


# ── The shared SparkSession ─────────────────────────────────────────────────

# Starting Spark launches a Java process and takes seconds, so we build the
# session once and reuse it (like a database connection pool).
_session: "SparkSession | None" = None
_session_lock = threading.Lock()


def get_spark_session(
    settings: Settings | None = None,
    extra_conf: dict[str, str] | None = None,
) -> "SparkSession":
    """
    Return the one shared SparkSession, building it on first use.

    Built from SPARK_APP_NAME and SPARK_MASTER, plus any `extra_conf` (Spark
    settings such as {"spark.jars.packages": "org.postgresql:postgresql:42.7.4"}
    to download a JDBC driver, or {"spark.driver.memory": "4g"}). Extra conf
    only applies when the session is first created; later calls return the
    cached session as is.

    Args:
        settings: Settings | None: settings to read; defaults to `get_settings()`.
        extra_conf: dict[str, str] | None: extra Spark configuration keys.

    Returns:
        SparkSession: the entry point for every Spark operation.

    Raises:
        SparkUnavailable: if SPARK_ENABLED is off or pyspark is not installed
            (the message explains how to install it).

    Example:
        >>> spark = get_spark_session()          # doctest: +SKIP
        >>> spark.range(3).count()               # doctest: +SKIP
        3
    """
    global _session
    settings = settings or get_settings()
    if not settings.spark_enabled:
        raise SparkUnavailable(f"Spark is switched off (SPARK_ENABLED is not true). {INSTALL_HELP}")
    if not pyspark_installed():
        raise SparkUnavailable(f"The pyspark package is not installed. {INSTALL_HELP}")

    with _session_lock:                       # two requests must not start two Javas
        if _session is None:
            from pyspark.sql import SparkSession  # imported here, never at module load

            builder = (
                SparkSession.builder
                .appName(settings.spark_app_name)
                .master(settings.spark_master)
                # Small local jobs: 200 shuffle partitions (Spark's default) is
                # overkill and slow; Spark's adaptive execution tunes it anyway.
                .config("spark.sql.shuffle.partitions", "8")
                .config("spark.sql.adaptive.enabled", "true")
                # Faster toPandas()/createDataFrame() via Apache Arrow.
                .config("spark.sql.execution.arrow.pyspark.enabled", "true")
            )
            for key, value in (extra_conf or {}).items():
                builder = builder.config(key, value)
            _session = builder.getOrCreate()
            log.info("Started Spark session %r on %s", settings.spark_app_name, settings.spark_master)
        return _session


def stop_spark_session() -> None:
    """
    Stop the shared SparkSession (if one is running) and forget it.

    Frees the Java process and its memory; the next `get_spark_session()`
    starts a fresh one. Used by tests and at app shutdown.

    Returns:
        None

    Example:
        >>> stop_spark_session()   # safe even when no session was started
    """
    global _session
    with _session_lock:
        if _session is not None:
            _session.stop()
            _session = None


# ── Reading cloud databases (JDBC) ──────────────────────────────────────────

def redact_jdbc_url(url: str) -> str:
    """
    Hide passwords and secrets in a JDBC URL so it is safe to log.

    Covers both styles: credentials before the host
    ("jdbc:postgresql://user:pw@host/db") and as URL parameters
    ("...?user=x&password=pw", Snowflake's "...&private_key=...").

    Args:
        url: str: a JDBC connection URL.

    Returns:
        str: the same URL with secret values replaced by "***".

    Example:
        >>> redact_jdbc_url("jdbc:postgresql://bob:s3cret@db.example.com:5432/app?password=x&ssl=true")
        'jdbc:postgresql://***@db.example.com:5432/app?password=***&ssl=true'
    """
    # "//user:password@host" -> "//***@host"
    url = re.sub(r"//[^/@]+@", "//***@", url)
    # password=..., pwd=..., token=..., secret=..., private_key=... -> =***
    return re.sub(
        r"(?i)\b(password|pwd|token|secret|private_key|private_key_pwd|access_key|secret_key)=([^&;]*)",
        r"\1=***",
        url,
    )


def read_jdbc(
    url: str,
    table_or_query: str,
    properties: dict[str, str] | None = None,
    *,
    partition_column: str | None = None,
    lower_bound: int | None = None,
    upper_bound: int | None = None,
    num_partitions: int | None = None,
    fetch_size: int = 10_000,
    settings: Settings | None = None,
) -> "SparkDataFrame":
    """
    Read a table or a SQL query from a cloud database into a Spark DataFrame.

    Works with any database that has a JDBC driver: Postgres/Supabase,
    Amazon Redshift, Snowflake, SQL Server, MySQL. The driver jar must be on
    Spark's classpath — the easiest way is `extra_conf={"spark.jars.packages":
    "org.postgresql:postgresql:42.7.4"}` on the first `get_spark_session()` call
    (docs/SPARK.md lists the coordinates for each database).

    Parallel reads: give `partition_column` (a numeric or date column with
    evenly spread values, e.g. an auto-increment id) plus `lower_bound`,
    `upper_bound` and `num_partitions`. Spark then runs `num_partitions`
    queries side by side, each covering one slice of that range. Without them
    the whole table comes through ONE connection.

    Credentials (user, password) go in `properties`, never in log lines: only
    the redacted URL and the property NAMES are logged.

    Args:
        url: str: JDBC URL, e.g. "jdbc:postgresql://host:5432/postgres".
        table_or_query: str: a table name ("public.orders") or a SELECT query.
        properties: dict[str, str] | None: connection options such as
            {"user": ..., "password": ..., "driver": "org.postgresql.Driver"}.
        partition_column: str | None: column to split the read on.
        lower_bound: int | None: smallest value of `partition_column` to split from.
        upper_bound: int | None: largest value of `partition_column` to split to.
        num_partitions: int | None: how many parallel queries (and connections).
        fetch_size: int: rows fetched per round trip (bigger = fewer trips).
        settings: Settings | None: settings for `get_spark_session()`.

    Returns:
        SparkDataFrame: a lazy DataFrame; nothing is read until an action runs.

    Raises:
        SparkUnavailable: if Spark is off or not installed.
        ValueError: if only some of the partition options are given.

    Example:
        >>> sdf = read_jdbc(                                   # doctest: +SKIP
        ...     "jdbc:postgresql://db.example.com:5432/postgres",
        ...     "public.orders",
        ...     {"user": "reader", "password": os.environ["DB_PASSWORD"],
        ...      "driver": "org.postgresql.Driver"},
        ...     partition_column="order_id", lower_bound=1,
        ...     upper_bound=10_000_000, num_partitions=8,
        ... )
    """
    partition_opts = [partition_column, lower_bound, upper_bound, num_partitions]
    if any(o is not None for o in partition_opts) and not all(o is not None for o in partition_opts):
        raise ValueError(
            "For a parallel read give all four: partition_column, lower_bound, "
            "upper_bound and num_partitions (or none of them)."
        )

    spark = get_spark_session(settings)
    reader = spark.read.format("jdbc").option("url", url).option("fetchsize", str(fetch_size))
    for key, value in (properties or {}).items():
        reader = reader.option(key, value)

    is_query = table_or_query.lstrip().lower().startswith(("select", "with"))
    if partition_column is not None:
        # Spark's "query" option cannot be combined with partitioning, so a
        # query is wrapped as a sub-select: "(SELECT ...) AS src".
        dbtable = f"({table_or_query}) AS src" if is_query else table_or_query
        reader = (
            reader.option("dbtable", dbtable)
            .option("partitionColumn", partition_column)
            .option("lowerBound", str(lower_bound))
            .option("upperBound", str(upper_bound))
            .option("numPartitions", str(num_partitions))
        )
    elif is_query:
        reader = reader.option("query", table_or_query)
    else:
        reader = reader.option("dbtable", table_or_query)

    # Never log the password: redacted URL, and property names only.
    log.info(
        "Spark JDBC read from %s (options: %s, partitions: %s)",
        redact_jdbc_url(url), sorted((properties or {}).keys()), num_partitions or 1,
    )
    return reader.load()


# ── Moving data between pandas and Spark ────────────────────────────────────

def to_pandas_safely(spark_df: "SparkDataFrame", max_rows: int = 100_000) -> pd.DataFrame:
    """
    Bring a Spark DataFrame into pandas, but never more than `max_rows` rows.

    A plain `.toPandas()` on a billion-row table would try to pull it all into
    this machine's memory and crash. Here Spark sends at most `max_rows + 1`
    rows (the extra one only tells us whether rows were cut). When the table
    was cut, `result.attrs["truncated"]` is True — show the client a note.

    Args:
        spark_df: SparkDataFrame: the (possibly huge) Spark table.
        max_rows: int: the most rows to return (must be 0 or more).

    Returns:
        pd.DataFrame: up to `max_rows` rows, with `attrs["truncated"]` (bool)
        and `attrs["max_rows"]` (int) set.

    Raises:
        ValueError: if `max_rows` is negative.

    Example:
        >>> pdf = to_pandas_safely(sdf, max_rows=5)   # doctest: +SKIP
        >>> len(pdf), pdf.attrs["truncated"]          # doctest: +SKIP
        (5, True)
    """
    if max_rows < 0:
        raise ValueError("max_rows must be 0 or more.")
    pdf = spark_df.limit(max_rows + 1).toPandas()   # limit runs inside Spark, before transfer
    truncated = len(pdf) > max_rows
    if truncated:
        pdf = pdf.iloc[:max_rows].copy()
    pdf.attrs["truncated"] = truncated
    pdf.attrs["max_rows"] = max_rows
    return pdf


def from_pandas(pdf: pd.DataFrame, settings: Settings | None = None) -> "SparkDataFrame":
    """
    Turn a pandas DataFrame into a Spark DataFrame.

    Useful to run a Spark step (e.g. `melt_to_unified`) on a table that was
    read and cleaned with pandas. Uses Apache Arrow for speed when available.
    pandas' missing values (NaN, None) become Spark nulls, so Spark's
    `dropna()` and `isNull()` see them as missing, the way pandas does.

    Args:
        pdf: pd.DataFrame: the pandas table.
        settings: Settings | None: settings for `get_spark_session()`.

    Returns:
        SparkDataFrame: the same rows and columns, as a Spark DataFrame.

    Raises:
        SparkUnavailable: if Spark is off or not installed.

    Example:
        >>> sdf = from_pandas(pd.DataFrame({"a": [1, 2]}))   # doctest: +SKIP
        >>> sdf.count()                                       # doctest: +SKIP
        2
    """
    # pandas marks a missing number as NaN; Spark treats NaN as a real (odd)
    # number, not as missing. Swap NaN for None so it arrives as null.
    pdf = pdf.astype(object).where(pdf.notna(), None) if pdf.isna().any().any() else pdf
    return get_spark_session(settings).createDataFrame(pdf)


# ── Cleaning and reshaping in Spark ─────────────────────────────────────────

def clean_with_spark(
    sdf: "SparkDataFrame",
    *,
    trim_strings: bool = True,
    drop_empty_rows: bool = True,
    drop_duplicates: bool = True,
) -> "SparkDataFrame":
    """
    The first steps of the pandas cleaning pipeline, for a Spark DataFrame.

    Mirrors api/services/cleaning.py so both engines give the same result:
      Step 0. Column names -> unique snake_case, using the very same
              `clean_column_names` helper ("Order Date" -> order_date,
              a second "Amount" -> amount_2).
      Step 1. Trim spaces in text columns; blank text ("", "  ") -> null.
              Decimal NaN -> null too (pandas counts NaN as missing; Spark
              would otherwise treat it as a number).
      Step 2. Drop rows where EVERY value is null.
      Step 3. Drop exact duplicate rows (all values equal).
    Unlike pandas, Spark does not keep row order, so "which copy is kept" is
    not defined — the copies are identical, so the result is the same.

    Args:
        sdf: SparkDataFrame: the table to clean (not modified; Spark is immutable).
        trim_strings: bool: run Step 1.
        drop_empty_rows: bool: run Step 2.
        drop_duplicates: bool: run Step 3.

    Returns:
        SparkDataFrame: the cleaned (lazy) table.

    Example:
        >>> clean_with_spark(sdf).columns            # doctest: +SKIP
        ['order_date', 'customer_id', 'amount', 'amount_2']
    """
    from pyspark.sql import functions as F
    from pyspark.sql.types import DoubleType, FloatType, StringType

    # Step 0: rename by POSITION with toDF, so odd names ("a.b", "Unit Price ($)")
    # never need quoting.
    sdf = sdf.toDF(*clean_column_names(sdf.columns))

    # Step 1: trim text and turn blanks into null (like pandas' NaN).
    if trim_strings:
        for field in sdf.schema.fields:
            if isinstance(field.dataType, StringType):
                trimmed = F.trim(F.col(field.name))
                sdf = sdf.withColumn(field.name, F.when(trimmed == "", None).otherwise(trimmed))
            elif isinstance(field.dataType, (DoubleType, FloatType)):
                col = F.col(field.name)
                sdf = sdf.withColumn(field.name, F.when(F.isnan(col), None).otherwise(col))

    if drop_empty_rows:
        sdf = sdf.dropna(how="all")            # Step 2
    if drop_duplicates:
        sdf = sdf.dropDuplicates()             # Step 3: all columns must match
    return sdf


def melt_to_unified(
    sdf: "SparkDataFrame",
    id_cols: Iterable[str],
    value_cols: Iterable[str] | None = None,
    *,
    record_id_col: str = "record_id",
    field_col: str = "field_name",
    drop_nulls: bool = True,
) -> "SparkDataFrame":
    """
    Reshape a wide table to "one row per field" (the warehouse.unified_data shape).

    Like pandas' `df.melt()`: each record's columns become separate rows, so
    tables with different columns can share ONE long table. Output columns:

      record_id     text   — the id column(s); several are joined with "|"
      field_name    text   — the original column name
      value_text    text   — the value as text (always filled for a kept row)
      value_number  double — the value as a number, or null if it isn't one
      value_date    date   — the value as a date (ISO YYYY-MM-DD text only), else null

    Typed copies let the warehouse sum numbers and filter dates without
    re-parsing text. Numbers must be plain ("1234.56"; "$1,234.56" stays text
    only — clean it first). Dates are recognized in ISO form ("2026-10-15").

    Uses `DataFrame.unpivot` (Spark 3.4+). Every value is cast to text first,
    because unpivot needs one common type for the value column.

    Args:
        sdf: SparkDataFrame: the wide table.
        id_cols: Iterable[str]: column(s) that identify a record (at least one).
        value_cols: Iterable[str] | None: columns to melt; None = every other column.
        record_id_col: str: name of the output record id column.
        field_col: str: name of the output field name column.
        drop_nulls: bool: drop rows whose value is null (missing fields aren't stored).

    Returns:
        SparkDataFrame: columns record_id, field_name, value_text, value_number, value_date.

    Raises:
        ValueError: if no id column is given, a column is missing, or nothing is left to melt.

    Example:
        >>> # order_id | amount | order_date        record_id | field_name | value_text | value_number | value_date
        >>> # 1        | 9.5    | 2026-10-15   ->   1         | amount     | 9.5        | 9.5          | null
        >>> #                                        1         | order_date | 2026-10-15 | null         | 2026-10-15
        >>> melt_to_unified(sdf, ["order_id"]).columns          # doctest: +SKIP
        ['record_id', 'field_name', 'value_text', 'value_number', 'value_date']
    """
    from pyspark.sql import functions as F

    id_cols = list(id_cols)
    if not id_cols:
        raise ValueError("Give at least one id column that identifies each record.")
    missing = [c for c in id_cols + list(value_cols or []) if c not in sdf.columns]
    if missing:
        raise ValueError(f"Columns not found: {missing}")
    if value_cols is None:
        value_cols = [c for c in sdf.columns if c not in id_cols]
    value_cols = list(value_cols)
    if not value_cols:
        raise ValueError("No value columns left to melt.")

    # One text record id: a single id as is; several joined with "|".
    if len(id_cols) == 1:
        rid = F.col(id_cols[0]).cast("string")
    else:
        rid = F.concat_ws("|", *[F.col(c).cast("string") for c in id_cols])

    # Every value as text, so unpivot has one common type.
    wide = sdf.select(
        rid.alias(record_id_col),
        *[F.col(c).cast("string").alias(c) for c in value_cols],
    )
    long = wide.unpivot(
        ids=[record_id_col], values=value_cols,
        variableColumnName=field_col, valueColumnName="value_text",
    )
    if drop_nulls:
        long = long.where(F.col("value_text").isNotNull())

    # try_cast gives null (instead of an error) when the text isn't a number/date,
    # whatever Spark's ANSI mode is (it is on by default in Spark 4).
    # Dates: only text that STARTS like an ISO date (YYYY-MM-DD) is cast,
    # because Spark would otherwise read a plain number such as "2026" as
    # the date 2026-01-01.
    looks_like_date = F.col("value_text").rlike(r"^\d{4}-\d{2}-\d{2}")
    return long.select(
        record_id_col,
        field_col,
        "value_text",
        F.expr("try_cast(value_text AS DOUBLE)").alias("value_number"),
        F.when(looks_like_date, F.expr("try_cast(value_text AS DATE)")).alias("value_date"),
    )
