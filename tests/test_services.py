"""
Unit tests for the data services (no web server needed).

Run with:   pytest -q

Each test builds a tiny DataFrame by hand, runs one service, and checks the
result. Reading these is a quick way to see what each service promises.
"""

import pandas as pd
import pytest

from api.services.categorizer import categorize, combine_group, normalize_column
from api.services.cleaning import clean_column_names, clean_dataframe
from api.services.file_reader import LoadedDataset, UnsupportedFileError, read_file
from api.services.pricing import build_quote, download_estimate, runtime_cost, wait_label
from api.services.sample_size import sample_size_snapshot


def _ds(name: str, df: pd.DataFrame) -> LoadedDataset:
    """Wrap a DataFrame as a LoadedDataset, as if it had been read from a CSV upload.

    Args:
        name: str, the file name; used for both the dataset name and its source file.
        df: pd.DataFrame, the table the "file" holds.

    Returns:
        LoadedDataset: a dataset with file_type "CSV" and size_bytes 0.

    Example:
        >>> ds = _ds("one.csv", pd.DataFrame({"a": [1]}))
        >>> ds.source_file
        'one.csv'
    """
    return LoadedDataset(name=name, source_file=name, file_type="CSV", df=df, size_bytes=0)


# ── file_reader ──────────────────────────────────────────────────────────────

def test_read_csv_uses_first_row_as_header_and_trims_names():
    """Check the first CSV row becomes the header and spaces around names are trimmed."""
    raw = b" Name ,Amount\nAcme,10\nBeta,20\n"
    [ds] = read_file("sales.csv", raw)
    assert list(ds.df.columns) == ["Name", "Amount"]
    assert len(ds.df) == 2


def test_read_cp1252_csv():
    """Check a Windows (cp1252) encoded CSV reads accented text correctly."""
    raw = "City,Note\nSan José,café\n".encode("cp1252")
    [ds] = read_file("legacy.csv", raw)
    assert ds.df.iloc[0]["City"] == "San José"


def test_duplicate_headers_are_made_unique():
    """Check repeated headers get "_2" and blank headers get "unnamed_column_<position>"."""
    # pandas renames a repeated CSV header to "id.1"; we make it a clear second "id".
    [ds] = read_file("dup.csv", b"id,id,name\n1,2,a\n")
    assert list(ds.df.columns) == ["id", "id_2", "name"]
    # Blank headers get their position, so the client can find them.
    [ds] = read_file("blank.csv", b"a,,amount,amount,\n1,2,3,4,5\n")
    assert list(ds.df.columns) == ["a", "unnamed_column_2", "amount", "amount_2", "unnamed_column_5"]


def test_unsupported_extension():
    """Check an unsupported file type (.png) raises UnsupportedFileError."""
    with pytest.raises(UnsupportedFileError):
        read_file("photo.png", b"...")


# ── cleaning ─────────────────────────────────────────────────────────────────

def test_cleaning_removes_exact_duplicates_and_empty_rows():
    """Check cleaning removes duplicate and empty rows, treats spaces as blank, and counts missing values."""
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
    """Check the added source_file column is ignored when finding empty and duplicate rows."""
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
    """Check whole-number columns with gaps become Int64 (no ".0") while real decimals stay float."""
    df = pd.DataFrame({"customer_id": [1001, None, 1003], "price": [1.5, 2.0, None]})
    cleaned, _ = clean_dataframe(df)
    assert str(cleaned["customer_id"].dtype) == "Int64"
    assert cleaned.to_csv(index=False).splitlines()[1].startswith("1001,")
    assert str(cleaned["price"].dtype) == "float64"   # real decimals stay decimals


def test_drop_any_strategy_removes_rows_with_missing_values():
    """Check missing_strategy="drop_any" removes every row with a missing value and counts them."""
    df = pd.DataFrame({"a": [1, 2, None], "b": ["x", None, "z"]})
    cleaned, report = clean_dataframe(df, missing_strategy="drop_any")
    assert len(cleaned) == 1
    assert report.rows_dropped_for_missing == 2


