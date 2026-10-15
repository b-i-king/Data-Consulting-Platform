"""
Unified data: turn ANY source (QuickBooks JSON, an uploaded file) into ONE
shape — IDs, dates and categories kept as they are, every METRIC pivoted into
its own row — and plan loads that never add duplicate data.

WHY THIS EXISTS
---------------
Every client's data names its numbers differently (QuickBooks "TotalAmt" and
"Line.Amount", a spreadsheet "amount", "unit_price", "shipping_cost", "csat").
A wide table would need a new column for every new number from every new
source. Instead each number becomes a ROW described the same way for everyone:

    metric_type     what is measured            amount, price, cost, quantity, score
    metric_subtype  which one (optional)        line, total, unit, shipping, csat
    metric_unit     what the number is in       usd, count, percent, score, hours
    metric_value    the number itself

and everything that is NOT a metric — IDs, dates, categories and other text
(customer, store, product, age group) — stays as it is, in `attributes`, on
every metric row of that record:

    wide:  order_id | order_date | store   | quantity | unit_price | amount
           50001    | 2024-10-11 | Midtown | 3        | 7.00       | 21.00

    long:  record_id | field_name | metric_type | metric_subtype | metric_unit | metric_value | attributes
           50001     | quantity   | quantity    |                | count       | 3            | {order_id, order_date, store}
           50001     | unit_price | price       | unit           | usd         | 7.00         | {order_id, order_date, store}
           50001     | line_amount| amount      | line           | usd         | 21.00        | {order_id, order_date, store}

The matching database table is `fact.account_data` (db/schema.sql);
the design is explained in docs/DATA_MODEL.md ("Unified data"). The
`unified-metrics` skill is the test bench: it runs this module on a client's
file, shows every decision, and proves by round trip that nothing is lost.

THE PIECES, IN ORDER
--------------------
1. ALIGN NAMES AND DESCRIBE METRICS. `QUICKBOOKS_FIELD_MAP` and
   `FILE_UPLOAD_FIELD_MAP` translate each source's field names into one shared
   vocabulary (TxnDate and order_date both become "transaction_date") and say
   which fields are metrics, with their type / subtype / unit. The same rows
   are seeded into `fact.field_map`; a test keeps the two copies
   identical. A field that is in NO map is still kept, under the snake_case of
   its own name with `mapped = False`; if it is a number, `classify_metric()`
   guesses its type from the words in its name (with a confidence, so a person
   can review the guess).
2. RESHAPE. `flatten_quickbooks()` turns one QuickBooks Online API object (an
   Invoice, a Purchase, ...) into rows: header fields are line_number 0, each
   item of its `Line` list is line 1, 2, 3, ... `dataframe_to_unified()` does
   the same for a cleaned DataFrame (pandas-style melt of the metric columns).
3. PIVOT BACK. `unified_to_wide()` rebuilds a normal table (one row per
   record + line); `round_trip()` compares it with the original, cell by cell.
4. NO DUPLICATES. `row_hash()` fingerprints a row's content; `plan_upsert()`
   compares a new batch with what is stored and splits it into insert /
   update / unchanged (plus "stale": stored metrics that disappeared).
   Re-loading the same data therefore inserts nothing and changes nothing.

This module is pure pandas: it never writes to a database. The "Load into
database" flow uses it: api/services/warehouse_loader.py builds the rows and
the preview, api/services/warehouse_store.py writes them to fact.account_data.

COLUMNS SET LATER, AT LOAD TIME
-------------------------------
row_id, account_id, consultant_id, source_system, source_name, added_at and
modified_at belong to the database row and are filled in by the loader (they
depend on who is signed in and which source was connected), so they are not
produced here. Everything else in `UNIFIED_COLUMNS` matches the SQL table.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import numpy as np
import pandas as pd

# ── Columns (same names as fact.account_data) ───────────────────────────────

# The kinds of value a field can hold (field_map.value_type). Metrics are
# always numbers; the other kinds are kept in `attributes`.
VALUE_TYPES = ("text", "number", "date", "timestamp", "boolean")

# The columns this module produces, in the same order as the SQL table
# (minus the load-time columns listed in the module docstring).
UNIFIED_COLUMNS = [
    "record_id",           # source record's ID (e.g. "Invoice:130") or a content hash
    "line_number",         # 0 = header, 1.. = line items
    "event_type",          # e.g. "invoice"
    "event_subtype",       # e.g. "sent"
    "event_date",          # the date used in the clean data (e.g. TxnDate)
    "source_modified_at",  # when the source last changed the record (UTC)
    "field_name",          # unified name of the metric, e.g. "line_amount"
    "source_field",        # original name/path, e.g. "Line.Amount" or "Amount ($)"
    "metric_type",         # e.g. "amount"
    "metric_subtype",      # e.g. "line" (None when there is no "which one")
    "metric_unit",         # e.g. "usd"
    "metric_value",        # the number
    "attributes",          # dict: the record line's IDs, dates and categories, by unified name
    "row_hash",            # fingerprint of the row's content (see row_hash)
    "mapped",              # True if the metric's name/description came from a field map or a person
]

# One metric of one line of one record: the unique key inside one client +
# one source. In SQL the key is (account_id, source_system, source_name, record_id,
# line_number, field_name).
KEY_COLUMNS = ["record_id", "line_number", "field_name"]

# field_name of the one row a line gets when it has NO metric at all (e.g. a
# QuickBooks description-only line, or a file row whose numbers are all
# blank), so its attributes are never lost. Its metric columns are empty.
NO_METRIC = "_no_metric"

# ── QuickBooks Online: which entity is which event ──────────────────────────
# entity name in the API -> (event_type, event_subtype, field holding the date).
# The type/subtype pairs are in warehouse_loader.EVENT_TYPES (a test checks it).
QUICKBOOKS_ENTITIES: dict[str, tuple[str, str, str]] = {
    "Invoice":       ("invoice", "sent", "TxnDate"),          # we bill a customer
    "Bill":          ("invoice", "received", "TxnDate"),      # a vendor bills us
    "BillPayment":   ("invoice", "paid", "TxnDate"),          # we pay a vendor's bill
    "Payment":       ("transaction", "income", "TxnDate"),    # a customer pays an invoice
    "SalesReceipt":  ("transaction", "income", "TxnDate"),    # paid-on-the-spot sale
    "Purchase":      ("transaction", "expense", "TxnDate"),   # "Expense"/"Check" in the QuickBooks UI
    "RefundReceipt": ("transaction", "refund", "TxnDate"),    # money returned to a customer
}

# API bookkeeping keys that are not business data (every object has them).
QUICKBOOKS_SKIP_FIELDS = {"domain", "sparse"}

# ── Field maps ──────────────────────────────────────────────────────────────
# source name -> (field_name, value_type, metric_type, metric_subtype, metric_unit).
# The last three are None for a field that is NOT a metric (an ID, a date, a
# name, a category, or a number that only counts lines such as LineNum): those
# are kept in `attributes`.
# Paths use dots for nesting and leave out list positions: "Line.Amount" is the
# Amount of every item in the Line list. KEEP IN SYNC with the
# fact.field_map seed in db/schema.sql (tests/test_unified.py checks).
FieldSpec = tuple[str, str, "str | None", "str | None", "str | None"]

QUICKBOOKS_FIELD_MAP: dict[str, FieldSpec] = {
    # Header (line_number 0)
    "Id":                           ("source_record_id", "text", None, None, None),
    "SyncToken":                    ("sync_token", "text", None, None, None),
    "TxnDate":                      ("transaction_date", "date", None, None, None),
    "DueDate":                      ("due_date", "date", None, None, None),
    "DocNumber":                    ("reference_number", "text", None, None, None),
    "TotalAmt":                     ("total_amount", "number", "amount", "total", "usd"),
    "Balance":                      ("balance_due", "number", "balance", "due", "usd"),
    "UnappliedAmt":                 ("unapplied_amount", "number", "amount", "unapplied", "usd"),
    "TxnTaxDetail.TotalTax":        ("tax_amount", "number", "tax", None, "usd"),
    "CurrencyRef.value":            ("currency", "text", None, None, None),
    "CurrencyRef.name":             ("currency_name", "text", None, None, None),
    "ExchangeRate":                 ("exchange_rate", "number", "rate", "exchange", "ratio"),
    "CustomerRef.value":            ("customer_source_id", "text", None, None, None),
    "CustomerRef.name":             ("customer_name", "text", None, None, None),
    "VendorRef.value":              ("vendor_source_id", "text", None, None, None),
    "VendorRef.name":               ("vendor_name", "text", None, None, None),
    "EntityRef.value":              ("payee_source_id", "text", None, None, None),
    "EntityRef.name":               ("payee_name", "text", None, None, None),
    "EntityRef.type":               ("payee_type", "text", None, None, None),
    "AccountRef.value":             ("payment_account_id", "text", None, None, None),
    "AccountRef.name":              ("payment_account_name", "text", None, None, None),
    "DepositToAccountRef.name":     ("deposit_account_name", "text", None, None, None),
    "PaymentType":                  ("payment_type", "text", None, None, None),
    "PaymentMethodRef.name":        ("payment_method_name", "text", None, None, None),
    "PaymentRefNum":                ("payment_reference", "text", None, None, None),
    "DepartmentRef.name":           ("location_name", "text", None, None, None),
    "SalesTermRef.name":            ("terms", "text", None, None, None),
    "PrivateNote":                  ("memo", "text", None, None, None),
    "CustomerMemo.value":           ("customer_memo", "text", None, None, None),
    "BillEmail.Address":            ("customer_email", "text", None, None, None),
    "BillAddr.Line1":               ("bill_address_line1", "text", None, None, None),
    "BillAddr.City":                ("bill_city", "text", None, None, None),
    "BillAddr.CountrySubDivisionCode": ("bill_state", "text", None, None, None),
    "BillAddr.PostalCode":          ("bill_postal_code", "text", None, None, None),
    "EmailStatus":                  ("email_status", "text", None, None, None),
    "LinkedTxn.TxnId":              ("linked_txn_id", "text", None, None, None),
    "LinkedTxn.TxnType":            ("linked_txn_type", "text", None, None, None),
    "MetaData.CreateTime":          ("source_created_at", "timestamp", None, None, None),
    "MetaData.LastUpdatedTime":     ("source_updated_at", "timestamp", None, None, None),
    # Line items (line_number 1, 2, ...)
    "Line.Id":                      ("line_source_id", "text", None, None, None),
    "Line.LineNum":                 ("line_num", "number", None, None, None),     # a position, not a measure
    "Line.DetailType":              ("line_detail_type", "text", None, None, None),
    "Line.Amount":                  ("line_amount", "number", "amount", "line", "usd"),
    "Line.Description":             ("line_description", "text", None, None, None),
    "Line.SalesItemLineDetail.ItemRef.value":        ("item_source_id", "text", None, None, None),
    "Line.SalesItemLineDetail.ItemRef.name":         ("item_name", "text", None, None, None),
    "Line.SalesItemLineDetail.Qty":                  ("quantity", "number", "quantity", None, "count"),
    "Line.SalesItemLineDetail.UnitPrice":            ("unit_price", "number", "price", "unit", "usd"),
    "Line.SalesItemLineDetail.ServiceDate":          ("service_date", "date", None, None, None),
    "Line.SalesItemLineDetail.TaxCodeRef.value":     ("tax_code", "text", None, None, None),
    "Line.SalesItemLineDetail.ItemAccountRef.name":  ("income_account_name", "text", None, None, None),
    "Line.ItemBasedExpenseLineDetail.ItemRef.value": ("item_source_id", "text", None, None, None),
    "Line.ItemBasedExpenseLineDetail.ItemRef.name":  ("item_name", "text", None, None, None),
    "Line.ItemBasedExpenseLineDetail.Qty":           ("quantity", "number", "quantity", None, "count"),
    "Line.ItemBasedExpenseLineDetail.UnitPrice":     ("unit_price", "number", "price", "unit", "usd"),
    "Line.ItemBasedExpenseLineDetail.TaxCodeRef.value": ("tax_code", "text", None, None, None),
    "Line.AccountBasedExpenseLineDetail.AccountRef.value": ("expense_category_id", "text", None, None, None),
    "Line.AccountBasedExpenseLineDetail.AccountRef.name":  ("expense_category", "text", None, None, None),
    "Line.AccountBasedExpenseLineDetail.BillableStatus":   ("billable_status", "text", None, None, None),
    "Line.AccountBasedExpenseLineDetail.TaxCodeRef.value": ("tax_code", "text", None, None, None),
    "Line.DiscountLineDetail.DiscountPercent":       ("discount_percent", "number", "discount", None, "percent"),
    "Line.LinkedTxn.TxnId":                          ("linked_txn_id", "text", None, None, None),
    "Line.LinkedTxn.TxnType":                        ("linked_txn_type", "text", None, None, None),
}

# Generic uploaded-file headers, after snake_case ("Order Date" -> "order_date").
# A file row is treated like ONE line item, so "amount" lines up with
# QuickBooks' Line.Amount. Per-upload overrides go in `source_field_map`
# (names) and `metric_map` (metric descriptions).
FILE_UPLOAD_FIELD_MAP: dict[str, FieldSpec] = {
    "date":            ("transaction_date", "date", None, None, None),
    "order_date":      ("transaction_date", "date", None, None, None),
    "transaction_date": ("transaction_date", "date", None, None, None),
    "expense_date":    ("transaction_date", "date", None, None, None),
    "invoice_date":    ("transaction_date", "date", None, None, None),
    "due_date":        ("due_date", "date", None, None, None),
    "order_id":        ("reference_number", "text", None, None, None),
    "invoice_number":  ("reference_number", "text", None, None, None),
    "receipt_number":  ("reference_number", "text", None, None, None),
    "customer_id":     ("customer_source_id", "text", None, None, None),
    "customer":        ("customer_name", "text", None, None, None),
    "customer_name":   ("customer_name", "text", None, None, None),
    "customer_email":  ("customer_email", "text", None, None, None),
    "vendor":          ("vendor_name", "text", None, None, None),
    "supplier":        ("vendor_name", "text", None, None, None),
    "product":         ("item_name", "text", None, None, None),
    "item":            ("item_name", "text", None, None, None),
    "description":     ("line_description", "text", None, None, None),
    "quantity":        ("quantity", "number", "quantity", None, "count"),
    "qty":             ("quantity", "number", "quantity", None, "count"),
    "unit_price":      ("unit_price", "number", "price", "unit", "usd"),
    "price":           ("unit_price", "number", "price", "unit", "usd"),
    "amount":          ("line_amount", "number", "amount", "line", "usd"),
    "total":           ("total_amount", "number", "amount", "total", "usd"),
    "total_amount":    ("total_amount", "number", "amount", "total", "usd"),
    "tax":             ("tax_amount", "number", "tax", None, "usd"),
    "payment_method":  ("payment_method_name", "text", None, None, None),
    "store":           ("location_name", "text", None, None, None),
    "location":        ("location_name", "text", None, None, None),
    "memo":            ("memo", "text", None, None, None),
    "notes":           ("memo", "text", None, None, None),
}

# system name (fact.account_data.source_system, fact.field_map.system) -> its map.
FIELD_MAPS = {"quickbooks": QUICKBOOKS_FIELD_MAP, "file_upload": FILE_UPLOAD_FIELD_MAP}

# ── Guessing metrics nobody mapped ──────────────────────────────────────────
# (word in the name) -> (metric_type, metric_unit, subtype when the word itself
# is specific). The FIRST word found, scanning top to bottom, gives the type,
# so more specific words come first ("nps" before "score").
KNOWN_MEASURES: list[tuple[str, str, str, str | None]] = [
    # word         type          unit       subtype
    ("csat",       "score",      "score",   "csat"),
    ("nps",        "score",      "score",   "nps"),
    ("rating",     "score",      "score",   "rating"),
    ("stars",      "score",      "score",   "rating"),
    ("score",      "score",      "score",   None),
    ("price",      "price",      "usd",     None),
    ("cost",       "cost",       "usd",     None),
    ("expense",    "cost",       "usd",     "expense"),
    ("spend",      "cost",       "usd",     "spend"),
    ("fee",        "cost",       "usd",     "fee"),
    ("tax",        "tax",        "usd",     None),
    ("discount",   "discount",   "usd",     None),
    ("tip",        "amount",     "usd",     "tip"),
    ("revenue",    "amount",     "usd",     "revenue"),
    ("sales",      "amount",     "usd",     "sales"),
    ("income",     "amount",     "usd",     "income"),
    ("amount",     "amount",     "usd",     None),
    ("total",      "amount",     "usd",     "total"),
    ("balance",    "balance",    "usd",     None),
    ("salary",     "pay",        "usd",     "salary"),
    ("wage",       "pay",        "usd",     "wage"),
    ("pay",        "pay",        "usd",     None),
    ("profit",     "profit",     "usd",     None),
    ("margin",     "margin",     "percent", None),
    ("pct",        "rate",       "percent", None),
    ("percent",    "rate",       "percent", None),
    ("rate",       "rate",       "percent", None),
    ("hours",      "time",       "hours",   None),
    ("hrs",        "time",       "hours",   None),
    ("minutes",    "time",       "minutes", None),
    ("mins",       "time",       "minutes", None),
    ("days",       "time",       "days",    None),
    ("quantity",   "quantity",   "count",   None),
    ("qty",        "quantity",   "count",   None),
    ("units",      "quantity",   "count",   None),
    ("count",      "count",      "count",   None),
    ("visits",     "count",      "count",   "visits"),
    ("orders",     "count",      "count",   "orders"),
    ("age",        "age",        "years",   None),
    ("weight",     "weight",     "lbs",     None),
]
# Words that name the UNIT outright; they win over the measure word's default
# unit ("discount_pct" is a discount in percent, not dollars).
UNIT_WORDS = {"pct": "percent", "percent": "percent", "hours": "hours", "hrs": "hours",
              "minutes": "minutes", "mins": "minutes", "days": "days"}

# Money going OUT: in these events an "amount" is a cost.
EXPENSE_EVENTS = {("transaction", "expense"), ("invoice", "received"), ("payroll", "gross_pay")}
# Words in OTHER columns that say "this table is expenses" (when no event is given).
EXPENSE_HINTS = {"expense", "expenses", "vendor", "supplier", "bill", "payee"}
# Names that look like identifiers, not measures (order_no, zip, sku, phone...).
ID_NAME = re.compile(r"(^id$|_id$|^id_|_no$|_num$|number$|code$|zip|postal|phone|sku|^key$|_key$)", re.I)
# Names that suggest a text column holds dates.
DATE_NAME = re.compile(r"(date|_at$|_on$|time|day$|month|period)", re.I)


# ── Small helpers ───────────────────────────────────────────────────────────

def snake_case(name: str) -> str:
    """
    Turn a source field name or path into a lowercase_with_underscores name.

    Used to name fields that no field map knows, so they are kept with a
    readable, predictable name instead of being dropped.

    Args:
        name: a field name or dotted path, e.g. "MetaData.LastUpdatedTime",
            "Order Date" or "Line.SalesItemLineDetail.ClassRef.name".

    Returns:
        str: the snake_case name ("" if nothing usable is left).

    Example:
        >>> snake_case("MetaData.LastUpdatedTime")
        'meta_data_last_updated_time'
        >>> snake_case("Order Date")
        'order_date'
    """
    # Split camelCase / PascalCase: "TxnId" -> "Txn_Id", "SKUCode" -> "SKU_Code".
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", str(name))
    text = re.sub(r"[^0-9a-zA-Z]+", "_", text)       # dots, spaces, brackets -> "_"
    return text.strip("_").lower()


def _words(name: str) -> list[str]:
    """
    Split a name into lowercase words: "Unit Price ($)" -> ["unit", "price"], "TaxPercent" -> ["tax", "percent"].

    Example:
        >>> _words("TaxPercent")
        ['tax', 'percent']
    """
    return [w for w in snake_case(name).split("_") if w]


def _is_missing(value: Any) -> bool:
    """
    Tell whether a value counts as "no value" (None, NaN, NaT, pd.NA or "").

    Args:
        value: any cell value.

    Returns:
        bool: True for a missing value.

    Example:
        >>> _is_missing(float("nan")), _is_missing(""), _is_missing(0)
        (True, True, False)
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    try:
        result = pd.isna(value)
    except (TypeError, ValueError):
        return False
    # pd.isna gives one True/False for a single value, an array for a list
    # (a list is a value, not "missing").
    return isinstance(result, (bool, np.bool_)) and bool(result)


