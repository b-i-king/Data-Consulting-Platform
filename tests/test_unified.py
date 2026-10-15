"""
Tests for the unified "one row per field" data layer (api/services/unified.py).

They cover the owner's two promises:
1. Any source fits one long table: QuickBooks Online JSON (fixtures shaped like
   real API v3 responses, fictional bakery data) and uploaded files both turn
   into the same rows, and those rows pivot back into the original table.
2. No duplicate data: loading the same data again inserts nothing and changes
   nothing; a changed value is an update; identical rows count once.
It also checks that the Python field maps and the warehouse.field_map seed in
db/warehouse.sql stay identical.
"""

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from api.services.unified import (
    FIELD_MAPS,
    KEY_COLUMNS,
    QUICKBOOKS_ENTITIES,
    UNIFIED_COLUMNS,
    dataframe_to_unified,
    flatten_quickbooks,
    plan_upsert,
    quickbooks_response_to_unified,
    snake_case,
    unified_to_wide,
    value_hash,
)

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).resolve().parent / "fixtures"


def _load(name: str) -> dict:
    """Read a JSON fixture from tests/fixtures/."""
    return json.loads((FIXTURES / name).read_text())


def _invoice_130() -> dict:
    """Return the first invoice in the QuickBooks invoice fixture (2 item lines + a subtotal line)."""
    return _load("quickbooks_invoice.json")["QueryResponse"]["Invoice"][0]


def _value(rows: pd.DataFrame, line: int, field: str):
    """Return the one stored value of `field` on `line` (whichever value_* column holds it)."""
    row = rows[(rows["line_number"] == line) & (rows["field_name"] == field)]
    assert len(row) == 1, f"expected one {field!r} on line {line}, got {len(row)}"
    row = row.iloc[0]
    return row[{"text": "value_text", "number": "value_number", "date": "value_date",
                "timestamp": "value_timestamp", "boolean": "value_bool"}[row["value_type"]]]


# ── QuickBooks ──────────────────────────────────────────────────────────────

def test_flatten_invoice_header_and_lines():
    """An invoice becomes header rows (line 0) plus rows per Line item, with mapped names and typed values."""
    rows = pd.DataFrame(flatten_quickbooks("Invoice", _invoice_130()))

    assert set(rows["record_id"]) == {"Invoice:130"}
    assert set(rows["line_number"]) == {0, 1, 2, 3}            # header + 2 items + subtotal line
    assert set(rows["event_type"]) == {"invoice"} and set(rows["event_subtype"]) == {"sent"}
    assert set(rows["event_date"].astype(str)) == {"2026-09-14"}
    # source_modified_at = MetaData.LastUpdatedTime, in UTC.
    assert str(rows["source_modified_at"].iloc[0]) == "2026-09-15 20:05:47+00:00"

    assert _value(rows, 0, "customer_name") == "Blue Door Cafe"
    assert _value(rows, 0, "total_amount") == 181.44
    assert str(_value(rows, 0, "due_date")) == "2026-10-14"
    assert _value(rows, 1, "item_name") == "Sourdough Loaf"
    assert _value(rows, 1, "quantity") == 12
    assert _value(rows, 2, "line_amount") == 72.0
    assert _value(rows, 3, "line_detail_type") == "SubTotalLineDetail"

    # API bookkeeping keys are not business data.
    assert not rows["source_field"].isin(["domain", "sparse"]).any()
    # The unique key holds: no field appears twice on one line of one record.
    assert not rows.duplicated(subset=KEY_COLUMNS).any()
    assert list(pd.DataFrame(rows).columns) == UNIFIED_COLUMNS


def test_unknown_quickbooks_fields_are_kept_and_flagged():
    """Fields no map knows are kept under a snake_case name with mapped=False, so nothing is lost."""
    rows = pd.DataFrame(flatten_quickbooks("Invoice", _invoice_130()))
    custom = rows[rows["source_field"] == "CustomField[0].StringValue"].iloc[0]
    assert custom["field_name"] == "custom_field_string_value"
    assert custom["value_text"] == "6-8 AM"
    assert not custom["mapped"]
    assert rows.loc[rows["field_name"] == "customer_name", "mapped"].all()