def test_missing_value_advice_levels_and_fill_suggestions():
    """Check each column's missing level (low / review / remove) and suggested fill (mean, median, mode)."""
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
    """Check per-column fill and drop choices apply as asked, are reported, and leave the input untouched."""
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
    """Check fills run before drop_any removes rows, and an unknown action raises ValueError."""
    df = pd.DataFrame({"a": [1.0, None, 3.0], "b": ["x", "y", None]})
    cleaned, report = clean_dataframe(df, missing_strategy="drop_any", column_actions={"a": "median"})
    assert len(cleaned) == 2 and report.rows_dropped_for_missing == 1   # only b's gap drops a row
    with pytest.raises(ValueError):
        clean_dataframe(df, column_actions={"a": "average"})


def test_mostly_empty_data_recommends_a_consultation():
    """Check a table that is about 90% empty recommends a consultation."""
    # 10 columns, each row has just one value: 90% of cells are empty.
    df = pd.DataFrame([[i if j == i else None for j in range(10)] for i in range(10)],
                      columns=[f"c{j}" for j in range(10)])
    _, report = clean_dataframe(df)
    assert report.missing_pct_before >= 90 and report.consultation_recommended


def test_cleaning_does_not_modify_the_input():
    """Check clean_dataframe works on a copy and leaves the caller's DataFrame unchanged."""
    df = pd.DataFrame({"a": [" x ", " x "]})
    clean_dataframe(df)
    assert df["a"].tolist() == [" x ", " x "]


# ── categorizer ──────────────────────────────────────────────────────────────

def test_single_file_goes_straight_to_cleaning():
    """Check a single upload is categorized as "single_file" with one group."""
    result = categorize([_ds("one.csv", pd.DataFrame({"a": [1]}))])
    assert result.mode == "single_file"
    assert len(result.groups) == 1


def test_year_partitioned_files_are_grouped():
    """Check same-shape files split by year are grouped as one partitioned transactions group."""
    cols = {"Order ID": [1], "Order Date": ["2023-01-05"], "Amount": [9.5]}
    a = _ds("sales_2023.csv", pd.DataFrame(cols))
    b = _ds("sales_2024.csv", pd.DataFrame({**cols, "Order Date": ["2024-02-01"]}))
    result = categorize([a, b])
    assert len(result.groups) == 1
    assert result.groups[0].structure == "partitioned_by_year"
    assert result.groups[0].years == [2023, 2024]
    assert result.groups[0].entity == "transactions"


def test_different_entities_are_separated_and_linked():
    """Check orders and customers become two groups linked by customer_id."""
    tx = _ds("orders.csv", pd.DataFrame({"order_id": [1], "customer_id": [7], "amount": [5.0]}))
    cu = _ds("customers.csv", pd.DataFrame({"customer_id": [7], "email": ["a@b.com"], "first_name": ["A"]}))
    result = categorize([tx, cu])
    assert len(result.groups) == 2
    assert {g.entity for g in result.groups} == {"transactions", "customers"}
    assert result.relationships[0].key_columns == ["customer_id"]


def test_combine_group_stacks_and_tracks_source():
    """Check combine_group stacks files with matching normalized columns and adds a source_file column."""
    a = _ds("a_2023.csv", pd.DataFrame({"Customer ID": [1]}))
    b = _ds("b_2024.csv", pd.DataFrame({"customer_id": [2]}))
    combined = combine_group([a, b], ["a_2023.csv", "b_2024.csv"])
    assert list(combined.columns) == ["source_file", "Customer ID"]
    assert combined["source_file"].tolist() == ["a_2023.csv", "b_2024.csv"]


def test_normalize_column():
    """Check normalize_column trims, lowercases and turns separators into underscores."""
    assert normalize_column(" Customer-ID ") == "customer_id"


# ── pricing ──────────────────────────────────────────────────────────────────

def test_runtime_is_billed_per_started_minute():
    """Check runtime is billed per started minute, with a one-minute minimum."""
    assert runtime_cost(12)["billable_minutes"] == 1
    assert runtime_cost(61)["billable_minutes"] == 2
    assert runtime_cost(0)["billable_minutes"] == 1