def _canonical(value: Any, value_type: str) -> str:
    """
    Write a value as one canonical string, so equal values always look equal.

    450, 450.0 and Decimal("450.00") all become "450"; dates become
    "YYYY-MM-DD"; timestamps become UTC ISO strings; booleans "true"/"false";
    text is trimmed. This is what `value_hash` and `row_hash` fingerprint.

    Args:
        value: the value (already of the right kind for value_type).
        value_type: one of VALUE_TYPES.

    Returns:
        str: the canonical text.

    Example:
        >>> _canonical(450.0, "number"), _canonical("  Rye  ", "text")
        ('450', 'Rye')
    """
    if isinstance(value, np.generic):      # numpy scalar (np.float64, np.bool_) -> plain Python
        value = value.item()
    if value_type == "number":
        # repr() of a float is its shortest exact form; Decimal.normalize() drops
        # trailing zeros. "f" format avoids scientific notation ("1E+2").
        number = Decimal(repr(value)) if isinstance(value, float) else Decimal(str(value))
        text = format(number.normalize(), "f")
        return "0" if text in ("-0", "") else text
    if value_type == "date":
        return pd.Timestamp(value).date().isoformat()
    if value_type == "timestamp":
        stamp = pd.Timestamp(value)
        stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
        return stamp.isoformat()
    if value_type == "boolean":
        return "true" if bool(value) else "false"
    return str(value).strip()


