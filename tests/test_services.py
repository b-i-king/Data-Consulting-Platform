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


def test_missing_value_advice_levels_and_fill_suggestions():
    n = 20
    df = pd.DataFrame({
        "row_id":    range(1, n + 1),   # keeps rows unique, so none are removed as duplicates
        "symmetric": [10.0, 11, 9, 10, 12, 8, 10, 11, 9, 10, 10, 11, 9, 10, 12, 8, 10, 11, 9, None],  # 5% missing
        "skewed":    [1.0, 1, 1, 2, 1, 2, 1, 1, 3, 1, 2, 1, 1, 50, 1, 2, None, None, None, None],     # 20%, long tail
        "city":      ["Atlanta"] * 12 + ["Decatur"] * 2 + [None] * 6,                               # 30% text
        "notes":     ["x"] + [None] * (n - 1),                                                      # 95%
    })
    _, report = clean_dataframe(df)
    by = {c.column: c for c in report.column_reports}
    assert (by["symmetric"].missing_level, by["symmetric"].suggested_fill) == ("low", "mean")
    assert (by["skewed"].missing_level, by["skewed"].suggested_fill) == ("review", "median")
    assert by["skewed"].skewness > 0.5
    assert (by["city"].missing_level, by["city"].suggested_fill) == ("review", "mode")
    assert by["city"].fill_options == {"mode": "Atlanta"}
    assert by["notes"].missing_level == "remove"
    assert report.columns_to_review == ["skewed", "city"] and report.columns_to_remove == ["notes"]
    assert not report.consultation_recommended


def test_fill_and_drop_choices_change_only_what_was_asked():
    df = pd.DataFrame({"qty": [1, 2, None, 4], "price": [1.5, None, 2.5, 3.0],
                       "city": ["A", "A", None, "B"], "notes": [None, None, None, "x"]})
    cleaned, report = clean_dataframe(
        df, column_actions={"qty": "mean", "price": "median", "city": "auto", "notes": "drop"})
    assert cleaned["qty"].tolist() == [1, 2, 2, 4]          # mean 2.33 rounded: whole numbers stay whole
    assert cleaned["price"].tolist() == [1.5, 2.5, 2.5, 3.0]
    assert cleaned["city"].tolist() == ["A", "A", "A", "B"]  # auto = mode for text
    assert "notes" not in cleaned.columns and report.columns_dropped == ["notes"]
    assert report.columns == 3 and report.columns_before == 4 and report.cells_filled == 3
    qty = next(c for c in report.column_reports if c.column == "qty")
    assert (qty.missing_count, qty.action, qty.filled_count, qty.missing_after) == (1, "mean", 1, 0)
    # The caller's data is untouched, and "keep" is the default.
    assert df["qty"].isna().sum() == 1
    assert clean_dataframe(df)[0]["qty"].isna().sum() == 1


def test_fills_happen_before_dropping_rows_and_bad_actions_are_refused():
    df = pd.DataFrame({"a": [1.0, None, 3.0], "b": ["x", "y", None]})
    cleaned, report = clean_dataframe(df, missing_strategy="drop_any", column_actions={"a": "median"})
    assert len(cleaned) == 2 and report.rows_dropped_for_missing == 1   # only b's gap drops a row
    with pytest.raises(ValueError):
        clean_dataframe(df, column_actions={"a": "average"})


def test_mostly_empty_data_recommends_a_consultation():
    # 10 columns, each row has just one value: 90% of cells are empty.
    df = pd.DataFrame([[i if j == i else None for j in range(10)] for i in range(10)],
                      columns=[f"c{j}" for j in range(10)])
    _, report = clean_dataframe(df)
    assert report.missing_pct_before >= 90 and report.consultation_recommended


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


def test_no_shared_id_suggests_fuzzy_link():
    sales = _ds("sales.csv", pd.DataFrame({"order_no": [1], "customer": ["Acme Bakery"], "amount": [5.0]}))
    qb = _ds("quickbooks_customers.csv", pd.DataFrame({"DisplayName": ["ACME BAKERY, LLC"], "Balance": [0]}))
    result = categorize([sales, qb])
    [rel] = result.relationships
    assert rel.method == "fuzzy" and rel.key_columns == ["name"]


def test_id_columns_are_never_filled():
    df = pd.DataFrame({"order_id": range(10), "customer_id": [1001, None, 1003, None, 1005, 1006, None, 1008, 1009, 1010]})
    cleaned, report = clean_dataframe(df, column_actions={"customer_id": "mean"})
    cid = next(c for c in report.column_reports if c.column == "customer_id")
    assert cid.fill_options == {} and cid.suggested_fill is None and "ID" in cid.recommendation
    assert cleaned["customer_id"].isna().sum() == 3            # the request is refused, gaps kept
    assert any("IDs" in n for n in report.notes)