def test_quote_always_includes_cleaning_and_flags_consultant():
    """Check a quote always starts with Data Cleaning and flags services needing a consultant."""
    q = build_quote(["data_strategy_discovery"], runtime_seconds=5)
    assert q["lines"][0]["service_id"] == "data_cleaning"
    assert q["requires_consultant"] is True
    assert q["booking_url"]


def test_quote_rejects_unknown_service():
    """Check build_quote raises ValueError for an unknown service id."""
    with pytest.raises(ValueError):
        build_quote(["nope"])


# ── sample size ──────────────────────────────────────────────────────────────

def test_sample_size_snapshot_counts_complete_rows():
    """Check the sample-size snapshot counts complete rows and gives green / yellow verdicts."""
    df = pd.DataFrame({"a": range(400), "b": [None] * 100 + list(range(300))})
    snap = sample_size_snapshot(df)
    assert snap["rows"] == 400
    assert snap["complete_rows"] == 300
    verdicts = {c["rows_needed"]: c["verdict"] for c in snap["checks"]}
    assert verdicts[385] == "yellow"   # 300 is short of 385 but more than half
    assert verdicts[100] == "green"


def test_no_shared_id_suggests_fuzzy_link():
    """Check files with no shared ID column are linked by a fuzzy name match."""
    sales = _ds("sales.csv", pd.DataFrame({"order_no": [1], "customer": ["Acme Bakery"], "amount": [5.0]}))
    qb = _ds("quickbooks_customers.csv", pd.DataFrame({"DisplayName": ["ACME BAKERY, LLC"], "Balance": [0]}))
    result = categorize([sales, qb])
    [rel] = result.relationships
    assert rel.method == "fuzzy" and rel.key_columns == ["name"]


def test_id_columns_are_never_filled():
    """Check ID columns are never filled (the request is refused) and dropping rows is suggested instead."""
    df = pd.DataFrame({"order_id": range(10), "customer_id": [1001, None, 1003, None, 1005, 1006, None, 1008, 1009, 1010]})
    cleaned, report = clean_dataframe(df, column_actions={"customer_id": "mean"})
    cid = next(c for c in report.column_reports if c.column == "customer_id")
    # Never filled — the suggestion is to remove the rows with no ID instead.
    assert cid.fill_options == {} and cid.suggested_fill == "drop_rows" and "ID" in cid.recommendation
    assert cleaned["customer_id"].isna().sum() == 3            # the request is refused, gaps kept
    assert any("IDs" in n for n in report.notes)


def test_download_estimate_scales_with_rows_times_fields():
    """Check the download time estimate grows with rows x columns, Excel is slower than CSV, and the
    charge is processing time only.
    """
    small = download_estimate(0.2, rows=100, columns=5)
    big = download_estimate(0.2, rows=400_000, columns=20)
    assert small["cells"] == 500 and big["cells"] == 8_000_000
    # Excel is slower to write than CSV, and more cells take longer.
    assert big["formats"]["xlsx"]["estimated_seconds"] > big["formats"]["csv"]["estimated_seconds"]
    assert big["formats"]["csv"]["estimated_seconds"] > small["formats"]["csv"]["estimated_seconds"]
    assert small["formats"]["csv"]["under_five_minutes"]
    assert not big["formats"]["xlsx"]["under_five_minutes"]   # 8M cells / 20k per second = 400 s
    # The charge is the processing time only (1 started minute here).
    assert small["amount_usd"] == runtime_cost(0.2)["amount_usd"]


def test_wait_label_reads_like_plain_english():
    """Check wait_label turns seconds into plain-English waits ("about 2 minutes")."""
    assert wait_label(4) == "under 10 seconds"
    assert wait_label(34) == "about 40 seconds"
    assert wait_label(61) == "about 2 minutes"