def _infer_type(value: Any) -> str:
    """
    Guess the value_type of a value nobody mapped, from its Python type.

    Text is never guessed to be a date: an unmapped "2026-09-01" stays text,
    which is safe (nothing is mis-read); a field map can say otherwise.

    Args:
        value: a JSON or pandas value.

    Returns:
        str: one of VALUE_TYPES.

    Example:
        >>> _infer_type(True), _infer_type(3), _infer_type("x")
        ('boolean', 'number', 'text')
    """
    if isinstance(value, (bool, np.bool_)):          # check before int: True is an int
        return "boolean"
    if isinstance(value, (int, float, Decimal, np.integer, np.floating)):
        return "number"
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        return "timestamp"
    if isinstance(value, dt.date):
        return "date"
    return "text"


def value_hash(value: Any, value_type: str | None = None) -> str:
    """
    Fingerprint one value (SHA-256 of its type + canonical form).

    Equal values give equal hashes however they are written; the same digits
    as a different type (text "450" vs. number 450) give a different hash.

    Args:
        value: the value to fingerprint.
        value_type: one of VALUE_TYPES; guessed from the Python type if omitted.

    Returns:
        str: 64 hex characters.

    Example:
        >>> value_hash(450) == value_hash(450.0) == value_hash("450.00", "number")
        True
        >>> value_hash("450") == value_hash(450)
        False
    """
    kind = value_type or _infer_type(value)
    if kind == "number" and isinstance(value, str):
        value = Decimal(value.strip())
    payload = f"{kind}:{_canonical(value, kind)}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _typed_value(value: Any, value_type: str) -> tuple[str, Any]:
    """
    Convert a raw value to the kind its value_type promises.

    If the conversion fails (a mapped number field holds "N/A"), the value is
    kept as text rather than lost.

    Args:
        value: the raw value (e.g. a JSON string "2026-09-14").
        value_type: the expected type, one of VALUE_TYPES.

    Returns:
        tuple: (actual value_type, converted value).

    Example:
        >>> _typed_value("2026-09-14", "date")
        ('date', datetime.date(2026, 9, 14))
        >>> _typed_value("N/A", "number")
        ('text', 'N/A')
    """
    if isinstance(value, np.generic):
        value = value.item()
    try:
        if value_type == "number":
            if isinstance(value, bool):
                raise ValueError("a boolean is not a number")
            number = float(value)
            if math.isnan(number) or math.isinf(number):
                raise ValueError("not a finite number")
            return "number", number
        if value_type == "date":
            return "date", pd.Timestamp(value).date()
        if value_type == "timestamp":
            stamp = pd.Timestamp(value)
            stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
            return "timestamp", stamp
        if value_type == "boolean":
            if isinstance(value, str):
                lowered = value.strip().lower()
                if lowered not in ("true", "false"):
                    raise ValueError("not a boolean")
                return "boolean", lowered == "true"
            return "boolean", bool(value)
    except (ValueError, TypeError, InvalidOperation):
        return "text", str(value)
    return "text", str(value).strip() if isinstance(value, str) else str(value)


def _date_key(value: Any) -> str:
    """
    Write a date as "YYYY-MM-DD" ("" when missing), so stored and new dates compare fairly.

    Example:
        >>> _date_key(None), _date_key("2026-09-14")
        ('', '2026-09-14')
    """
    return "" if _is_missing(value) else pd.Timestamp(value).date().isoformat()


# ── Describing metrics ──────────────────────────────────────────────────────

