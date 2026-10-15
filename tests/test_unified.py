"""
Tests for the unified data layer (api/services/unified.py): IDs, dates and
categories kept as they are, every metric pivoted into its own row.

They cover the owner's promises:
1. Any source fits one shape: QuickBooks Online JSON (fixtures shaped like real
   API v3 responses, fictional bakery data) and uploaded files both turn into
   the same rows — metric_type / subtype / unit / value plus attributes — and
   those rows pivot back into the original table, cell for cell.
2. Metrics are described consistently: the field map first, then a guess from
   the name with a confidence; expenses make "amount" a cost.
3. No duplicate data: loading the same data again inserts nothing and changes
   nothing; a changed value is an update; identical rows count once.
It also checks that the Python field maps and the fact.field_map seed in
db/schema.sql stay identical.
"""

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from api.services.unified import (
    FIELD_MAPS,
    KEY_COLUMNS,
    NO_METRIC,
    QUICKBOOKS_ENTITIES,
    UNIFIED_COLUMNS,
    attributes_json,
    classify_metric,
    dataframe_to_unified,
    flatten_quickbooks,
    plan_columns,
    plan_upsert,
    quickbooks_response_to_unified,
    round_trip,
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


def _metric(rows: pd.DataFrame, line: int, field: str) -> pd.Series:
    """Return the one metric row of `field` on `line`."""
    row = rows[(rows["line_number"] == line) & (rows["field_name"] == field)]
    assert len(row) == 1, f"expected one {field!r} on line {line}, got {len(row)}"
    return row.iloc[0]


def _attr(rows: pd.DataFrame, line: int, field: str):
    """Return attribute `field` of `line` (the same on every row of the line)."""
    on_line = rows[rows["line_number"] == line]
    values = {json.dumps(a.get(field), default=str) for a in on_line["attributes"]}
    assert len(values) == 1, f"{field!r} differs between rows of line {line}"
    return on_line["attributes"].iloc[0].get(field)


# ── QuickBooks ──────────────────────────────────────────────────────────────

def test_flatten_invoice_metrics_and_attributes():
    """An invoice: metrics become rows with type/subtype/unit; names, dates and IDs ride along as attributes."""
    rows = pd.DataFrame(flatten_quickbooks("Invoice", _invoice_130()))

    assert set(rows["record_id"]) == {"Invoice:130"}
    assert set(rows["line_number"]) == {0, 1, 2, 3}            # header + 2 items + subtotal line
    assert set(rows["event_type"]) == {"invoice"} and set(rows["event_subtype"]) == {"sent"}
    assert set(rows["event_date"].astype(str)) == {"2026-09-14"}
    # source_modified_at = MetaData.LastUpdatedTime, in UTC.
    assert str(rows["source_modified_at"].iloc[0]) == "2026-09-15 20:05:47+00:00"

    total = _metric(rows, 0, "total_amount")
    assert (total["metric_type"], total["metric_subtype"], total["metric_unit"], total["metric_value"]) \
        == ("amount", "total", "usd", 181.44)
    price = _metric(rows, 1, "unit_price")
    assert (price["metric_type"], price["metric_subtype"], price["metric_value"]) == ("price", "unit", 8.0)
    assert _metric(rows, 1, "quantity")["metric_value"] == 12
    assert _metric(rows, 2, "line_amount")["metric_value"] == 72.0

    # Not metrics: kept as they are, on every row of their line.
    assert _attr(rows, 0, "customer_name") == "Blue Door Cafe"
    assert str(_attr(rows, 0, "due_date")) == "2026-10-14"
    assert _attr(rows, 1, "item_name") == "Sourdough Loaf"
    assert _attr(rows, 1, "line_num") == 1                       # a position, not a measure
    assert _attr(rows, 3, "line_detail_type") == "SubTotalLineDetail"
    assert not (rows["field_name"] == "line_num").any()

    # API bookkeeping keys are not business data.
    assert not any("domain" in a or "sparse" in a for a in rows["attributes"])
    # The unique key holds: no metric appears twice on one line of one record.
    assert not rows.duplicated(subset=KEY_COLUMNS).any()
    assert list(rows.columns) == UNIFIED_COLUMNS


def test_unknown_quickbooks_fields_are_kept_and_flagged():
    """Fields no map knows are kept: text as an attribute, numbers as metrics described by a guess (mapped=False)."""
    rows = pd.DataFrame(flatten_quickbooks("Invoice", _invoice_130()))
    assert _attr(rows, 0, "custom_field_string_value") == "6-8 AM"
    tax_percent = rows[rows["source_field"].str.endswith("TaxPercent")].iloc[0]
    assert (tax_percent["metric_type"], tax_percent["metric_unit"], tax_percent["mapped"]) == ("tax", "percent", False)
    assert rows.loc[rows["field_name"] == "total_amount", "mapped"].all()


def test_purchase_is_an_expense_so_amounts_are_costs():
    """Purchases are transaction/expense: their amounts are costs; vendor, category and location line up."""
    long = quickbooks_response_to_unified(_load("quickbooks_purchase.json"))
    assert set(long["record_id"]) == {"Purchase:252", "Purchase:253"}
    assert set(zip(long["event_type"], long["event_subtype"])) == {("transaction", "expense")}
    second = long[long["record_id"] == "Purchase:253"]
    assert _metric(second, 0, "total_amount")["metric_type"] == "cost"
    assert _metric(second, 1, "line_amount")["metric_subtype"] == "line"
    assert _attr(second, 0, "payee_name") == "Midtown Restaurant Depot"
    assert _attr(second, 0, "location_name") == "Midtown"           # DepartmentRef.name
    assert _attr(second, 1, "item_name") == "Cake Box 10in"          # item-based line
    assert _attr(second, 2, "expense_category") == "Supplies & Materials"   # account-based line


def test_other_currency_replaces_usd():
    """A record in Canadian dollars gets unit 'cad', so amounts in different currencies are never summed."""
    invoice = {"Id": "9", "TxnDate": "2026-09-01", "TotalAmt": 50, "CurrencyRef": {"value": "CAD"}}
    rows = pd.DataFrame(flatten_quickbooks("Invoice", invoice))
    assert _metric(rows, 0, "total_amount")["metric_unit"] == "cad"


def test_line_without_numbers_keeps_its_attributes():
    """A description-only line has no metric; one NO_METRIC row keeps its text."""
    invoice = {"Id": "9", "TxnDate": "2026-09-01",
               "Line": [{"Description": "Thank you!", "DetailType": "DescriptionOnly"}]}
    rows = pd.DataFrame(flatten_quickbooks("Invoice", invoice))
    note = rows[rows["line_number"] == 1]
    assert list(note["field_name"]) == [NO_METRIC]
    assert note["metric_value"].isna().all() and note["metric_type"].isna().all()
    assert _attr(rows, 1, "line_description") == "Thank you!"


def test_second_list_item_gets_numbered_name():
    """A 2nd LinkedTxn on one record gets linked_txn_id_2 instead of colliding with the 1st."""
    invoice = {"Id": "9", "TxnDate": "2026-09-01",
               "LinkedTxn": [{"TxnId": "1", "TxnType": "Payment"}, {"TxnId": "2", "TxnType": "Payment"}]}
    attrs = flatten_quickbooks("Invoice", invoice)[0]["attributes"]
    assert {"linked_txn_id", "linked_txn_id_2"} <= set(attrs)


def test_unknown_entity_and_missing_id_are_rejected():
    """Only known entities with an Id can be flattened (the Id is the record_id)."""
    with pytest.raises(ValueError):
        flatten_quickbooks("Estimate", {"Id": "1"})
    with pytest.raises(ValueError):
        flatten_quickbooks("Invoice", {"TxnDate": "2026-09-01"})


def test_quickbooks_wide_has_one_row_per_line():
    """Pivoting a QuickBooks response back gives one row per record + line, with metrics and attributes."""
    wide = unified_to_wide(quickbooks_response_to_unified(_load("quickbooks_invoice.json")))
    assert len(wide) == 7          # invoice 130: 4 rows, invoice 131: 3 rows
    header = wide[(wide["record_id"] == "Invoice:130") & (wide["line_number"] == 0)].iloc[0]
    assert header["total_amount"] == 181.44
    assert header["customer_name"] == "Blue Door Cafe"
    assert str(header["transaction_date"].date()) == "2026-09-14"


def test_every_entity_uses_a_known_event_type():
    """Each QuickBooks entity is stored as a (type, subtype) pair the load flow knows (warehouse_loader.EVENT_TYPES)."""
    from api.services.warehouse_loader import EVENT_TYPES

    for event_type, event_subtype, _ in QUICKBOOKS_ENTITIES.values():
        assert f"{event_type}/{event_subtype}" in EVENT_TYPES


# ── Uploaded files ──────────────────────────────────────────────────────────

SALES = pd.DataFrame({
    "order_id": [50001, 50002, 50003],
    "order_date": pd.to_datetime(["2026-09-01", "2026-09-01", "2026-09-02"]),
    "product": ["Sourdough Loaf", "Cupcake Box (6)", "Rye Bread"],
    "quantity": [3, 2, 1],
    "amount": [21.0, 36.0, None],
    "loyalty_member": [True, False, True],
    "note": ["", "gift", None],
})


def test_plan_columns_roles_names_and_metrics():
    """Each column gets a role and a unified name; metrics get type/subtype/unit with a confidence."""
    plan = {p["column"]: p for p in plan_columns(SALES)}
    assert {c: p["role"] for c, p in plan.items()} == {
        "order_id": "id", "order_date": "date", "product": "category", "quantity": "metric",
        "amount": "metric", "loyalty_member": "category", "note": "category"}
    assert plan["amount"]["field_name"] == "line_amount"
    assert (plan["amount"]["metric_type"], plan["amount"]["metric_subtype"], plan["amount"]["metric_unit"]) \
        == ("amount", "line", "usd")
    assert plan["amount"]["confidence"] == "mapped"
    assert plan["product"]["field_name"] == "item_name" and plan["product"]["mapped"]
    # Not in any map: kept under its own (snake_case) name, flagged unmapped.
    assert plan["note"]["field_name"] == "note" and not plan["note"]["mapped"]


def test_text_dates_from_cleaning_are_dates():
    """The cleaning step keeps dates as ISO text; a date-named column of them is still a date."""
    text = SALES.assign(order_date=SALES["order_date"].dt.strftime("%Y-%m-%d"))
    plan = {p["column"]: p for p in plan_columns(text)}
    assert plan["order_date"]["role"] == "date"
    long = dataframe_to_unified(text, event_type="transaction", event_subtype="income",
                                date_column="order_date", id_column="order_id")
    assert str(long["event_date"].iloc[0]) == "2026-09-01"
    assert round_trip(text, long)["ok"]


def test_mostly_dates_is_the_date_column_and_keeps_odd_values():
    """A date column with one typo is still the date column; the typo is stored as written (nothing lost)."""
    df = pd.DataFrame({"order_date": [f"2026-09-{d:02d}" for d in range(1, 11)] + ["TBD"],
                       "amount": [1.0] * 11})
    plan = {p["column"]: p for p in plan_columns(df)}
    assert plan["order_date"]["role"] == "date" and plan["order_date"]["value_type"] == "text"
    long = dataframe_to_unified(df, event_type="transaction", event_subtype="income", date_column="order_date",
                                keep_identical_rows=True)
    assert long["event_date"].notna().sum() == 10                    # the TBD row has no date
    assert long.iloc[-1]["attributes"]["transaction_date"] == "TBD"
    assert round_trip(df, long)["ok"]


def test_classify_metric_guesses_from_the_name():
    """Unmapped metric names: type from a known word, unit word wins, the other words are the subtype."""
    shipping = classify_metric("shipping_cost")
    assert (shipping["type"], shipping["subtype"], shipping["unit"]) == ("cost", "shipping", "usd")
    assert classify_metric("discount_pct")["unit"] == "percent"
    assert classify_metric("csat")["type"] == "score"
    assert classify_metric("widgets")["confidence"] == "low"


def test_expense_columns_make_amount_a_cost_to_confirm():
    """A vendor column means money going out: amount -> cost, flagged medium (a person confirms)."""
    expenses = pd.DataFrame({"expense_date": ["2026-09-01"], "vendor": ["Depot"], "amount": [12.5]})
    amount = plan_columns(expenses)[-1]
    assert (amount["metric_type"], amount["confidence"]) == ("cost", "medium")
    # A chosen event makes it certain.
    certain = plan_columns(expenses, event_type="transaction", event_subtype="expense")[-1]
    assert (certain["metric_type"], certain["confidence"]) == ("cost", "mapped")


def test_metric_map_overrides_and_marks_mapped():
    """A person's metric_map wins and the metric counts as mapped."""
    odd = pd.DataFrame({"widgets": [1, 2]})
    guess = plan_columns(odd)[0]
    assert guess["confidence"] == "low" and not guess["mapped"]
    fixed = plan_columns(odd, metric_map={"widgets": {"type": "quantity", "subtype": "widgets", "unit": "count"}})[0]
    assert (fixed["metric_type"], fixed["confidence"], fixed["mapped"]) == ("quantity", "set by you", True)


def test_file_round_trip_every_cell():
    """A cleaned file -> unified rows -> wide gives back the same table, every column (blanks stay blank)."""
    long = dataframe_to_unified(SALES, event_type="transaction", event_subtype="income",
                                date_column="order_date", id_column="order_id")
    assert set(long["record_id"]) == {"50001", "50002", "50003"}
    assert set(long["line_number"]) == {0}
    # 3 quantities + 2 amounts (the blank amount makes no row).
    assert sorted(long["field_name"].value_counts().items()) == [("line_amount", 2), ("quantity", 3)]
    first = long.iloc[0]["attributes"]
    assert first["reference_number"] == 50001 and first["item_name"] == "Sourdough Loaf"
    assert "memo" not in first                                       # empty text = no value

    wide = unified_to_wide(long, use_source_names=True)
    assert list(wide.columns) == ["record_id", "line_number"] + list(SALES.columns)
    check = round_trip(SALES, long)
    assert check == {"ok": True, "cells_checked": 21, "mismatches": 0, "examples": []}
    assert wide["amount"].sum() == pytest.approx(57.0)
    assert pd.api.types.is_datetime64_any_dtype(wide["order_date"])


def test_round_trip_reports_a_mismatch():
    """If a value changed between the file and the rows, the round trip says where."""
    long = dataframe_to_unified(SALES, event_type="transaction", event_subtype="income",
                                date_column="order_date", id_column="order_id")
    long.loc[long["field_name"] == "quantity", "metric_value"] = 99
    check = round_trip(SALES, long)
    assert not check["ok"] and check["mismatches"] == 3
    assert check["examples"][0]["column"] == "quantity"


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
    long = dataframe_to_unified(lines, event_type="invoice", event_subtype="sent", id_column="invoice_number")
    pairs = set(zip(long["record_id"], long["line_number"]))
    assert pairs == {("1037", 1), ("1037", 2), ("1038", 1)}


def test_two_columns_on_one_field_are_refused():
    """Two columns that align to the same field would break the unique key, so it is an error."""
    both = pd.DataFrame({"store": ["Midtown"], "location": ["Decatur"], "amount": [1.0]})
    with pytest.raises(ValueError, match="location_name"):
        dataframe_to_unified(both, event_type="transaction", event_subtype="income")
    # Renaming one through source_field_map fixes it.
    ok = dataframe_to_unified(both, event_type="transaction", event_subtype="income",
                              source_field_map={"location": "region"})
    assert set(ok["attributes"].iloc[0]) == {"location_name", "region"}


def test_blank_text_is_none_not_nan():
    """A metric with no subtype holds None (the database refuses NaN, a float, in a TEXT column)."""
    long = dataframe_to_unified(SALES, event_type="transaction", event_subtype="income",
                                date_column="order_date", id_column="order_id")
    quantity = long[long["field_name"] == "quantity"]
    assert all(v is None for v in quantity["metric_subtype"])


def test_attributes_json_writes_dates_as_text():
    """The database copy of attributes has ISO dates, so SQL can cast them."""
    long = dataframe_to_unified(SALES, event_type="transaction", event_subtype="income",
                                date_column="order_date", id_column="order_id")
    as_json = json.loads(attributes_json(long.iloc[0]["attributes"]))
    assert as_json["transaction_date"] == "2026-09-01" and as_json["loyalty_member"] is True


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


def test_changed_value_or_attribute_is_an_update_and_cleared_metric_is_stale():
    """Editing an invoice in QuickBooks: a new balance or memo is an update; a removed discount is stale."""
    stored = quickbooks_response_to_unified(_load("quickbooks_invoice.json")).assign(
        row_id=lambda d: range(1, len(d) + 1))          # as if read back from the database
    edited = _load("quickbooks_invoice.json")
    invoice = edited["QueryResponse"]["Invoice"][0]
    invoice["Balance"] = 0                             # the customer paid
    del invoice["TxnTaxDetail"]                         # tax removed (the TotalTax metric disappears)
    plan = plan_upsert(quickbooks_response_to_unified(edited), stored)

    # Line 0's attributes did not change, but its metric rows did (balance) or vanished (tax).
    changed = plan["update"][plan["update"]["record_id"] == "Invoice:130"]
    assert "balance_due" in set(changed["field_name"])
    assert changed.loc[changed["field_name"] == "balance_due", "metric_value"].iloc[0] == 0
    assert "row_id" in plan["update"].columns           # the loader knows which row to update
    assert "tax_amount" in set(plan["stale"]["field_name"])
    assert len(plan["insert"]) == 0
    # Lines 1-3 of the invoice did not change at all.
    assert set(changed["line_number"]) == {0}


def test_corrected_attribute_updates_every_row_of_the_line():
    """A corrected customer name changes the row_hash of every metric row of that line."""
    stored = quickbooks_response_to_unified(_load("quickbooks_invoice.json"))
    edited = _load("quickbooks_invoice.json")
    edited["QueryResponse"]["Invoice"][0]["CustomerRef"]["name"] = "Blue Door Café"
    plan = plan_upsert(quickbooks_response_to_unified(edited), stored)
    header = stored[(stored["record_id"] == "Invoice:130") & (stored["line_number"] == 0)]
    assert len(plan["update"]) == len(header)


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
    assert list(total["metric_value"]) == [12.0]


# ── Python maps and SQL seed stay in sync ───────────────────────────────────

def test_field_map_matches_sql_seed():
    """Every (system, source_field) -> (name, value_type, metric type/subtype/unit) is the same in Python and SQL."""
    sql = (ROOT / "db" / "schema.sql").read_text(encoding="utf-8")
    block = sql[sql.index("INSERT INTO fact.field_map"):]
    block = block[:block.index("ON CONFLICT")]
    value = r"\s*(NULL|'[^']*')"
    rows = re.findall(r"\('(\w+)',\s*'([^']+)',\s*'(\w+)',\s*'(\w+)'," + value + "," + value + "," + value, block)
    unquote = lambda v: None if v == "NULL" else v.strip("'")           # noqa: E731
    seeded = {(s, src): (name, kind, unquote(mt), unquote(ms), unquote(mu))
              for s, src, name, kind, mt, ms, mu in rows}
    in_python = {(system, src): spec for system, fmap in FIELD_MAPS.items() for src, spec in fmap.items()}
    assert seeded == in_python


def test_every_mapped_metric_has_a_unit_and_is_a_number():
    """A metric in the field map always says what it is in, and only number fields are metrics."""
    for fmap in FIELD_MAPS.values():
        for src, (name, kind, mtype, _sub, unit) in fmap.items():
            if mtype is not None:
                assert kind == "number" and unit, src


def test_snake_case():
    """Unknown field paths get predictable snake_case names."""
    assert snake_case("MetaData.LastUpdatedTime") == "meta_data_last_updated_time"
    assert snake_case("Order Date") == "order_date"
    assert snake_case("TxnTaxDetail.TaxLine[0].Amount") == "txn_tax_detail_tax_line_0_amount"