def test_purchase_maps_to_expense_and_aligns_names():
    """Purchases are transaction/expense; vendor, category and location names line up with other sources."""
    long = quickbooks_response_to_unified(_load("quickbooks_purchase.json"))
    assert set(long["record_id"]) == {"Purchase:252", "Purchase:253"}
    assert set(zip(long["event_type"], long["event_subtype"])) == {("transaction", "expense")}
    second = long[long["record_id"] == "Purchase:253"]
    assert _value(second, 0, "payee_name") == "Midtown Restaurant Depot"
    assert _value(second, 0, "location_name") == "Midtown"         # DepartmentRef.name
    assert _value(second, 1, "item_name") == "Cake Box 10in"        # item-based line
    assert _value(second, 2, "expense_category") == "Supplies & Materials"   # account-based line


def test_second_list_item_gets_numbered_name():
    """A 2nd LinkedTxn on one record gets linked_txn_id_2 instead of colliding with the 1st."""
    invoice = {"Id": "9", "TxnDate": "2026-09-01",
               "LinkedTxn": [{"TxnId": "1", "TxnType": "Payment"}, {"TxnId": "2", "TxnType": "Payment"}]}
    names = {r["field_name"] for r in flatten_quickbooks("Invoice", invoice)}
    assert {"linked_txn_id", "linked_txn_id_2"} <= names


def test_unknown_entity_and_missing_id_are_rejected():
    """Only known entities with an Id can be flattened (the Id is the record_id)."""
    with pytest.raises(ValueError):
        flatten_quickbooks("Estimate", {"Id": "1"})
    with pytest.raises(ValueError):
        flatten_quickbooks("Invoice", {"TxnDate": "2026-09-01"})


def test_quickbooks_wide_has_one_row_per_line():
    """Pivoting a QuickBooks response back gives one row per record + line."""
    wide = unified_to_wide(quickbooks_response_to_unified(_load("quickbooks_invoice.json")))
    assert len(wide) == 7          # invoice 130: 4 rows, invoice 131: 3 rows
    header = wide[(wide["record_id"] == "Invoice:130") & (wide["line_number"] == 0)].iloc[0]
    assert header["total_amount"] == 181.44
    assert str(header["transaction_date"].date()) == "2026-09-14"


def test_every_entity_uses_an_existing_event_type():
    """Each QuickBooks entity points at a (type, subtype) pair seeded in dim_event_type."""
    sql = (ROOT / "db" / "warehouse.sql").read_text()
    seeded = set(re.findall(r"\('(\w+)',\s*'(\w+)',\s*'(?:amount|quantity|score|count)'", sql))
    for event_type, event_subtype, _ in QUICKBOOKS_ENTITIES.values():
        assert (event_type, event_subtype) in seeded


# ── Uploaded files ──────────────────────────────────────────────────────────

SALES = pd.DataFrame({
    "order_id": [50001, 50002, 50003],
    "order_date": pd.to_datetime(["2026-09-01", "2026-09-01", "2026-09-02"]),
    "product": ["Sourdough Loaf", "Cupcake Box (6)", "Rye Bread"],
    "quantity": [3, 2, 1],
    "amount": [21.0, 36.0, 6.5],
    "loyalty_member": [True, False, True],
    "note": ["", "gift", None],
})


