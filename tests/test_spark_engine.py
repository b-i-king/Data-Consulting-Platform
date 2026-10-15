"""
Tests for the optional PySpark engine (api/services/spark_engine.py).

Most tests need NO Spark: engine choice, the on/off switch, the clear error
when Spark is unavailable, and credential redaction are plain Python. The
last tests start a tiny local Spark session and are SKIPPED automatically
when pyspark is not installed (the normal case; see docs/SPARK.md).
"""

import pandas as pd
import pytest

from api.core.config import Settings
from api.services import spark_engine as se

MIN = 50_000_000  # the default SPARK_MIN_CELLS


def test_choose_engine_defaults_to_pandas():
    """
    Small uploads stay on pandas even when Spark is available, and everything
    stays on pandas when Spark is not available (even a warehouse or a huge table).
    """
    assert se.choose_engine(1_000, 20, "upload", available=True, min_cells=MIN) == "pandas"
    assert se.choose_engine(10**9, 100, "upload", available=False, min_cells=MIN) == "pandas"
    assert se.choose_engine(10, 2, "snowflake", available=False, min_cells=MIN) == "pandas"


def test_choose_engine_thresholds():
    """
    Spark is chosen only ABOVE the cell threshold (rows × columns > min_cells),
    and for cloud-warehouse sources whatever their size (case-insensitive).
    """
    # exactly at the threshold -> pandas; one row more -> spark
    assert se.choose_engine(5_000_000, 10, None, available=True, min_cells=MIN) == "pandas"
    assert se.choose_engine(5_000_001, 10, None, available=True, min_cells=MIN) == "spark"
    for source in ["bigquery", "Snowflake", " databricks ", "redshift", "postgres_large"]:
        assert se.choose_engine(10, 2, source, available=True, min_cells=MIN) == "spark"
    assert se.choose_engine(10, 2, "quickbooks", available=True, min_cells=MIN) == "pandas"
    # bad (negative) sizes never trigger Spark
    assert se.choose_engine(-5, 10, None, available=True, min_cells=MIN) == "pandas"


def test_is_available_false_when_disabled():
    """SPARK_ENABLED off (the default) means Spark is never used, installed or not."""
    assert Settings().spark_enabled is False
    assert se.is_available(Settings(spark_enabled=False)) is False


def test_is_available_needs_the_package(monkeypatch):
    """Switched on but pyspark not installed -> still not available."""
    monkeypatch.setattr(se, "pyspark_installed", lambda: False)
    assert se.is_available(Settings(spark_enabled=True)) is False


def test_get_spark_session_raises_when_disabled():
    """Asking for Spark while it is off raises SparkUnavailable with install help."""
    with pytest.raises(se.SparkUnavailable) as err:
        se.get_spark_session(Settings(spark_enabled=False))
    assert "requirements-spark.txt" in str(err.value)
    assert "SPARK_ENABLED" in str(err.value)


def test_get_spark_session_raises_when_not_installed(monkeypatch):
    """Enabled but pyspark missing -> SparkUnavailable, not an ImportError."""
    monkeypatch.setattr(se, "pyspark_installed", lambda: False)
    with pytest.raises(se.SparkUnavailable, match="not installed"):
        se.get_spark_session(Settings(spark_enabled=True))


def test_module_does_not_import_pyspark():
    """Importing the module must not import pyspark (it is optional and slow to load)."""
    import sys

    if "pyspark" in sys.modules:
        pytest.skip("pyspark already imported by another test")
    import importlib

    importlib.reload(se)
    assert "pyspark" not in sys.modules


def test_redact_jdbc_url_hides_secrets():
    """Passwords in either URL style are replaced by *** before logging."""
    url = "jdbc:postgresql://bob:s3cret@db.example.com:5432/app?password=x&ssl=true"
    safe = se.redact_jdbc_url(url)
    assert "s3cret" not in safe and "password=x" not in safe
    assert safe == "jdbc:postgresql://***@db.example.com:5432/app?password=***&ssl=true"
    snow = "jdbc:snowflake://acct.snowflakecomputing.com/?user=ann&private_key=ABC&db=sales"
    assert "ABC" not in se.redact_jdbc_url(snow)
    assert "user=ann" in se.redact_jdbc_url(snow)


def test_read_jdbc_rejects_partial_partition_options():
    """Partitioned reads need all four options; a partial set is a clear ValueError."""
    with pytest.raises(ValueError, match="all four"):
        se.read_jdbc("jdbc:postgresql://h/db", "orders", partition_column="id")


# ── Tests that start a real (local) Spark session — skipped without pyspark ──

@pytest.fixture(scope="module")
def spark_settings():
    """
    Skip unless pyspark is installed; yield settings with Spark switched on,
    and stop the local session afterwards.
    """
    pytest.importorskip("pyspark")
    settings = Settings(spark_enabled=True, spark_master="local[1]", spark_app_name="pytest")
    try:
        se.get_spark_session(settings)
    except Exception as exc:  # e.g. Java missing on this machine
        pytest.skip(f"Spark could not start: {exc}")
    yield settings
    se.stop_spark_session()


def test_clean_with_spark_matches_pandas_naming(spark_settings):
    """
    Column names follow cleaning.py (snake_case, unique), text is trimmed with
    blanks -> null, all-empty rows and exact duplicates are dropped.
    """
    pdf = pd.DataFrame({
        "Order Date": ["2026-01-02", "2026-01-02", None, "2026-01-03"],
        "CustomerID": [" Acme ", "Acme", None, "  "],
        "Amount": [10.0, 10.0, None, 5.0],
    })
    sdf = se.clean_with_spark(se.from_pandas(pdf, spark_settings))
    assert sdf.columns == ["order_date", "customer_id", "amount"]
    out = se.to_pandas_safely(sdf, max_rows=100).sort_values("order_date").reset_index(drop=True)
    assert len(out) == 2                              # duplicate and empty row gone
    assert out.loc[0, "customer_id"] == "Acme"        # trimmed
    assert pd.isna(out.loc[1, "customer_id"])         # blank -> missing
    assert out.attrs["truncated"] is False


def test_to_pandas_safely_caps_rows(spark_settings):
    """A table bigger than max_rows comes back cut, and says so."""
    sdf = se.get_spark_session(spark_settings).range(50)
    out = se.to_pandas_safely(sdf, max_rows=10)
    assert len(out) == 10 and out.attrs["truncated"] is True


def test_melt_to_unified(spark_settings):
    """One row per field, with typed number/date copies and nulls dropped."""
    pdf = pd.DataFrame({
        "order_id": [1, 2],
        "amount": [9.5, None],
        "order_date": ["2026-10-15", "2026-10-16"],
        "note": ["rush", "2026"],
    })
    long = se.melt_to_unified(se.from_pandas(pdf, spark_settings), ["order_id"])
    assert long.columns == ["record_id", "field_name", "value_text", "value_number", "value_date"]
    rows = {(r.record_id, r.field_name): r for r in long.collect()}
    assert len(rows) == 5                                   # order 2's missing amount dropped
    assert rows[("1", "amount")].value_number == 9.5
    assert str(rows[("1", "order_date")].value_date) == "2026-10-15"
    assert rows[("1", "note")].value_number is None
    assert rows[("2", "note")].value_date is None           # "2026" is not read as a date
    with pytest.raises(ValueError):
        se.melt_to_unified(se.from_pandas(pdf, spark_settings), [])
