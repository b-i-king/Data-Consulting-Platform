# PySpark — the optional big-data engine

**pandas is the data engine.** Every upload, cleaning step, skill and
dashboard runs on pandas today, and that does not change. PySpark is set up
*in advance* for the day a client's data is too big for pandas or lives in a
cloud warehouse. It is **off by default** and **not installed** on the
production API.

Code: [`api/services/spark_engine.py`](../api/services/spark_engine.py) ·
Tests: [`tests/test_spark_engine.py`](../tests/test_spark_engine.py) ·
Install file: [`requirements-spark.txt`](../requirements-spark.txt)

## What PySpark is (for a pandas user)

[Apache Spark](https://spark.apache.org/) is a data engine that splits a table
into **partitions** and processes them in parallel — on all the CPU cores of
one machine, or on a cluster of many machines. **PySpark** is its Python API.

| | pandas | PySpark |
|---|---|---|
| Where the data lives | All of it in one machine's RAM | Partitions, streamed through memory, possibly across many machines |
| When work happens | Immediately, line by line | **Lazily**: calls build a plan; an *action* (`.count()`, `.toPandas()`, `.write`) runs it |
| Practical size | Up to a few GB (≈ tens of millions of rows) | Billions of rows / terabytes |
| Start-up cost | None | A Java process; seconds to start |
| Row order | Kept | Not guaranteed (like SQL) |
| Looks like | `df.drop_duplicates()` | `sdf.dropDuplicates()` |
| Runs on | Python | Python + Java 17 (Spark is a JVM program) |

Rule of thumb: if it fits in pandas, pandas is simpler, faster to start and
easier to debug. Spark earns its cost only when the data is big or already
sits in a cloud warehouse.

## When the platform uses Spark vs pandas

`choose_engine(rows, columns, source)` decides, in this order:

| # | Situation | Engine |
|---|---|---|
| 1 | Spark not available (`SPARK_ENABLED` off, or pyspark not installed) | **pandas** |
| 2 | Source is a cloud warehouse: `bigquery`, `snowflake`, `databricks`, `redshift`, `postgres_large` | **spark** |
| 3 | rows × columns **>** `SPARK_MIN_CELLS` (default 50,000,000 ≈ 1–4 GB in pandas) | **spark** |
| 4 | Everything else — uploads, QuickBooks, Google Sheets, normal tables | **pandas** |

Examples (with Spark enabled): a 200,000 × 25 sales export (5,000,000 cells)
→ pandas; a 3,000,000 × 40 transaction log (120,000,000 cells) → spark; any
Snowflake table → spark. With Spark disabled, all of them → pandas (big ones
may then need sampling or a row cap).

## What `spark_engine.py` provides

| Function | What it does |
|---|---|
| `is_available()` | True when `SPARK_ENABLED=true` **and** pyspark is importable (checked without importing it). |
| `choose_engine(rows, columns, source)` | The decision table above. Pure when `available=` / `min_cells=` are passed. |
| `get_spark_session()` | Builds the one shared `SparkSession` from the settings (cached). Raises `SparkUnavailable` with install steps when Spark is off or missing. |
| `read_jdbc(url, table_or_query, properties, ...)` | Reads a table or SELECT from a cloud database; optional parallel read by a partition column. Never logs credentials. |
| `to_pandas_safely(sdf, max_rows)` | Brings at most `max_rows` rows into pandas; `attrs["truncated"]` says if it was cut. |
| `from_pandas(pdf)` | pandas → Spark (NaN becomes null). |
| `clean_with_spark(sdf)` | Cleaning Steps 0–3 from `cleaning.py`: same snake_case column names (reuses `clean_column_names`), trim text / blanks → null, drop all-empty rows, drop exact duplicates. |
| `melt_to_unified(sdf, keep_cols)` | The unified reshape: kept columns (IDs, dates, categories) stay as they are, each number column becomes rows `field_name, source_field, metric_type, metric_subtype, metric_unit, metric_value` — the shape of `warehouse.unified_data` ([DATA_MODEL.md](DATA_MODEL.md)), described like pandas does (`unified.describe_metric_column`). Uses `DataFrame.unpivot` (Spark 3.4+). |

None of these are wired into an HTTP endpoint yet; they are building blocks
for the connector work below.

## How to enable it

1. **Install Java 17** (Spark 4 also accepts Java 21). Check: `java -version`.
   - macOS: `brew install openjdk@17` · Ubuntu: `sudo apt install openjdk-17-jre-headless`
2. **Install PySpark** (pinned in `requirements-spark.txt`):
   ```bash
   pip install -r requirements.txt -r requirements-spark.txt
   ```
3. **Switch it on** in `.env` (or the host's environment):

   | Variable | Default | Meaning |
   |---|---|---|
   | `SPARK_ENABLED` | `false` | Master switch. Off = pandas only, Spark never started. |
   | `SPARK_MASTER` | `local[*]` | `local[*]` = this machine, all cores; or a cluster URL such as `spark://host:7077`. |
   | `SPARK_APP_NAME` | `data-consulting-platform` | Name in the Spark UI / cluster logs. |
   | `SPARK_MIN_CELLS` | `50000000` | Cell count above which `choose_engine` picks Spark. |

4. **Check it:** `python -m pytest -q tests/test_spark_engine.py` — the three
   Spark tests run instead of being skipped.

### Render and hosting — the realistic path

Do **not** install Spark into the Render web service. PySpark adds ~300 MB to
the build, needs Java, and a useful local Spark wants 4–16 GB of RAM; the
Render free/Starter instances (512 MB) cannot run it, and a long Spark job
would block web requests. Options, best first:

1. **Managed Spark next to the client's data (recommended).** Run the Spark job
   on Databricks (any cloud), AWS EMR / EMR Serverless (Redshift clients),
   Google Dataproc Serverless (BigQuery clients), or Snowflake Snowpark
   (Snowflake clients). Data stays in the client's cloud region (faster,
   cheaper egress, easier privacy story); the platform API only starts the job
   and reads back a small summary or a capped sample via `to_pandas_safely`.
2. **A separate worker service** (Render Background Worker or a VM with
   8 GB+ RAM) with Java + `requirements-spark.txt`, `SPARK_ENABLED=true`,
   taking jobs from a queue. Fine for mid-size data; you manage the machine.
3. **Locally**, for a consultant handling one large client file on a laptop.

## Connecting to cloud databases (JDBC)

`read_jdbc` uses Spark's JDBC reader, which works with any database that has a
JDBC driver. The driver jar must be on Spark's classpath; the easiest way is to
let Spark download it when the session starts:

```python
from api.services.spark_engine import get_spark_session, read_jdbc, to_pandas_safely

get_spark_session(extra_conf={"spark.jars.packages": "org.postgresql:postgresql:42.7.4"})
sdf = read_jdbc(
    "jdbc:postgresql://aws-0-us-east-1.pooler.supabase.com:5432/postgres",
    "public.orders",
    {"user": "reader", "password": os.environ["CLIENT_DB_PASSWORD"],
     "driver": "org.postgresql.Driver"},
    partition_column="order_id", lower_bound=1, upper_bound=10_000_000, num_partitions=8,
)
sample = to_pandas_safely(sdf, max_rows=50_000)
```

| Database | `spark.jars.packages` (Maven coordinates) | Driver class |
|---|---|---|
| Postgres / Supabase | `org.postgresql:postgresql:42.7.4` | `org.postgresql.Driver` |
| Amazon Redshift | `com.amazon.redshift:redshift-jdbc42:2.1.0.30` | `com.amazon.redshift.jdbc.Driver` |
| Snowflake | `net.snowflake:snowflake-jdbc:3.19.0` (or the faster `spark-snowflake` connector) | `net.snowflake.client.jdbc.SnowflakeDriver` |
| BigQuery | Use the native `spark-bigquery` connector rather than JDBC (Dataproc includes it). | — |
| Databricks | Run the job *on* Databricks; read tables with `spark.table(...)`. | — |

(Versions are examples — check the latest driver release before use.)

Security rules:

- Credentials go in `properties` (from encrypted storage or env vars), never
  in code, and never in log lines: `read_jdbc` logs only the **redacted** URL
  (`redact_jdbc_url`) and the property *names*.
- Use a **read-only** database user scoped to the tables the client approved.
- Parallel reads open `num_partitions` connections — keep it modest (4–16) so
  the client's database is not overloaded; prefer a read replica.
- Storing anything Spark reads still follows the "no client data stored" rule
  ([ARCHITECTURE.md](ARCHITECTURE.md)); keeping it needs the opt-in managed
  storage design ([ROADMAP.md](ROADMAP.md) item 5).

## Roadmap — when connectors land

1. **Now (done):** optional engine, settings, decision rule, JDBC reader,
   Spark versions of the first cleaning steps and the unified "one row per
   metric" reshape, tests that skip without pyspark.
2. **With the warehouse connectors** (Snowflake / BigQuery / Redshift / large
   Postgres, see [CONNECTORS.md](CONNECTORS.md) and ROADMAP "Other
   connectors"): the connector runs `COUNT(*)` and reads the column list,
   calls `choose_engine(rows, columns, source)`, and either reads with pandas
   (small) or submits a Spark job on the managed service near the warehouse.
3. **Profile in Spark, show in pandas:** compute the cleaning report
   (missing counts, duplicates, types) in Spark; bring a capped sample back
   with `to_pandas_safely` for the on-screen preview and the existing skills.
4. **Load into the warehouse:** `clean_with_spark` → `melt_to_unified` →
   pack the kept columns into `attributes` → write to `warehouse.unified_data`
   (opt-in clients only).
5. **Port more steps** of `cleaning.py` as clients need them (type inference,
   missing-value fills), each with a test comparing the pandas and Spark
   results on the same small table.