def classify_metric(name: str) -> dict:
    """
    Guess metric_type, metric_subtype and metric_unit from a field's name.

    The name's words are checked against KNOWN_MEASURES; the first match gives
    the type and default unit. A unit word in the name ("pct", "hours") sets
    the unit. The OTHER words become the subtype ("shipping_cost" -> cost /
    shipping). No match: the type is the name itself, unit "number",
    confidence "low" (a person should decide).

    Args:
        name: the column name, or the last part of a source path ("TaxPercent").

    Returns:
        dict: {"type", "subtype", "unit", "confidence" ("high" | "medium" | "low"), "why"}.

    Example:
        >>> classify_metric("shipping_cost")
        {'type': 'cost', 'subtype': 'shipping', 'unit': 'usd', 'confidence': 'high', 'why': 'found "cost" in the name'}
        >>> classify_metric("discount_pct")["unit"], classify_metric("widgets")["confidence"]
        ('percent', 'low')
    """
    words = _words(name)
    for word, mtype, unit, default_sub in KNOWN_MEASURES:
        if word in words:
            unit_word = next((w for w in words if w in UNIT_WORDS and w != word), None)
            if unit_word:
                unit = UNIT_WORDS[unit_word]
            rest = [w for w in words if w not in (word, unit_word)]
            subtype = "_".join(rest) or default_sub
            # Leftover words we can't place (e.g. "x_y_z_amount") lower confidence a little.
            confidence = "high" if len(rest) <= 2 else "medium"
            return {"type": mtype, "subtype": subtype, "unit": unit, "confidence": confidence,
                    "why": f'found "{word}" in the name'}
    return {"type": "_".join(words) or str(name), "subtype": None, "unit": "number", "confidence": "low",
            "why": "no known measure in the name — please set type, subtype and unit"}


def _expense_context(event_type: str | None, event_subtype: str | None, other_names: Iterable[str]) -> str | None:
    """
    Decide whether this data is money going OUT, and why.

    The event says so for sure (a QuickBooks Purchase is transaction/expense).
    Without an event, a "vendor" / "supplier" / "expense..." column is a strong
    hint, but only a hint (a person should confirm).

    Returns:
        str | None: "event" (certain), "columns" (a guess), or None.

    Example:
        >>> _expense_context("transaction", "expense", []), _expense_context(None, None, ["vendor"])
        ('event', 'columns')
    """
    if (event_type, event_subtype) in EXPENSE_EVENTS:
        return "event"
    if event_type is None and {w for n in other_names for w in _words(n)} & EXPENSE_HINTS:
        return "columns"
    return None


def _describe(spec: FieldSpec | None, name_for_guess: str, *, expense: str | None,
              currency: str | None, override: dict | None) -> dict:
    """
    The metric description of one NUMBER field: from its field map row, else guessed.

    Then two context rules apply: in an expense, "amount" means "cost"; a
    non-US-dollar currency replaces the "usd" unit. A person's `override`
    (metric_map) wins over everything.

    Args:
        spec: the field's FieldSpec when a map knows it (its metric_type must be set).
        name_for_guess: the name classify_metric reads when there is no spec.
        expense: result of _expense_context.
        currency: the record's currency code (QuickBooks CurrencyRef), or None.
        override: {"type", "subtype", "unit"} from the person, or None.

    Returns:
        dict: {"type", "subtype", "unit", "confidence", "why"}; confidence is
        "mapped" (from a field map), "set by you", or classify_metric's.

    Example:
        >>> _describe(None, "amount", expense="event", currency=None, override=None)["type"]
        'cost'
    """
    if spec is not None:
        meta = {"type": spec[2], "subtype": spec[3], "unit": spec[4], "confidence": "mapped",
                "why": "from the field map"}
    else:
        meta = classify_metric(name_for_guess)
    if expense and meta["type"] == "amount":
        meta = {**meta, "type": "cost"}
        if expense == "event":
            meta["why"] += "; money going out (an expense), so it is a cost"
        else:
            meta["why"] += "; the table looks like expenses (vendor / expense columns), so it's a cost — please confirm"
            if meta["confidence"] in ("high", "mapped"):
                meta["confidence"] = "medium"
    if currency and currency.upper() != "USD" and meta["unit"] == "usd":
        meta["unit"] = currency.lower()
    if override:
        meta = {**meta, **{k: override[k] for k in ("type", "subtype", "unit") if k in override},
                "confidence": "set by you", "why": "from metric_map"}
    return meta


def describe_metric_column(column: str) -> dict:
    """
    Unified name and description of one metric COLUMN of an uploaded file, by its name alone.

    The shared file map first ("amount" -> line_amount, amount / line / usd),
    else a guess from the words in the name. Used where only names are known,
    such as the Spark reshape (spark_engine.melt_to_unified).

    Args:
        column: the column name.

    Returns:
        dict: {"field_name", "type", "subtype", "unit", "confidence", "why"}.

    Example:
        >>> describe_metric_column("Amount")["field_name"], describe_metric_column("shipping_cost")["type"]
        ('line_amount', 'cost')
    """
    spec = FILE_UPLOAD_FIELD_MAP.get(snake_case(column))
    if spec is not None and spec[2] is None:
        spec = None            # in the map, but not as a metric: describe it by its name
    meta = _describe(spec, column, expense=None, currency=None, override=None)
    return {"field_name": spec[0] if spec else snake_case(column), **meta}


# ── Building rows ───────────────────────────────────────────────────────────

def _json_safe(value: Any) -> Any:
    """
    One attribute value as JSON-friendly Python (numpy -> plain, NaN -> None).

    Dates, timestamps and booleans are KEPT as Python objects here so a
    pivot back gives the same types; `attributes_json()` writes them as text
    for the database.
    """
    if isinstance(value, np.generic):
        value = value.item()
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def _attr_canonical(value: Any) -> str:
    """The canonical text of an attribute value (for the row fingerprint)."""
    return _canonical(value, _infer_type(value))


def _attributes_key(attributes: dict | None) -> dict:
    """The attributes in canonical form (sorted, blanks dropped): the part of row_hash shared by a line's rows."""
    return {k: _attr_canonical(v) for k, v in sorted((attributes or {}).items()) if not _is_missing(v)}