def test_file_round_trip_long_to_wide():
    """A cleaned file -> long -> wide gives back the same table (empty cells come back empty)."""
    long = dataframe_to_unified(SALES, event_type="transaction", event_subtype="income",
                                date_column="order_date", id_column="order_id")
    assert set(long["record_id"]) == {"50001", "50002", "50003"}
    assert set(long["line_number"]) == {0}
    assert long.loc[long["source_field"] == "product", "field_name"].iloc[0] == "item_name"
    assert not long.loc[long["source_field"] == "note", "mapped"].iloc[0]

    wide = unified_to_wide(long, use_source_names=True)
    assert list(wide.columns) == ["record_id", "line_number"] + list(SALES.columns)

    def normalized(values):
        """Missing and empty text both mean "no value"; 3 == 3.0 and Timestamps compare by value."""
        return [None if (v is None or v == "" or (not isinstance(v, str) and pd.isna(v))) else v
                for v in values]

    for column in SALES.columns:
        assert normalized(wide[column].tolist()) == normalized(SALES[column].tolist()), column
    # And the numbers really are numbers again.
    assert wide["amount"].sum() == pytest.approx(63.5)
    assert pd.api.types.is_datetime64_any_dtype(wide["order_date"])


def test_rows_without_id_get_a_stable_content_hash():
    """No ID column: the same row gets the same record_id, in any file and any row order."""
    first = dataframe_to_unified(SALES.drop(columns="order_id"), event_type="transaction",
                                 event_subtype="income", date_column="order_date")
    shuffled = SALES.drop(columns="order_id").iloc[::-1][["amount", "product", "order_date",
                                                         "quantity", "loyalty_member", "note"]]
    second = dataframe_to_unified(shuffled, event_type="transaction",
                                  event_subtype="income", date_column="order_date")
    assert set(first["record_id"]) == set(second["record_id"])
    assert first["record_id"].nunique() == 3
    assert all(r.startswith("sha256:") for r in first["record_id"])


def test_identical_rows_collapse_unless_asked_to_keep():
    """Two identical rows share one record_id; keep_identical_rows numbers the copy instead."""
    twice = pd.concat([SALES.iloc[[0]], SALES.iloc[[0]]]).drop(columns="order_id")
    collapsed = dataframe_to_unified(twice, event_type="transaction", event_subtype="income",
                                     date_column="order_date")
    assert collapsed["record_id"].nunique() == 1
    kept = dataframe_to_unified(twice, event_type="transaction", event_subtype="income",
                                date_column="order_date", keep_identical_rows=True)
    assert kept["record_id"].nunique() == 2
    assert any(r.endswith("#2") for r in kept["record_id"])


def test_repeated_ids_become_line_numbers():
    """A file with one row per invoice line (ID repeats) numbers the lines 1, 2, ..."""
    lines = pd.DataFrame({"invoice_number": ["1037", "1037", "1038"], "amount": [96.0, 72.0, 65.0]})
    long = dataframe_to_unified(lines, event_type="invoice", event_subtype="sent",
                                date_column=None, id_column="invoice_number")
    pairs = set(zip(long["record_id"], long["line_number"]))
    assert pairs == {("1037", 1), ("1037", 2), ("1038", 1)}


def test_two_columns_on_one_field_are_refused():
    """Two columns that align to the same field would break the unique key, so it is an error."""
    both = pd.DataFrame({"store": ["Midtown"], "location": ["Decatur"]})
    with pytest.raises(ValueError, match="location_name"):
        dataframe_to_unified(both, event_type="transaction", event_subtype="income", date_column=None)
    # Renaming one through source_field_map fixes it.
    ok = dataframe_to_unified(both, event_type="transaction", event_subtype="income",
                              date_column=None, source_field_map={"location": "region"})
    assert set(ok["field_name"]) == {"location_name", "region"}


# ── No duplicate data ───────────────────────────────────────────────────────

def test_value_hash_normalizes():
    """Equal values hash equal regardless of int/float/text-number form; different types differ."""
    assert value_hash(450) == value_hash(450.0) == value_hash("450.00", "number")
    assert value_hash("Rye ") == value_hash("Rye")
    assert value_hash("450") != value_hash(450)
    assert value_hash(True) != value_hash(1)


