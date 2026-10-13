"""
Unit tests for the data services (no web server needed).

Run with:   pytest -q

Each test builds a tiny DataFrame by hand, runs one service, and checks the
result. Reading these is a quick way to see what each service promises.
"""

import pandas as pd
import pytest

from api.services.categorizer import categorize, combine_group, normalize_column
from api.services.cleaning import clean_dataframe
from api.services.file_reader import LoadedDataset, UnsupportedFileError, read_file
from api.services.pricing import build_quote, runtime_cost
from api.services.sample_size import sample_size_snapshot


def _ds(name: str, df: pd.DataFrame) -> LoadedDataset:
    return LoadedDataset(name=name, source_file=name, file_type="CSV", df=df, size_bytes=0)


# ── file_reader ──────────────────────────────────────────────────────────────

def test_read_csv_uses_first_row_as_header_and_trims_names():
    raw = b" Name ,Amount\nAcme,10\nBeta,20\n"
    [ds] = read_file("sales.csv", raw)
    assert list(ds.df.columns) == ["Name", "Amount"]
    assert len(ds.df) == 2


def test_read_cp1252_csv():
    raw = "City,Note\nSan José,café\n".encode("cp1252")
    [ds] = read_file("legacy.csv", raw)
    assert ds.df.iloc[0]["City"] == "San José"


def test_duplicate_headers_are_made_unique():
    # pandas itself renames a repeated CSV header to "id.1".
    [ds] = read_file("dup.csv", b"id,id,name\n1,2,a\n")
    assert list(ds.df.columns) == ["id", "id.1", "name"]


def test_unsupported_extension():
    with pytest.raises(UnsupportedFileError):
        read_file("photo.png", b"...")


# ── cleaning ─────────────────────────────────────────────────────────────────

def test_cleaning_removes_exact_duplicates_and_empty_rows():
    df = pd.DataFrame({
        "customer": ["Ann", "Ann", "Bob", None, "  "],
        "amount":   [10,    10,    None,  None, None],
    })
    cleaned, report = clean_dataframe(df)
    # Row 2 is a duplicate of row 1; rows 4 and 5 are entirely empty
    # (row 5 only had spaces, which count as blank).
    assert report.duplicate_rows_removed == 1
    assert report.empty_rows_removed == 2
    assert report.blank_strings_converted == 1
    assert len(cleaned) == 2
    amount = next(c for c in report.column_reports if c.column == "amount")
    assert amount.missing_count == 1 and amount.missing_pct == 50.0


def test_stacked_files_ignore_source_file_for_empty_and_duplicate_rows():
    df = pd.DataFrame({
        "source_file": ["a.csv", "a.csv", "b.csv"],
        "order_id":    [1,       None,    1],
        "amount":      [5.0,     None,    5.0],
    })
    cleaned, report = clean_dataframe(df)
    assert report.empty_rows_removed == 1       # blank row in a.csv
    assert report.duplicate_rows_removed == 1   # same order in a.csv and b.csv
    assert len(cleaned) == 1


def test_whole_number_columns_lose_the_trailing_decimal():
    df = pd.DataFrame({"customer_id": [1001, None, 1003], "price": [1.5, 2.0, None]})
    cleaned, _ = clean_dataframe(df)
    assert str(cleaned["customer_id"].dtype) == "Int64"
    assert cleaned.to_csv(index=False).splitlines()[1].startswith("1001,")
    assert str(cleaned["price"].dtype) == "float64"   # real decimals stay decimals


def test_drop_any_strategy_removes_rows_with_missing_values():
    df = pd.DataFrame({"a": [1, 2, None], "b": ["x", None, "z"]})
    cleaned, report = clean_dataframe(df, missing_strategy="drop_any")
    assert len(cleaned) == 1
    assert report.rows_dropped_for_missing == 2


def test_cleaning_does_not_modify_the_input():
    df = pd.DataFrame({"a": [" x ", " x "]})
    clean_dataframe(df)
    assert df["a"].tolist() == [" x ", " x "]


# ── categorizer ──────────────────────────────────────────────────────────────

def test_single_file_goes_straight_to_cleaning():
    result = categorize([_ds("one.csv", pd.DataFrame({"a": [1]}))])
    assert result.mode == "single_file"
    assert len(result.groups) == 1


def test_year_partitioned_files_are_grouped():
    cols = {"Order ID": [1], "Order Date": ["2023-01-05"], "Amount": [9.5]}
    a = _ds("sales_2023.csv", pd.DataFrame(cols))
    b = _ds("sales_2024.csv", pd.DataFrame({**cols, "Order Date": ["2024-02-01"]}))
    result = categorize([a, b])
    assert len(result.groups) == 1
    assert result.groups[0].structure == "partitioned_by_year"
    assert result.groups[0].years == [2023, 2024]
    assert result.groups[0].entity == "transactions"


def test_different_entities_are_separated_and_linked():
    tx = _ds("orders.csv", pd.DataFrame({"order_id": [1], "customer_id": [7], "amount": [5.0]}))
    cu = _ds("customers.csv", pd.DataFrame({"customer_id": [7], "email": ["a@b.com"], "first_name": ["A"]}))
    result = categorize([tx, cu])
    assert len(result.groups) == 2
    assert {g.entity for g in result.groups} == {"transactions", "customers"}
    assert result.relationships[0].key_columns == ["customer_id"]


def test_combine_group_stacks_and_tracks_source():
    a = _ds("a_2023.csv", pd.DataFrame({"Customer ID": [1]}))
    b = _ds("b_2024.csv", pd.DataFrame({"customer_id": [2]}))
    combined = combine_group([a, b], ["a_2023.csv", "b_2024.csv"])
    assert list(combined.columns) == ["source_file", "Customer ID"]
    assert combined["source_file"].tolist() == ["a_2023.csv", "b_2024.csv"]


def test_normalize_column():
    assert normalize_column(" Customer-ID ") == "customer_id"


# ── pricing ──────────────────────────────────────────────────────────────────

def test_runtime_is_billed_per_started_minute():
    assert runtime_cost(12)["billable_minutes"] == 1
    assert runtime_cost(61)["billable_minutes"] == 2
    assert runtime_cost(0)["billable_minutes"] == 1


def test_quote_always_includes_cleaning_and_flags_consultant():
    q = build_quote(["data_strategy_discovery"], runtime_seconds=5)
    assert q["lines"][0]["service_id"] == "data_cleaning"
    assert q["requires_consultant"] is True
    assert q["booking_url"]


def test_quote_rejects_unknown_service():
    with pytest.raises(ValueError):
        build_quote(["nope"])


# ── sample size ──────────────────────────────────────────────────────────────

def test_sample_size_snapshot_counts_complete_rows():
    df = pd.DataFrame({"a": range(400), "b": [None] * 100 + list(range(300))})
    snap = sample_size_snapshot(df)
    assert snap["rows"] == 400
    assert snap["complete_rows"] == 300
    verdicts = {c["rows_needed"]: c["verdict"] for c in snap["checks"]}
    assert verdicts[385] == "yellow"   # 300 is short of 385 but more than half
    assert verdicts[100] == "green"