def row_hash(row: dict, attributes_key: dict | None = None) -> str:
    """
    Fingerprint everything a row SAYS: the metric, its value, the record date and the attributes.

    Two loads of the same data give the same hash, so `plan_upsert` (and the
    SQL upsert) can skip the row: nothing rewritten, modified_at untouched.
    A changed value, a corrected customer name or a re-classified metric gives
    a new hash, so the row is updated.

    Args:
        row: a dict with metric_type, metric_subtype, metric_unit,
            metric_value, event_date and attributes.
        attributes_key: _attributes_key(row["attributes"]) when already
            made (every row of a line shares it, so it is made once per line).

    Returns:
        str: 64 hex characters.

    Example:
        >>> a = {"metric_type": "amount", "metric_subtype": None, "metric_unit": "usd",
        ...      "metric_value": 21, "event_date": None, "attributes": {"store": "Midtown"}}
        >>> row_hash(a) == row_hash({**a, "metric_value": 21.0})
        True
    """
    value = row.get("metric_value")
    payload = {
        "metric": [row.get("metric_type"), row.get("metric_subtype"), row.get("metric_unit")],
        "value": None if _is_missing(value) else _canonical(value, "number"),
        "date": _date_key(row.get("event_date")),
        "attributes": attributes_key if attributes_key is not None else _attributes_key(row.get("attributes")),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _line_rows(*, record_id: str, line_number: int, event_type: str, event_subtype: str,
               event_date: Any, source_modified_at: Any, attributes: dict, metrics: list[dict]) -> list[dict]:
    """
    The rows for ONE line of one record: one per metric, all sharing the line's attributes.

    Args:
        record_id, line_number, event_type, event_subtype, event_date, source_modified_at:
            the line's identity (see UNIFIED_COLUMNS).
        attributes: {field_name: value} of every non-metric field on the line.
        metrics: dicts with field_name, source_field, type, subtype, unit, value, mapped.

    Returns:
        list[dict]: rows with every UNIFIED_COLUMNS key. A line with no metric
        gets one NO_METRIC row, so its attributes are still kept.

    Example:
        >>> rows = _line_rows(record_id="1", line_number=0, event_type="transaction", event_subtype="income",
        ...                   event_date=None, source_modified_at=None, attributes={"store": "A"}, metrics=[])
        >>> rows[0]["field_name"], rows[0]["metric_value"]
        ('_no_metric', None)
    """
    base = {"record_id": record_id, "line_number": int(line_number), "event_type": event_type,
            "event_subtype": event_subtype, "event_date": event_date, "source_modified_at": source_modified_at}
    items = metrics or [{"field_name": NO_METRIC, "source_field": "", "type": None, "subtype": None,
                         "unit": None, "value": None, "mapped": True}]
    shared = _attributes_key(attributes)
    rows = []
    for m in items:
        row = {**base, "field_name": m["field_name"], "source_field": m["source_field"],
               "metric_type": m["type"], "metric_subtype": m["subtype"], "metric_unit": m["unit"],
               "metric_value": m["value"], "attributes": dict(attributes), "mapped": bool(m["mapped"])}
        row["row_hash"] = row_hash(row, shared)
        rows.append({c: row[c] for c in UNIFIED_COLUMNS})           # same order as the table
    return rows


def _to_frame(rows: Iterable[dict]) -> pd.DataFrame:
    """
    Put rows into a DataFrame with the standard columns, in order.

    Example:
        >>> list(_to_frame([]).columns) == UNIFIED_COLUMNS
        True
    """
    frame = pd.DataFrame(list(rows), columns=UNIFIED_COLUMNS)
    frame["line_number"] = frame["line_number"].astype("int64")
    frame["metric_value"] = pd.to_numeric(frame["metric_value"], errors="coerce")
    # Text columns as plain Python objects with None for "no value": pandas 3
    # would otherwise store blanks as NaN (a float), which the database
    # driver refuses for a TEXT column (e.g. a metric with no subtype).
    for column in ("record_id", "event_type", "event_subtype", "field_name", "source_field",
                   "metric_type", "metric_subtype", "metric_unit", "row_hash"):
        frame[column] = frame[column].astype(object).where(frame[column].notna(), None)
    return frame


def attributes_json(attributes: dict) -> str:
    """
    The attributes as JSON text for the database's JSONB column.

    Dates become "YYYY-MM-DD", timestamps UTC ISO text, so they sort and
    compare correctly in SQL: (attributes->>'transaction_date')::date.

    Example:
        >>> attributes_json({"d": dt.date(2026, 9, 1), "store": "Midtown"})
        '{"d": "2026-09-01", "store": "Midtown"}'
    """
    def plain(v: Any) -> Any:
        kind = _infer_type(v)
        return _canonical(v, kind) if kind in ("date", "timestamp") else v
    return json.dumps({k: plain(v) for k, v in attributes.items()})


# ── QuickBooks Online ───────────────────────────────────────────────────────

def _walk(value: Any, path: list[str], indexes: list[int]):
    """
    Yield every leaf value of nested JSON with its path and list positions.

    Args:
        value: a JSON value (dict, list or scalar).
        path: the keys so far, e.g. ["CustomerRef"].
        indexes: list positions passed so far (for lists other than Line).

    Returns:
        generator of (lookup_path, source_field, indexes, value):
        lookup_path has no list positions ("LinkedTxn.TxnId", used for the
        field map); source_field shows them ("LinkedTxn[1].TxnId").

    Example:
        >>> list(_walk({"CustomerRef": {"value": "21"}}, [], []))
        [('CustomerRef.value', 'CustomerRef.value', [], '21')]
    """
    if isinstance(value, dict):
        for key, child in value.items():
            yield from _walk(child, path + [key], indexes)
    elif isinstance(value, list):
        for position, child in enumerate(value):
            # Mark the position on the last key: "LinkedTxn" -> "LinkedTxn[1]".
            marked = path[:-1] + [f"{path[-1]}[{position}]"] if path else [f"[{position}]"]
            yield from _walk(child, marked, indexes + [position])
    else:
        source_field = ".".join(path)
        lookup_path = re.sub(r"\[\d+\]", "", source_field)
        yield lookup_path, source_field, indexes, value


def _field_name_for(lookup_path: str, indexes: list[int], field_map: dict) -> tuple[str, FieldSpec | None]:
    """
    Pick the unified field name (and the map row, if any) for one leaf.

    Args:
        lookup_path: the path without list positions, e.g. "LinkedTxn.TxnId".
        indexes: list positions on the way (the 2nd LinkedTxn adds "_2").
        field_map: source path -> FieldSpec.

    Returns:
        tuple: (field_name, FieldSpec or None when no map knows the path).

    Example:
        >>> _field_name_for("LinkedTxn.TxnId", [1], QUICKBOOKS_FIELD_MAP)[0]
        'linked_txn_id_2'
    """
    spec = field_map.get(lookup_path)
    name = spec[0] if spec else snake_case(lookup_path)
    # The first item of a list keeps the plain name; later items get _2, _3...
    if any(indexes):
        name += "_" + "_".join(str(i + 1) for i in indexes)
    return name, spec


def flatten_quickbooks(entity_name: str, obj: dict, *, field_map: dict | None = None) -> list[dict]:
    """
    Turn one QuickBooks Online API object into unified rows (one per metric).

    Header fields belong to line_number 0; the n-th item of the object's
    `Line` list is line_number n (1-based, in the order QuickBooks returns
    them). On each line, number fields the map calls metrics (and unmapped
    numbers that don't look like IDs) become rows; every other field goes
    into that line's `attributes`. Known fields are renamed through the field
    map; unknown ones are KEPT under a snake_case name (an unknown metric gets
    mapped=False). Missing/empty values are skipped.

    The record_id is "<entity>:<Id>" because QuickBooks Ids are only unique
    within one entity type (Invoice 130 and Payment 130 can both exist).

    Args:
        entity_name: the API entity, a key of QUICKBOOKS_ENTITIES ("Invoice").
        obj: one object from a QueryResponse, e.g. response["QueryResponse"]["Invoice"][0].
        field_map: optional override of QUICKBOOKS_FIELD_MAP.

    Returns:
        list[dict]: rows with the UNIFIED_COLUMNS keys.

    Raises:
        ValueError: if the entity is unknown or the object has no Id.

    Example:
        >>> rows = flatten_quickbooks("Invoice", {"Id": "130", "TxnDate": "2026-09-14",
        ...     "TotalAmt": 96.0, "Line": [{"Amount": 96.0, "DetailType": "SalesItemLineDetail"}]})
        >>> [(r["line_number"], r["field_name"], r["metric_value"]) for r in rows]
        [(0, 'total_amount', 96.0), (1, 'line_amount', 96.0)]
    """
    if entity_name not in QUICKBOOKS_ENTITIES:
        raise ValueError(f"Unknown QuickBooks entity {entity_name!r}; known: {sorted(QUICKBOOKS_ENTITIES)}")
    if _is_missing(obj.get("Id")):
        raise ValueError(f"QuickBooks {entity_name} object has no Id")
    fmap = QUICKBOOKS_FIELD_MAP if field_map is None else field_map
    event_type, event_subtype, date_field = QUICKBOOKS_ENTITIES[entity_name]

    record_id = f"{entity_name}:{obj['Id']}"
    raw_date = obj.get(date_field)
    event_date = None if _is_missing(raw_date) else _typed_value(raw_date, "date")[1]
    raw_updated = (obj.get("MetaData") or {}).get("LastUpdatedTime")
    source_modified_at = None if _is_missing(raw_updated) else _typed_value(raw_updated, "timestamp")[1]
    currency = (obj.get("CurrencyRef") or {}).get("value")
    expense = _expense_context(event_type, event_subtype, [])

    # Header = everything except the Line list; then one group per line item.
    header = {k: v for k, v in obj.items() if k != "Line" and k not in QUICKBOOKS_SKIP_FIELDS}
    groups = [(0, header, [])]
    groups += [(n, line, ["Line"]) for n, line in enumerate(obj.get("Line") or [], start=1)]

    rows: list[dict] = []
    for line_number, content, prefix in groups:
        attributes: dict[str, Any] = {}
        metrics: list[dict] = []
        used: set[str] = set()       # field names already used on this line
        for lookup_path, source_field, indexes, value in _walk(content, prefix, []):
            if _is_missing(value):
                continue
            name, spec = _field_name_for(lookup_path, indexes, fmap)
            if name in used:         # two paths mapped to one name: keep both
                name, spec = snake_case(source_field), None
            used.add(name)
            kind, typed = _typed_value(value, spec[1] if spec else _infer_type(value))
            # A metric: a number the map calls a metric, or an unmapped number
            # whose name doesn't look like an ID ("LineNum" is mapped as NOT a metric).
            is_metric = kind == "number" and (
                spec[2] is not None if spec else not ID_NAME.search(snake_case(lookup_path.split(".")[-1])))
            if not is_metric:
                attributes[name] = typed
                continue
            # Unmapped: guess from the path's last part ("TaxPercent"), not the whole path.
            meta = _describe(spec, lookup_path.split(".")[-1], expense=expense, currency=currency, override=None)
            metrics.append({"field_name": name, "source_field": source_field, "type": meta["type"],
                            "subtype": meta["subtype"], "unit": meta["unit"], "value": typed,
                            "mapped": spec is not None})
        rows += _line_rows(record_id=record_id, line_number=line_number, event_type=event_type,
                           event_subtype=event_subtype, event_date=event_date,
                           source_modified_at=source_modified_at, attributes=attributes, metrics=metrics)
    return rows


def quickbooks_response_to_unified(response: dict) -> pd.DataFrame:
    """
    Flatten a whole QuickBooks Online query response into one unified DataFrame.

    Walks `response["QueryResponse"]` and flattens every known entity list in
    it (Invoice, Purchase, ...). Unknown entity lists are skipped.

    Args:
        response: the parsed JSON of a QuickBooks query, e.g. the result of
            GET /v3/company/<realmId>/query?query=select * from Invoice.

    Returns:
        pd.DataFrame: rows, columns UNIFIED_COLUMNS.

    Example:
        >>> df = quickbooks_response_to_unified({"QueryResponse": {"Invoice": [
        ...     {"Id": "1", "TxnDate": "2026-09-01", "TotalAmt": 5}]}})
        >>> df["record_id"].unique().tolist()
        ['Invoice:1']
    """
    body = response.get("QueryResponse", response)
    rows: list[dict] = []
    for entity_name, objects in body.items():
        if entity_name in QUICKBOOKS_ENTITIES and isinstance(objects, list):
            for obj in objects:
                rows.extend(flatten_quickbooks(entity_name, obj))
    return _to_frame(rows)


# ── Uploaded files (any cleaned DataFrame) ──────────────────────────────────

def _column_value_type(series: pd.Series) -> str:
    """
    Pick the value_type for a whole DataFrame column from its dtype.

    Returns:
        str: "boolean", "number", "date" (datetime with no time part),
        "timestamp", or "text".

    Example:
        >>> _column_value_type(pd.Series([1.5, 2]))
        'number'
    """
    if pd.api.types.is_bool_dtype(series):
        return "boolean"
    if pd.api.types.is_numeric_dtype(series):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(series):
        values = series.dropna()
        if getattr(series.dt, "tz", None) is None and (values == values.dt.normalize()).all():
            return "date"
        return "timestamp"
    return "text"


def _as_dates(series: pd.Series) -> pd.Series | None:
    """
    A text column with a date-like name whose every value parses as a date -> the parsed dates; else None.

    The cleaning step keeps dates as ISO text ("2024-10-11"), so this is how
    order_date is recognized as a date.
    """
    if not DATE_NAME.search(str(series.name)) or not (
            series.dtype == object or pd.api.types.is_string_dtype(series)):
        return None
    values = series.dropna()
    if values.empty or not values.map(lambda v: isinstance(v, str)).all():
        return None
    parsed = pd.to_datetime(series, errors="coerce", format="mixed")
    return parsed if parsed.notna().sum() == len(values) else None


# A text column counts as THE date column when at least this share of its
# values are dates (an export with a few "TBD" or typo cells).
MOSTLY_DATES = 0.9


def _mostly_dates(series: pd.Series) -> bool:
    """
    A text column with a date-like name where at least MOSTLY_DATES of the values are dates.

    Such a column is the record's date (event_date is filled where the value
    is a date), but its values are kept exactly as written, so the odd ones
    ("not a date") are never lost.

    Example:
        >>> _mostly_dates(pd.Series(["2024-10-01"] * 9 + ["TBD"], name="order_date"))
        True
    """
    if not DATE_NAME.search(str(series.name)) or not (
            series.dtype == object or pd.api.types.is_string_dtype(series)):
        return False
    values = series.dropna()
    if values.empty:
        return False
    parsed = pd.to_datetime(values.astype(str), errors="coerce", format="mixed")
    return parsed.notna().mean() >= MOSTLY_DATES


def plan_columns(
    df: pd.DataFrame,
    *,
    event_type: str | None = None,
    event_subtype: str | None = None,
    metric_columns: list[str] | None = None,
    id_columns: list[str] | None = None,
    metric_map: dict[str, dict] | None = None,
    source_field_map: dict[str, str | tuple] | None = None,
) -> list[dict]:
    """
    Decide, for every column, its ROLE and its unified name — and for metrics, their description.

    Roles: "metric" (a number that is measured), "id", "date", "category"
    (everything else: text, yes/no, demographics). Only metrics become rows;
    the other three stay as they are, in `attributes`.

    Order of decisions for each column:
      1. id_columns / metric_columns chosen by a person win.
      2. A field map row (source_field_map, then FILE_UPLOAD_FIELD_MAP by the
         snake_case name) gives the unified name, and says whether it is a metric.
      3. Otherwise: a date column -> date; an ID-like name -> id; a number ->
         metric (described by classify_metric); anything else -> category.
    Context: in an expense (event, or vendor / expense columns) "amount" is a
    cost. metric_map ({column: {"type", "subtype", "unit"}}) overrides any guess.

    Args:
        df: the cleaned table.
        event_type, event_subtype: what each row is, if known (e.g. "transaction", "expense").
        metric_columns: force exactly these columns to be metrics.
        id_columns: force these columns to be IDs.
        metric_map: a person's corrections of metric descriptions.
        source_field_map: {column: field_name} (or a FieldSpec tuple) for this upload.

    Returns:
        list[dict]: one per column, in table order: column, role, field_name,
        mapped, value_type, metric_type, metric_subtype, metric_unit,
        confidence ("mapped", "set by you", "high", "medium", "low", or None for
        non-metrics) and why.

    Raises:
        ValueError: a chosen column isn't in the table, or metric_map is malformed.

    Example:
        >>> plan = plan_columns(pd.DataFrame({"order_id": [1], "store": ["A"], "amount": [2.0]}))
        >>> [(p["column"], p["role"], p["field_name"]) for p in plan]
        [('order_id', 'id', 'reference_number'), ('store', 'category', 'location_name'), ('amount', 'metric', 'line_amount')]
    """
    for chosen in (metric_columns or []) + (id_columns or []) + list(metric_map or {}):
        if chosen not in df.columns:
            raise ValueError(f"Column {chosen!r} isn't in this table.")
    for column, override in (metric_map or {}).items():
        if not isinstance(override, dict):
            raise ValueError(f"metric_map[{column!r}] must be an object with type / subtype / unit.")
    overrides = source_field_map or {}
    expense = _expense_context(event_type, event_subtype,
                               [c for c in df.columns if c not in (metric_columns or [])])

    plan = []
    for column in df.columns:
        series = df[column]
        kind = _column_value_type(series)
        if kind == "text" and _as_dates(series) is not None:
            kind = "date"
        # Mostly dates: still the date column, but stored as written (text) so nothing is lost.
        text_dates = kind == "text" and _mostly_dates(series)
        # The map row, if any: a per-upload name first, then the shared file map.
        spec: FieldSpec | None = None
        if column in overrides:
            target = overrides[column]
            spec = tuple(target) if isinstance(target, (tuple, list)) else (target, kind, None, None, None)
            if len(spec) < 5:
                spec = (*spec, *([None] * (5 - len(spec))))
        elif snake_case(column) in FILE_UPLOAD_FIELD_MAP:
            spec = FILE_UPLOAD_FIELD_MAP[snake_case(column)]
        name = spec[0] if spec else snake_case(column)

        if id_columns and column in id_columns:
            role = "id"
        elif metric_columns is not None:
            role = "metric" if column in metric_columns else None
        elif spec is not None and spec[2] is not None and kind == "number":
            role = "metric"
        elif spec is None and kind == "number" and not ID_NAME.search(str(column)):
            role = "metric"
        else:
            role = None
        if role is None:     # not a metric: which kind of attribute?
            role = ("date" if kind in ("date", "timestamp") or text_dates
                    else "id" if ID_NAME.search(str(column)) else "category")

        entry = {"column": column, "role": role, "field_name": name, "mapped": spec is not None,
                 "value_type": kind, "metric_type": None, "metric_subtype": None, "metric_unit": None,
                 "confidence": None, "why": "kept as it is"}
        if role == "metric":
            if kind != "number":
                raise ValueError(f"Column {column!r} isn't a number column, so it can't be a metric.")
            map_spec = spec if spec is not None and spec[2] is not None else None
            meta = _describe(map_spec, str(column), expense=expense, currency=None,
                             override=(metric_map or {}).get(column))
            entry.update(metric_type=meta["type"], metric_subtype=meta["subtype"], metric_unit=meta["unit"],
                         confidence=meta["confidence"], why=meta["why"],
                         mapped=map_spec is not None or meta["confidence"] == "set by you")
        plan.append(entry)

    # Two columns aligned to one name (e.g. "store" and "location") would collide.
    names = pd.Series([p["field_name"] for p in plan])
    clashes = sorted(names[names.duplicated()].unique())
    if clashes:
        raise ValueError(f"Several columns map to the same field: {clashes}; "
                         "pass source_field_map to rename one")
    return plan


def pick_record_id_column(df: pd.DataFrame, plan: list[dict]) -> str | None:
    """
    The ID column that names each record, if there is one.

    First choice: an id column with no blanks and no repeats (one row per
    record). Second: a document number (order / invoice / receipt number,
    unified name reference_number) with no blanks that repeats — one row per
    LINE of an order; dataframe_to_unified then numbers the lines 1, 2, 3. A
    repeating customer_id is never used (a customer is not a record). Without
    either, each row's ID is a fingerprint of its content.

    Args:
        df: the cleaned table.
        plan: its column plan (plan_columns).

    Returns:
        str | None: the column, or None for content fingerprints.

    Example:
        >>> d = pd.DataFrame({"customer_id": [1, 1], "order_id": [7, 7], "amount": [1.0, 2.0]})
        >>> pick_record_id_column(d, plan_columns(d))
        'order_id'
    """
    ids = [p for p in plan if p["role"] == "id" and df[p["column"]].notna().all()]
    unique = next((p["column"] for p in ids if df[p["column"]].is_unique), None)
    document = next((p["column"] for p in ids if p["field_name"] == "reference_number"), None)
    return unique or document


def _row_content_hash(df: pd.DataFrame, types: dict[str, str]) -> pd.Series:
    """
    Give each row a stable ID made from its content (for files with no ID column).

    The same row uploaded again — even in another file, in another order — gets
    the same ID, so it cannot be stored twice. Values are canonicalized first,
    so 21 and 21.0 hash the same; column order does not matter.

    Args:
        df: the data.
        types: column -> value_type.

    Returns:
        pd.Series: "sha256:<32 hex>" per row, same index as df.

    Example:
        >>> d = pd.DataFrame({"a": [1, 1.0]})
        >>> h = _row_content_hash(d, {"a": "number"})
        >>> h.iloc[0] == h.iloc[1]
        True
    """
    columns = sorted(df.columns, key=str)

    def one_row(row: pd.Series) -> str:
        content = {str(c): (None if _is_missing(row[c]) else _canonical(row[c], types[c])) for c in columns}
        digest = hashlib.sha256(json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
        return f"sha256:{digest[:32]}"     # 128 bits: collisions are not a practical concern

    return df.apply(one_row, axis=1) if len(df) else pd.Series([], dtype=object)


def dataframe_to_unified(
    df: pd.DataFrame,
    *,
    event_type: str,
    event_subtype: str,
    date_column: str | None = None,
    id_column: str | None = None,
    line_column: str | None = None,
    plan: list[dict] | None = None,
    keep_identical_rows: bool = False,
    **plan_options: Any,
) -> pd.DataFrame:
    """
    Reshape a cleaned DataFrame into unified rows: one per metric cell with a value.

    Like pandas `melt` with the id / date / category columns as `id_vars`:
    each metric column becomes rows, and the other columns ride along in
    `attributes` (by their unified name), exactly as they were.

    record_id comes from `id_column` when the file has IDs. Without one, it is
    a SHA-256 hash of the row's content, so the SAME row uploaded twice gets
    the SAME record_id and `plan_upsert` sees it as already stored. Identical
    rows inside one file therefore collapse into one record, unless
    `keep_identical_rows=True` (then the 2nd copy gets "#2" added to its ID —
    still stable across re-uploads of the same file).

    line_number is 0, unless `line_column` is given, or `id_column` repeats
    (e.g. one row per invoice line): then rows of the same ID are numbered
    1, 2, 3... in file order.

    Args:
        df: the cleaned data (one row per record or line).
        event_type: e.g. "transaction" (a warehouse_loader.EVENT_TYPES pair).
        event_subtype: e.g. "income".
        date_column: the column holding the date used in the clean data
            (copied to event_date), or None.
        id_column: the column holding each record's ID, if the file has one.
        line_column: a column holding line numbers (integers), if any.
        plan: the result of plan_columns, if already made (e.g. after review).
        keep_identical_rows: keep exact duplicate rows as separate records.
        **plan_options: passed to plan_columns when `plan` is None
            (metric_columns, id_columns, metric_map, source_field_map).

    Returns:
        pd.DataFrame: rows, columns UNIFIED_COLUMNS. `.attrs["plan"]` holds the
        column plan, which `unified_to_wide` uses to give back the original names.

    Raises:
        ValueError: a named column is missing, the ID column has blanks, or two
        columns map to one field.

    Example:
        >>> sales = pd.DataFrame({"order_date": pd.to_datetime(["2026-09-01"]), "store": ["A"], "amount": [21.0]})
        >>> long = dataframe_to_unified(sales, event_type="transaction",
        ...     event_subtype="income", date_column="order_date")
        >>> long[["field_name", "metric_type", "metric_value"]].values.tolist()
        [['line_amount', 'amount', 21.0]]
        >>> sorted(long["attributes"].iloc[0])
        ['location_name', 'transaction_date']
    """
    data = df.reset_index(drop=True)
    for column in (date_column, id_column, line_column):
        if column is not None and column not in data.columns:
            raise ValueError(f"Column {column!r} is not in the data")
    if plan is None:
        plan = plan_columns(data, event_type=event_type, event_subtype=event_subtype, **plan_options)
    types = {p["column"]: p["value_type"] for p in plan}
    # Text dates found by plan_columns are parsed once, so they are real dates.
    for p in plan:
        if p["value_type"] == "date" and _column_value_type(data[p["column"]]) == "text":
            data[p["column"]] = _as_dates(data[p["column"]])

    # 1. record_id: the file's own ID, or a content hash.
    if id_column is not None:
        record_ids = data[id_column].map(
            lambda v: None if _is_missing(v) else _canonical(v, types[id_column]))
        if record_ids.isna().any():
            raise ValueError(f"ID column {id_column!r} has empty values")
    else:
        record_ids = _row_content_hash(data, types)
        if keep_identical_rows:
            copy_number = record_ids.groupby(record_ids).cumcount() + 1
            record_ids = record_ids.where(copy_number == 1, record_ids + "#" + copy_number.astype(str))

    # 2. line_number.
    if line_column is not None:
        line_numbers = pd.to_numeric(data[line_column], errors="raise").astype("int64")
    elif id_column is not None and record_ids.duplicated().any():
        line_numbers = record_ids.groupby(record_ids).cumcount() + 1
    else:
        line_numbers = pd.Series(0, index=data.index)

    # 3. event_date.
    if date_column is not None:
        event_dates = pd.to_datetime(data[date_column], errors="coerce").map(
            lambda t: None if pd.isna(t) else t.date())
    else:
        event_dates = pd.Series([None] * len(data), index=data.index, dtype=object)

    # 4. Per row: attributes from the non-metric columns, one row per metric with a value.
    kept = [p for p in plan if p["role"] != "metric"]
    measured = [p for p in plan if p["role"] == "metric"]
    # Each column as a plain Python list, read once (reading cell by cell with
    # pandas .at is about 50 times slower on a file of 20,000 rows).
    values = {p["column"]: data[p["column"]].tolist() for p in plan}
    for p in kept:
        if p["value_type"] == "date":
            values[p["column"]] = [None if _is_missing(v) else pd.Timestamp(v).date() for v in values[p["column"]]]
    ids, lines, dates = record_ids.tolist(), line_numbers.tolist(), event_dates.tolist()
    rows: list[dict] = []
    for i in range(len(data)):
        attributes = {p["field_name"]: _json_safe(values[p["column"]][i]) for p in kept
                      if not _is_missing(values[p["column"]][i])}
        metrics = [{"field_name": p["field_name"], "source_field": str(p["column"]),
                    "type": p["metric_type"], "subtype": p["metric_subtype"], "unit": p["metric_unit"],
                    "value": float(values[p["column"]][i]), "mapped": p["mapped"]}
                   for p in measured if not _is_missing(values[p["column"]][i])]
        rows += _line_rows(record_id=ids[i], line_number=int(lines[i]), event_type=event_type,
                           event_subtype=event_subtype, event_date=dates[i], source_modified_at=None,
                           attributes=attributes, metrics=metrics)
    long = _to_frame(rows)
    long.attrs["plan"] = plan
    return long


# ── Pivot back to wide ──────────────────────────────────────────────────────

def unified_to_wide(long_df: pd.DataFrame, *, use_source_names: bool = False,
                    plan: list[dict] | None = None) -> pd.DataFrame:
    """
    Pivot unified rows back into a normal table: one row per record_id + line_number.

    The attributes of each line become columns again, and each metric
    (field_name) becomes a numeric column. Rows keep the order they first
    appear in long_df. A field a line doesn't have is empty in that row.

    Args:
        long_df: unified rows (UNIFIED_COLUMNS).
        use_source_names: name columns as the client's original headers
            ("order_date") instead of unified names ("transaction_date"), in
            the original column order. Needs the column plan: `plan`, or the
            one dataframe_to_unified stored in long_df.attrs.
        plan: the column plan from plan_columns (see use_source_names).

    Returns:
        pd.DataFrame: columns record_id, line_number, then one per field.

    Example:
        >>> long = dataframe_to_unified(pd.DataFrame({"qty": [2, 3]}), event_type="transaction",
        ...     event_subtype="income")
        >>> unified_to_wide(long)["quantity"].tolist()
        [2.0, 3.0]
    """
    keys = ["record_id", "line_number"]
    if long_df.empty:
        return pd.DataFrame(columns=keys)
    # Attributes: the same on every row of a line, so take the line's first row.
    first = long_df.drop_duplicates(subset=keys)
    attrs = pd.DataFrame(first["attributes"].tolist(), index=first.index)
    wide = pd.concat([first[keys], attrs], axis=1).reset_index(drop=True)

    measured = long_df[long_df["field_name"] != NO_METRIC]
    if not measured.empty:
        metrics = measured.pivot_table(index=keys, columns="field_name", values="metric_value", aggfunc="first")
        metric_order = list(dict.fromkeys(measured["field_name"]))       # first-seen order, not sorted
        wide = wide.merge(metrics[metric_order].reset_index(), on=keys, how="left")

    # Real date columns again (attributes hold datetime.date objects).
    for column in wide.columns[2:]:
        values = wide[column].dropna()
        if len(values) and values.map(lambda v: isinstance(v, (dt.date, pd.Timestamp))).all():
            wide[column] = pd.to_datetime(wide[column])

    if use_source_names:
        plan = plan or long_df.attrs.get("plan")
        if not plan:
            raise ValueError("use_source_names needs the column plan (pass plan=...)")
        rename = {p["field_name"]: p["column"] for p in plan}
        wide = wide.rename(columns=rename)
        wide = wide.reindex(columns=keys + [p["column"] for p in plan])
    wide.columns.name = None
    return wide


def round_trip(df: pd.DataFrame, long_df: pd.DataFrame, plan: list[dict] | None = None) -> dict:
    """
    Prove nothing was lost: pivot the unified rows back and compare EVERY cell with the original.

    Blanks equal blanks; numbers compare as numbers (3 == 3.0); dates by day
    or exact time; everything else as text. Rows are matched in order, so
    build `long_df` with keep_identical_rows=True (identical rows otherwise
    collapse into one record, by design).

    Args:
        df: the original cleaned table.
        long_df: dataframe_to_unified(df, ...).
        plan: the column plan (default: long_df.attrs["plan"]).

    Returns:
        dict: {"ok", "cells_checked", "mismatches", "examples"} — up to 5
        differing cells as {"row", "column", "original", "rebuilt"}.

    Example:
        >>> d = pd.DataFrame({"store": ["A", None], "amount": [2.0, None]})
        >>> round_trip(d, dataframe_to_unified(d, event_type="transaction", event_subtype="income",
        ...            keep_identical_rows=True))["ok"]
        True
    """
    original = df.reset_index(drop=True)
    plan = plan or long_df.attrs.get("plan") or []
    rebuilt = unified_to_wide(long_df, use_source_names=True, plan=plan)
    columns = list(original.columns)
    # Date columns compare as dates on both sides (the cleaned file may hold
    # "2024-10-11" as text; the rebuilt column holds real dates).
    date_columns = {p["column"] for p in plan if p["value_type"] in ("date", "timestamp")}

    def norm(v: Any, is_date: bool = False) -> Any:
        if _is_missing(v):
            return None
        if is_date:
            return pd.Timestamp(v).isoformat()
        kind = _infer_type(v)
        if kind == "number":
            return float(v)
        if kind in ("date", "timestamp"):
            return pd.Timestamp(v).isoformat()
        if kind == "boolean":
            return bool(v)
        return str(v).strip()

    mismatches, examples = 0, []
    same_rows = len(rebuilt) == len(original)
    for column in columns:
        is_date = column in date_columns
        before = original[column].tolist()           # plain lists: fast to compare
        after = rebuilt[column].tolist() if same_rows and column in rebuilt.columns else None
        for i in range(len(original)):
            a = norm(before[i], is_date)
            b = norm(after[i], is_date) if after is not None else "(missing)"
            if a != b:
                mismatches += 1
                if len(examples) < 5:
                    examples.append({"row": i + 1, "column": str(column), "original": a, "rebuilt": b})
    return {"ok": mismatches == 0, "cells_checked": len(original) * len(columns),
            "mismatches": mismatches, "examples": examples}


# ── Anti-duplicate load planning ────────────────────────────────────────────

def plan_upsert(new_long: pd.DataFrame, existing_long: pd.DataFrame | None) -> dict[str, pd.DataFrame]:
    """
    Decide what a load must do so the table never holds duplicate data.

    Both frames must be for ONE client and ONE source (the SQL key adds
    account_id, source_system and source_name to KEY_COLUMNS). Steps:

    1. De-duplicate the new batch: identical rows count once; if one metric of
       one record line appears twice with different content, the copy with
       the latest source_modified_at wins (otherwise the last one).
    2. Compare each new row with the stored row of the same key:
         not stored              -> "insert"
         stored, same row_hash   -> "unchanged" (nothing written, modified_at
                                    keeps its old value)
         stored, different hash  -> "update" (a value, the date, an attribute
                                    or the metric's description changed)
    3. "stale": stored rows of records that ARE in the new batch but whose
       key is no longer there (a value was cleared or a line was removed in
       QuickBooks). The loader decides whether to delete them.

    This mirrors the SQL upsert in api/services/warehouse_store.py
    (ON CONFLICT ... DO UPDATE ... WHERE row_hash IS DISTINCT FROM ...).

    Args:
        new_long: rows about to be loaded.
        existing_long: rows already stored (None or empty if none). Extra
            columns such as row_id are carried into "update" and "stale".

    Returns:
        dict[str, pd.DataFrame]: keys "insert", "update", "unchanged", "stale".

    Example:
        >>> batch = dataframe_to_unified(pd.DataFrame({"qty": [2]}), event_type="transaction",
        ...     event_subtype="income")
        >>> plan = plan_upsert(batch, batch)
        >>> len(plan["insert"]), len(plan["unchanged"])
        (0, 1)
    """
    new = new_long.copy()
    if "row_hash" not in new.columns or new["row_hash"].isna().any():
        new["row_hash"] = [row_hash(row) for row in new.to_dict("records")]

    # 1. Identical rows once; conflicting copies -> the newest wins.
    new = new.drop_duplicates(subset=KEY_COLUMNS + ["row_hash"])
    if "source_modified_at" in new.columns and new["source_modified_at"].notna().any():
        modified = pd.to_datetime(new["source_modified_at"], utc=True, errors="coerce")
        new = new.assign(_modified=modified).sort_values("_modified", kind="stable", na_position="first")
        new = new.drop(columns="_modified")
    new = new.drop_duplicates(subset=KEY_COLUMNS, keep="last")

    empty = new.iloc[0:0]
    if existing_long is None or existing_long.empty:
        return {"insert": new.reset_index(drop=True), "update": empty.copy(),
                "unchanged": empty.copy(), "stale": empty.copy()}

    # 2. Compare by key. Stored extras (row_id, ...) come along.
    old = existing_long.copy()
    extras = [c for c in old.columns if c not in new.columns]
    stored = old[KEY_COLUMNS + ["row_hash"] + extras].rename(columns={"row_hash": "_old_hash"})
    merged = new.merge(stored, on=KEY_COLUMNS, how="left", indicator=True)
    is_new = merged["_merge"] == "left_only"
    same = merged["row_hash"] == merged["_old_hash"]
    helper = ["_old_hash", "_merge"]

    insert = merged[is_new].drop(columns=helper + extras)
    unchanged = merged[~is_new & same].drop(columns=helper)
    update = merged[~is_new & ~same].drop(columns=helper)

    # 3. Stale: stored keys of re-sent records that the new batch no longer has.
    resent = old[old["record_id"].isin(new["record_id"])]
    keys_now = set(map(tuple, new[KEY_COLUMNS].astype(str).to_numpy()))
    is_stale = [tuple(k) not in keys_now for k in resent[KEY_COLUMNS].astype(str).to_numpy()]
    stale = resent[is_stale]

    return {name: frame.reset_index(drop=True) for name, frame in
            {"insert": insert, "update": update, "unchanged": unchanged, "stale": stale}.items()}