def test_column_names_become_lowercase_snake_case():
    """Check column names become unique lowercase snake_case, and renames are reported."""
    assert clean_column_names(["Order Date", "Unit Price ($)", "CustomerID", "DisplayName", "Café Name",
                               "amount", "Amount", "", "2024 Sales", "  e-mail  "]) == [
        "order_date", "unit_price", "customer_id", "display_name", "cafe_name",
        "amount", "amount_2", "column_8", "col_2024_sales", "e_mail"]
    df = pd.DataFrame({"Order ID": range(3), "Total Amount": [1.0, 2.0, 3.0], "source_file": ["a.csv"] * 3})
    cleaned, report = clean_dataframe(df)
    assert list(cleaned.columns) == ["order_id", "total_amount", "source_file"]   # ours keeps its name
    assert report.columns_renamed == {"Order ID": "order_id", "Total Amount": "total_amount"}
    assert report.column_reports[0].column == "order_id" and report.column_reports[0].is_id


def test_rows_missing_an_id_can_be_removed_and_are_set_aside():
    """Check "drop_rows" on an ID column removes rows with no ID, keeps them aside, and reports them."""
    df = pd.DataFrame({"Order ID": [1, None, 3, None, 5], "amount": [10, 20, 30, 40, 50]})
    # Choices use the CLEANED column names (the ones the report shows).
    cleaned, report = clean_dataframe(df, column_actions={"order_id": "drop_rows"})
    assert cleaned["order_id"].tolist() == [1, 3, 5]
    assert report.rows_missing_id_removed == 2
    assert report.rows_missing_id["amount"].tolist() == [20, 40]     # kept for the Excel sheet
    oid = report.column_reports[0]
    assert oid.action == "rows_dropped" and oid.rows_dropped == 2 and oid.filled_count == 0
    assert report.cells_filled == 0
    assert any("rows_missing_id" in n for n in report.notes)
    d = report.to_dict()                                              # JSON report leaves the DataFrame out
    assert "rows_missing_id" not in d and d["rows_missing_id_removed"] == 2


def test_blank_and_repeated_headers_are_flagged_until_fixed():
    """Check blank and repeated headers are flagged, and clear once the client renames or drops them."""
    [ds] = read_file("orders.csv", b"Order ID,,Amount,Amount,\n1,x,3,4,\n2,y,5,6,\n")
    cleaned, report = clean_dataframe(ds.df)
    assert list(cleaned.columns) == ["order_id", "unnamed_column_2", "amount", "amount_2", "unnamed_column_5"]
    assert report.unnamed_columns == ["unnamed_column_2", "unnamed_column_5"]
    assert report.duplicate_columns == [["amount", "amount_2"]]
    by = {c.column: c for c in report.column_reports}
    assert by["unnamed_column_2"].name_problem == "unnamed" and by["unnamed_column_2"].original_name == ""
    assert by["amount_2"].name_problem == "duplicate" and by["amount_2"].original_name == "Amount"
    assert any("no name" in n for n in report.notes) and any("same name" in n for n in report.notes)

    # The client names one blank column and tells the two amounts apart.
    # Choices made under the old (automatic) names still apply.
    cleaned, report = clean_dataframe(
        ds.df,
        column_names={"unnamed_column_2": "Store Code", "amount_2": "Refund Amount"},
        column_actions={"unnamed_column_5": "drop"},
    )
    assert list(cleaned.columns) == ["order_id", "store_code", "amount", "refund_amount"]
    assert report.unnamed_columns == []          # one named, the other removed: both fixed
    assert report.duplicate_columns == []
    by = {c.column: c for c in report.column_reports}
    assert by["refund_amount"].key == "amount_2" and by["refund_amount"].name_problem is None
    assert by["unnamed_column_5"].action == "dropped"


def test_client_names_must_be_usable_and_unique():
    """Check client-chosen column names must be unique and contain usable characters."""
    df = pd.DataFrame({"a": [1], "b": [2]})
    with pytest.raises(ValueError, match="share a name"):
        clean_dataframe(df, column_names={"b": "A"})
    with pytest.raises(ValueError, match="can't be used"):
        clean_dataframe(df, column_names={"b": "  $$ "})