def test_same_batch_twice_is_all_unchanged():
    """Loading the same QuickBooks data again inserts nothing and updates nothing."""
    batch = quickbooks_response_to_unified(_load("quickbooks_invoice.json"))
    first = plan_upsert(batch, None)
    assert len(first["insert"]) == len(batch)
    again = plan_upsert(batch, first["insert"])
    assert len(again["insert"]) == 0 and len(again["update"]) == 0 and len(again["stale"]) == 0
    assert len(again["unchanged"]) == len(batch)


def test_changed_value_is_an_update_and_cleared_value_is_stale():
    """Editing one invoice in QuickBooks: a new balance is an update, a removed memo is stale."""
    stored = quickbooks_response_to_unified(_load("quickbooks_invoice.json")).assign(
        row_id=lambda d: range(1, len(d) + 1))          # as if read back from the database
    edited = _load("quickbooks_invoice.json")
    invoice = edited["QueryResponse"]["Invoice"][0]
    invoice["Balance"] = 0                             # the customer paid
    del invoice["CustomerMemo"]                         # memo cleared
    plan = plan_upsert(quickbooks_response_to_unified(edited), stored)

    assert list(plan["update"]["field_name"]) == ["balance_due"]
    assert plan["update"]["value_number"].iloc[0] == 0
    assert "row_id" in plan["update"].columns           # the loader knows which row to update
    assert list(plan["stale"]["field_name"]) == ["customer_memo"]
    assert len(plan["insert"]) == 0


def test_duplicate_rows_in_one_batch_insert_once():
    """The same rows twice in one upload (or an overlapping API page) are inserted once."""
    single = quickbooks_response_to_unified(_load("quickbooks_purchase.json"))
    doubled = pd.concat([single, single], ignore_index=True)
    plan = plan_upsert(doubled, None)
    assert len(plan["insert"]) == len(single)
    assert not plan["insert"].duplicated(subset=KEY_COLUMNS).any()

    file_rows = pd.concat([SALES, SALES], ignore_index=True).drop(columns="order_id")
    long = dataframe_to_unified(file_rows, event_type="transaction", event_subtype="income",
                                date_column="order_date")
    file_plan = plan_upsert(long, None)
    assert file_plan["insert"]["record_id"].nunique() == 3


def test_newest_copy_wins_inside_a_batch():
    """If one record arrives twice with different values, the copy QuickBooks updated last wins."""
    old = {"Id": "5", "TxnDate": "2026-09-01", "TotalAmt": 10,
           "MetaData": {"LastUpdatedTime": "2026-09-01T10:00:00-07:00"}}
    new = {**old, "TotalAmt": 12, "MetaData": {"LastUpdatedTime": "2026-09-02T10:00:00-07:00"}}
    batch = pd.DataFrame(flatten_quickbooks("Invoice", new) + flatten_quickbooks("Invoice", old))
    plan = plan_upsert(batch, None)
    total = plan["insert"][plan["insert"]["field_name"] == "total_amount"]
    assert list(total["value_number"]) == [12.0]


# ── Python maps and SQL seed stay in sync ───────────────────────────────────

def test_field_map_matches_sql_seed():
    """Every (system, source_field) -> (field_name, value_type) pair is the same in Python and SQL."""
    sql = (ROOT / "db" / "warehouse.sql").read_text()
    block = sql[sql.index("INSERT INTO field_map"):]
    block = block[:block.index("ON CONFLICT")]
    seeded = {(s, src): (name, kind) for s, src, name, kind in
              re.findall(r"\('(\w+)',\s*'([^']+)',\s*'(\w+)',\s*'(\w+)'", block)}
    in_python = {(system, src): pair for system, fmap in FIELD_MAPS.items() for src, pair in fmap.items()}
    assert seeded == in_python


def test_snake_case():
    """Unknown field paths get predictable snake_case names."""
    assert snake_case("MetaData.LastUpdatedTime") == "meta_data_last_updated_time"
    assert snake_case("Order Date") == "order_date"
    assert snake_case("TxnTaxDetail.TaxLine[0].Amount") == "txn_tax_detail_tax_line_0_amount"
