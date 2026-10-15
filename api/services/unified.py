"""
Unified data: turn ANY source (QuickBooks JSON, an uploaded file) into ONE long
table with one row per field, and plan loads that never add duplicate data.

WHY THIS EXISTS
---------------
Data Engineering clients bring data from many places, and every place names
its fields differently: QuickBooks says "TxnDate" and "CustomerRef.name", a
spreadsheet says "order_date" and "Customer". A wide table would need a new
column for every new field from every new source. Instead we "pivot long":

    one source record  ->  one row PER FIELD
    (record_id, line_number, field_name, value)

so a field nobody has seen before is just another row, never a new column.
The matching database table is `warehouse.unified_data` in db/warehouse.sql;
the design is explained in docs/DATA_MODEL.md ("Unified data") and the storage
decision in docs/ARCHITECTURE.md.

THE PIECES, IN ORDER
--------------------
1. ALIGN NAMES. `QUICKBOOKS_FIELD_MAP` and `FILE_UPLOAD_FIELD_MAP` translate
   each source's field names into one shared vocabulary (TxnDate and order_date
   both become "transaction_date"). The same pairs are seeded into
   `warehouse.field_map`; a test keeps the two copies identical. Fields that
   are NOT in a map are still kept, named by snake_case of their path, and
   flagged `mapped = False` so nothing is ever lost.
2. FLATTEN. `flatten_quickbooks()` turns one QuickBooks Online API object
   (an Invoice, a Purchase, ...) into long rows: header fields get
   line_number 0, each item in its `Line` list gets line_number 1, 2, 3, ...
   `dataframe_to_unified()` does the same for a cleaned DataFrame (a melt).
3. PIVOT BACK. `unified_to_wide()` turns long rows back into a normal table
   (one row per record + line), so analysis code can keep using pandas.
4. NO DUPLICATES. `value_hash()` fingerprints a value; `plan_upsert()`
   compares a new batch with what is already stored and splits it into
   insert / update / unchanged (plus "stale": stored fields that disappeared).
   Re-loading the same data therefore inserts nothing and changes nothing.

This module is pure pandas: it never writes to a database. Storage wiring
(an asyncpg loader using the SQL upsert in db/warehouse.sql) comes later.

COLUMNS SET LATER, AT LOAD TIME
-------------------------------
row_id, client_id, account_id, consultant_id, source_id, added_at and
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

# ── Columns (same names as warehouse.unified_data) ──────────────────────────

# The kinds of value a field can hold. Exactly one of the value_* columns is
# filled on each row, chosen by value_type.
VALUE_TYPES = ("text", "number", "date", "timestamp", "boolean")

# value_type -> the column that holds the value.
VALUE_COLUMN = {
    "text": "value_text",
    "number": "value_number",
    "date": "value_date",
    "timestamp": "value_timestamp",
    "boolean": "value_bool",
}

# The columns this module produces, in the same order as the SQL table
# (minus the load-time columns listed in the module docstring).
UNIFIED_COLUMNS = [
    "record_id",           # source record's ID (e.g. "Invoice:130") or a content hash
    "line_number",         # 0 = header, 1.. = line items
    "event_type",          # e.g. "invoice"
    "event_subtype",       # e.g. "sent"
    "event_date",          # the date used in the clean data (e.g. TxnDate)
    "source_modified_at",  # when the source last changed the record (UTC)
    "field_name",          # unified name, e.g. "customer_name"
    "source_field",        # original name/path, e.g. "CustomerRef.name"
    "value_type",          # one of VALUE_TYPES
    "value_text",
    "value_number",
    "value_date",
    "value_timestamp",
    "value_bool",
    "value_hash",          # fingerprint of the normalized value (see value_hash)
    "mapped",              # True if field_name came from a field map
]

# One field of one record: the unique key inside one client + one source.
# In SQL the key is (client_id, source_id, record_id, line_number, field_name).
KEY_COLUMNS = ["record_id", "line_number", "field_name"]

# ── QuickBooks Online: which entity is which event ──────────────────────────
# entity name in the API -> (event_type, event_subtype, field holding the date).
# The type/subtype pairs already exist in warehouse.dim_event_type.
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

# ── Field maps: source name -> (unified field_name, value_type) ─────────────
# Paths use dots for nesting and leave out list positions: "Line.Amount" is the
# Amount of every item in the Line list. KEEP IN SYNC with the
# warehouse.field_map seed in db/warehouse.sql (tests/test_unified.py checks).
QUICKBOOKS_FIELD_MAP: dict[str, tuple[str, str]] = {
    # Header (line_number 0)
    "Id":                           ("source_record_id", "text"),
    "SyncToken":                    ("sync_token", "text"),
    "TxnDate":                      ("transaction_date", "date"),
    "DueDate":                      ("due_date", "date"),
    "DocNumber":                    ("reference_number", "text"),
    "TotalAmt":                     ("total_amount", "number"),
    "Balance":                      ("balance_due", "number"),
    "UnappliedAmt":                 ("unapplied_amount", "number"),
    "TxnTaxDetail.TotalTax":        ("tax_amount", "number"),
    "CurrencyRef.value":            ("currency", "text"),
    "CurrencyRef.name":             ("currency_name", "text"),
    "ExchangeRate":                 ("exchange_rate", "number"),
    "CustomerRef.value":            ("customer_source_id", "text"),
    "CustomerRef.name":             ("customer_name", "text"),
    "VendorRef.value":              ("vendor_source_id", "text"),
    "VendorRef.name":               ("vendor_name", "text"),
    "EntityRef.value":              ("payee_source_id", "text"),
    "EntityRef.name":               ("payee_name", "text"),
    "EntityRef.type":               ("payee_type", "text"),
    "AccountRef.value":             ("payment_account_id", "text"),
    "AccountRef.name":              ("payment_account_name", "text"),
    "DepositToAccountRef.name":     ("deposit_account_name", "text"),
    "PaymentType":                  ("payment_type", "text"),
    "PaymentMethodRef.name":        ("payment_method_name", "text"),
    "PaymentRefNum":                ("payment_reference", "text"),
    "DepartmentRef.name":           ("location_name", "text"),
    "SalesTermRef.name":            ("terms", "text"),
    "PrivateNote":                  ("memo", "text"),
    "CustomerMemo.value":           ("customer_memo", "text"),
    "BillEmail.Address":            ("customer_email", "text"),
    "BillAddr.Line1":               ("bill_address_line1", "text"),
    "BillAddr.City":                ("bill_city", "text"),
    "BillAddr.CountrySubDivisionCode": ("bill_state", "text"),
    "BillAddr.PostalCode":          ("bill_postal_code", "text"),
    "EmailStatus":                  ("email_status", "text"),
    "LinkedTxn.TxnId":              ("linked_txn_id", "text"),
    "LinkedTxn.TxnType":            ("linked_txn_type", "text"),
    "MetaData.CreateTime":          ("source_created_at", "timestamp"),
    "MetaData.LastUpdatedTime":     ("source_updated_at", "timestamp"),
    # Line items (line_number 1, 2, ...)
    "Line.Id":                      ("line_source_id", "text"),
    "Line.LineNum":                 ("line_num", "number"),
    "Line.DetailType":              ("line_detail_type", "text"),
    "Line.Amount":                  ("line_amount", "number"),
    "Line.Description":             ("line_description", "text"),
    "Line.SalesItemLineDetail.ItemRef.value":        ("item_source_id", "text"),
    "Line.SalesItemLineDetail.ItemRef.name":         ("item_name", "text"),
    "Line.SalesItemLineDetail.Qty":                  ("quantity", "number"),
    "Line.SalesItemLineDetail.UnitPrice":            ("unit_price", "number"),
    "Line.SalesItemLineDetail.ServiceDate":          ("service_date", "date"),
    "Line.SalesItemLineDetail.TaxCodeRef.value":     ("tax_code", "text"),
    "Line.SalesItemLineDetail.ItemAccountRef.name":  ("income_account_name", "text"),
    "Line.ItemBasedExpenseLineDetail.ItemRef.value": ("item_source_id", "text"),
    "Line.ItemBasedExpenseLineDetail.ItemRef.name":  ("item_name", "text"),
    "Line.ItemBasedExpenseLineDetail.Qty":           ("quantity", "number"),
    "Line.ItemBasedExpenseLineDetail.UnitPrice":     ("unit_price", "number"),
    "Line.ItemBasedExpenseLineDetail.TaxCodeRef.value": ("tax_code", "text"),
    "Line.AccountBasedExpenseLineDetail.AccountRef.value": ("expense_category_id", "text"),
    "Line.AccountBasedExpenseLineDetail.AccountRef.name":  ("expense_category", "text"),
    "Line.AccountBasedExpenseLineDetail.BillableStatus":   ("billable_status", "text"),
    "Line.AccountBasedExpenseLineDetail.TaxCodeRef.value": ("tax_code", "text"),
    "Line.DiscountLineDetail.DiscountPercent":       ("discount_percent", "number"),
    "Line.LinkedTxn.TxnId":                          ("linked_txn_id", "text"),
    "Line.LinkedTxn.TxnType":                        ("linked_txn_type", "text"),
}

# Generic uploaded-file headers, after snake_case ("Order Date" -> "order_date").
# A file row is treated like ONE line item, so "amount" lines up with
# QuickBooks' Line.Amount. Per-upload overrides go in `source_field_map`.
FILE_UPLOAD_FIELD_MAP: dict[str, tuple[str, str]] = {
    "date":            ("transaction_date", "date"),
    "order_date":      ("transaction_date", "date"),
    "transaction_date": ("transaction_date", "date"),
    "expense_date":    ("transaction_date", "date"),
    "invoice_date":    ("transaction_date", "date"),
    "due_date":        ("due_date", "date"),
    "order_id":        ("reference_number", "text"),
    "invoice_number":  ("reference_number", "text"),
    "receipt_number":  ("reference_number", "text"),
    "customer_id":     ("customer_source_id", "text"),
    "customer":        ("customer_name", "text"),
    "customer_name":   ("customer_name", "text"),
    "customer_email":  ("customer_email", "text"),
    "vendor":          ("vendor_name", "text"),
    "supplier":        ("vendor_name", "text"),
    "product":         ("item_name", "text"),
    "item":            ("item_name", "text"),
    "description":     ("line_description", "text"),
    "quantity":        ("quantity", "number"),
    "qty":             ("quantity", "number"),
    "unit_price":      ("unit_price", "number"),
    "price":           ("unit_price", "number"),
    "amount":          ("line_amount", "number"),
    "total":           ("total_amount", "number"),
    "total_amount":    ("total_amount", "number"),
    "tax":             ("tax_amount", "number"),
    "payment_method":  ("payment_method_name", "text"),
    "store":           ("location_name", "text"),
    "location":        ("location_name", "text"),
    "memo":            ("memo", "text"),
    "notes":           ("memo", "text"),
}

# system name in warehouse.dim_source / field_map -> its map.
FIELD_MAPS = {"quickbooks": QUICKBOOKS_FIELD_MAP, "file_upload": FILE_UPLOAD_FIELD_MAP}


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
    text is trimmed. This is what `value_hash` fingerprints.

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


def value_hash(value: Any, value_type: str | None = None) -> str:
    """
    Fingerprint a value (SHA-256 of its type + canonical form).

    Two loads of the same value give the same hash, so the database can skip
    the row (no rewrite, no modified_at bump). A different value, or the same
    digits as a different type (text "450" vs. number 450), gives a different hash.

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
    if isinstance(value, bool):                     # check before int: True is an int
        return "boolean"
    if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool):
        return "number"
    if isinstance(value, (pd.Timestamp, dt.datetime)):
        return "timestamp"
    if isinstance(value, dt.date):
        return "date"
    return "text"


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


def _make_row(*, record_id: str, line_number: int, event_type: str, event_subtype: str,
              event_date: Any, source_modified_at: Any, field_name: str, source_field: str,
              value: Any, value_type: str, mapped: bool) -> dict:
    """
    Build one long row (a dict with every UNIFIED_COLUMNS key).

    Args:
        record_id: the record's ID or content hash.
        line_number: 0 for header fields, 1.. for line items.
        event_type: e.g. "invoice".
        event_subtype: e.g. "sent".
        event_date: the record's date (datetime.date or None).
        source_modified_at: the source's last-updated time (pd.Timestamp or None).
        field_name: the unified field name.
        source_field: the original name/path.
        value: the raw value (converted here).
        value_type: the expected value_type.
        mapped: True if field_name came from a field map.

    Returns:
        dict: one row, with exactly one value_* column filled.

    Example:
        >>> row = _make_row(record_id="Invoice:1", line_number=0, event_type="invoice",
        ...                 event_subtype="sent", event_date=None, source_modified_at=None,
        ...                 field_name="total_amount", source_field="TotalAmt", value=10,
        ...                 value_type="number", mapped=True)
        >>> row["value_number"], row["value_text"]
        (10.0, None)
    """
    actual_type, typed = _typed_value(value, value_type)
    row = {
        "record_id": record_id,
        "line_number": int(line_number),
        "event_type": event_type,
        "event_subtype": event_subtype,
        "event_date": event_date,
        "source_modified_at": source_modified_at,
        "field_name": field_name,
        "source_field": source_field,
        "value_type": actual_type,
        "value_hash": value_hash(typed, actual_type),
        "mapped": mapped,
    }
    for kind, column in VALUE_COLUMN.items():
        row[column] = typed if kind == actual_type else None
    return {column: row[column] for column in UNIFIED_COLUMNS}   # same order as the table


def _to_frame(rows: Iterable[dict]) -> pd.DataFrame:
    """
    Put long rows into a DataFrame with the standard columns, in order.

    Args:
        rows: dicts made by `_make_row` (may be empty).

    Returns:
        pd.DataFrame: columns exactly UNIFIED_COLUMNS.

    Example:
        >>> list(_to_frame([]).columns) == UNIFIED_COLUMNS
        True
    """
    frame = pd.DataFrame(list(rows), columns=UNIFIED_COLUMNS)
    frame["line_number"] = frame["line_number"].astype("int64")
    # Keep value columns as plain Python objects so each row's value survives
    # untouched (a number column with None would otherwise turn into NaN).
    for column in VALUE_COLUMN.values():
        frame[column] = frame[column].astype(object)
    return frame


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


def _field_name_for(lookup_path: str, indexes: list[int], field_map: dict) -> tuple[str, str, bool]:
    """
    Pick the unified field name and expected type for one leaf.

    Args:
        lookup_path: the path without list positions, e.g. "LinkedTxn.TxnId".
        indexes: list positions on the way (the 2nd LinkedTxn adds "_2").
        field_map: source path -> (field_name, value_type).

    Returns:
        tuple: (field_name, value_type or "" to infer, mapped).

    Example:
        >>> _field_name_for("LinkedTxn.TxnId", [1], QUICKBOOKS_FIELD_MAP)
        ('linked_txn_id_2', 'text', True)
    """
    if lookup_path in field_map:
        name, kind = field_map[lookup_path]
        mapped = True
    else:
        name, kind, mapped = snake_case(lookup_path), "", False
    # The first item of a list keeps the plain name; later items get _2, _3...
    if any(indexes):
        name += "_" + "_".join(str(i + 1) for i in indexes)
    return name, kind, mapped


def flatten_quickbooks(entity_name: str, obj: dict, *, field_map: dict | None = None) -> list[dict]:
    """
    Flatten one QuickBooks Online API object into long rows (one per field).

    Header fields get line_number 0; the n-th item of the object's `Line` list
    gets line_number n (1-based, in the order QuickBooks returns them). Known
    fields are renamed through the field map; unknown fields are KEPT with a
    snake_case name and mapped=False. Missing/empty values make no row.

    The record_id is "<entity>:<Id>" because QuickBooks Ids are only unique
    within one entity type (Invoice 130 and Payment 130 can both exist).

    Args:
        entity_name: the API entity, a key of QUICKBOOKS_ENTITIES ("Invoice").
        obj: one object from a QueryResponse, e.g. response["QueryResponse"]["Invoice"][0].
        field_map: optional override of QUICKBOOKS_FIELD_MAP.

    Returns:
        list[dict]: long rows with the UNIFIED_COLUMNS keys.

    Raises:
        ValueError: if the entity is unknown or the object has no Id.

    Example:
        >>> rows = flatten_quickbooks("Invoice", {"Id": "130", "TxnDate": "2026-09-14",
        ...     "TotalAmt": 96.0, "Line": [{"Amount": 96.0, "DetailType": "SalesItemLineDetail"}]})
        >>> sorted((r["line_number"], r["field_name"]) for r in rows)[:3]
        [(0, 'source_record_id'), (0, 'total_amount'), (0, 'transaction_date')]
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

    # Header = everything except the Line list; then one group per line item.
    header = {k: v for k, v in obj.items() if k != "Line" and k not in QUICKBOOKS_SKIP_FIELDS}
    groups = [(0, header, [])]
    groups += [(n, line, ["Line"]) for n, line in enumerate(obj.get("Line") or [], start=1)]

    rows: list[dict] = []
    for line_number, content, prefix in groups:
        used: set[str] = set()       # field names already used on this line
        for lookup_path, source_field, indexes, value in _walk(content, prefix, []):
            if _is_missing(value):
                continue
            name, kind, mapped = _field_name_for(lookup_path, indexes, fmap)
            if name in used:         # two paths mapped to one name: keep both
                name, mapped = snake_case(source_field), False
            used.add(name)
            rows.append(_make_row(
                record_id=record_id, line_number=line_number,
                event_type=event_type, event_subtype=event_subtype,
                event_date=event_date, source_modified_at=source_modified_at,
                field_name=name, source_field=source_field,
                value=value, value_type=kind or _infer_type(value), mapped=mapped,
            ))
    return rows


def quickbooks_response_to_unified(response: dict) -> pd.DataFrame:
    """
    Flatten a whole QuickBooks Online query response into one long DataFrame.

    Walks `response["QueryResponse"]` and flattens every known entity list in
    it (Invoice, Purchase, ...). Unknown entity lists are skipped.

    Args:
        response: the parsed JSON of a QuickBooks query, e.g. the result of
            GET /v3/company/<realmId>/query?query=select * from Invoice.

    Returns:
        pd.DataFrame: long rows, columns UNIFIED_COLUMNS.

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

    Args:
        series: one column.

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
    date_column: str | None,
    id_column: str | None = None,
    line_column: str | None = None,
    source_field_map: dict[str, str | tuple[str, str]] | None = None,
    keep_identical_rows: bool = False,
) -> pd.DataFrame:
    """
    Melt a cleaned DataFrame into long rows (one row per cell with a value).

    record_id comes from `id_column` when the file has IDs. Without one, it is
    a SHA-256 hash of the row's content, so the SAME row uploaded twice gets
    the SAME record_id and `plan_upsert` sees it as already stored. Identical
    rows inside one file therefore collapse into one record, unless
    `keep_identical_rows=True` (then the 2nd copy gets "#2" added to its ID —
    still stable across re-uploads of the same file).

    line_number is 0, unless `line_column` is given, or `id_column` repeats
    (e.g. one row per invoice line): then rows of the same ID are numbered
    1, 2, 3... in file order.

    Column names are aligned through `source_field_map` first, then
    FILE_UPLOAD_FIELD_MAP (by snake_case name); any other column keeps its
    snake_case name with mapped=False. Values are typed by the column's dtype.

    Args:
        df: the cleaned data (one row per record or line).
        event_type: e.g. "transaction" (must exist in dim_event_type).
        event_subtype: e.g. "income".
        date_column: the column holding the date used in the clean data, or None.
        id_column: the column holding each record's ID, if the file has one.
        line_column: a column holding line numbers (integers), if any.
        source_field_map: optional {column: field_name} or
            {column: (field_name, value_type)} for this upload; value_type is
            ignored here (the column's dtype decides), the name is used.
        keep_identical_rows: keep exact duplicate rows as separate records.

    Returns:
        pd.DataFrame: long rows, columns UNIFIED_COLUMNS.

    Example:
        >>> sales = pd.DataFrame({"order_date": pd.to_datetime(["2026-09-01"]), "amount": [21.0]})
        >>> long = dataframe_to_unified(sales, event_type="transaction",
        ...     event_subtype="income", date_column="order_date")
        >>> long["field_name"].tolist()
        ['transaction_date', 'line_amount']
    """
    data = df.reset_index(drop=True)
    for column in (date_column, id_column, line_column):
        if column is not None and column not in data.columns:
            raise ValueError(f"Column {column!r} is not in the data")
    types = {c: _column_value_type(data[c]) for c in data.columns}

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

    # 4. One block of long rows per column (a melt), skipping empty cells.
    overrides = source_field_map or {}
    rows: list[dict] = []
    for column in data.columns:
        if column in overrides:
            target = overrides[column]
            name, mapped = (target[0] if isinstance(target, tuple) else target), True
        elif snake_case(column) in FILE_UPLOAD_FIELD_MAP:
            name, mapped = FILE_UPLOAD_FIELD_MAP[snake_case(column)][0], True
        else:
            name, mapped = snake_case(column), False
        kind = types[column]
        for i, value in data[column].items():
            if _is_missing(value):
                continue
            if kind == "number":
                value = value.item() if hasattr(value, "item") else value   # numpy -> Python
            rows.append(_make_row(
                record_id=record_ids[i], line_number=int(line_numbers[i]),
                event_type=event_type, event_subtype=event_subtype,
                event_date=event_dates[i], source_modified_at=None,
                field_name=name, source_field=str(column),
                value=value, value_type=kind, mapped=mapped,
            ))
    long = _to_frame(rows)
    # Two columns aligned to one name (e.g. "store" and "location") would break
    # the unique key; refuse loudly instead of silently dropping one.
    names = long.groupby("field_name")["source_field"].nunique()
    if (names > 1).any():
        raise ValueError(f"Several columns map to the same field: {sorted(names[names > 1].index)}; "
                         "pass source_field_map to rename one")
    return long


# ── Pivot back to wide ──────────────────────────────────────────────────────

def unified_to_wide(long_df: pd.DataFrame, *, use_source_names: bool = False) -> pd.DataFrame:
    """
    Pivot long rows back into a normal table: one row per record_id + line_number.

    Each field becomes a column, typed by its value_type (numbers numeric,
    dates datetime, booleans boolean). Rows keep the order they first appear
    in long_df. A field a record doesn't have is NaN/None in that row.

    Args:
        long_df: rows with at least KEY_COLUMNS, "value_type", the value_*
            columns and (with use_source_names) "source_field".
        use_source_names: name columns by source_field (the client's original
            header, e.g. "order_date") instead of the unified field_name.

    Returns:
        pd.DataFrame: columns record_id, line_number, then one per field.

    Example:
        >>> long = dataframe_to_unified(pd.DataFrame({"qty": [2, 3]}), event_type="transaction",
        ...     event_subtype="income", date_column=None)
        >>> unified_to_wide(long)["quantity"].tolist()
        [2.0, 3.0]
    """
    if long_df.empty:
        return pd.DataFrame(columns=["record_id", "line_number"])
    data = long_df.copy()
    column_key = "source_field" if use_source_names else "field_name"
    # Pick each row's value from the column its value_type names.
    data["_value"] = [row[VALUE_COLUMN[row["value_type"]]] for _, row in data.iterrows()]

    # Remember row and column order before pivoting (pivot would sort them).
    order = data[["record_id", "line_number"]].drop_duplicates()
    columns = list(dict.fromkeys(data[column_key]))
    types = data.groupby(column_key)["value_type"].agg(lambda s: s.mode().iloc[0])

    wide = data.pivot_table(index=["record_id", "line_number"], columns=column_key,
                            values="_value", aggfunc="first", dropna=False)
    wide = order.merge(wide.reset_index(), on=["record_id", "line_number"], how="left")
    wide = wide[["record_id", "line_number"] + columns]

    for column in columns:
        kind = types[column]
        if kind == "number":
            wide[column] = pd.to_numeric(wide[column], errors="coerce")
        elif kind == "date":
            wide[column] = pd.to_datetime(wide[column], errors="coerce")
        elif kind == "timestamp":
            wide[column] = pd.to_datetime(wide[column], errors="coerce", utc=True)
        elif kind == "boolean":
            wide[column] = wide[column].astype("boolean")
    wide.columns.name = None
    return wide.reset_index(drop=True)


# ── Anti-duplicate load planning ────────────────────────────────────────────

def _date_key(value: Any) -> str:
    """
    Write a date as "YYYY-MM-DD" ("" when missing), so stored and new dates compare fairly.

    Args:
        value: a date, Timestamp, ISO string, None or NaN.

    Returns:
        str: the ISO date or "".

    Example:
        >>> _date_key(None), _date_key("2026-09-14")
        ('', '2026-09-14')
    """
    return "" if _is_missing(value) else pd.Timestamp(value).date().isoformat()


def plan_upsert(new_long: pd.DataFrame, existing_long: pd.DataFrame | None) -> dict[str, pd.DataFrame]:
    """
    Decide what a load must do so the table never holds duplicate data.

    Both frames must be for ONE client and ONE source (the SQL key adds
    client_id and source_id to KEY_COLUMNS). Steps:

    1. De-duplicate the new batch: identical rows count once; if one field of
       one record appears twice with different values, the copy with the
       latest source_modified_at wins (otherwise the last one).
    2. Compare each new row with the stored row of the same key:
         not stored                       -> "insert"
         stored, same value_hash and date -> "unchanged" (nothing written,
                                             modified_at keeps its old value)
         stored, different value or date  -> "update"
    3. "stale": stored rows of records that ARE in the new batch but whose
       field is no longer there (a value was cleared or a line was removed in
       QuickBooks). The loader decides whether to delete them.

    This mirrors the SQL upsert in db/warehouse.sql
    (ON CONFLICT ... DO UPDATE ... WHERE value_hash IS DISTINCT FROM ...).

    Args:
        new_long: long rows about to be loaded.
        existing_long: long rows already stored (None or empty if none). Extra
            columns such as row_id are carried into "update" and "stale".

    Returns:
        dict[str, pd.DataFrame]: keys "insert", "update", "unchanged", "stale".

    Example:
        >>> batch = dataframe_to_unified(pd.DataFrame({"qty": [2]}), event_type="transaction",
        ...     event_subtype="income", date_column=None)
        >>> plan = plan_upsert(batch, batch)
        >>> len(plan["insert"]), len(plan["unchanged"])
        (0, 1)
    """
    new = new_long.copy()
    if "value_hash" not in new.columns or new["value_hash"].isna().any():
        new["value_hash"] = [value_hash(row[VALUE_COLUMN[row["value_type"]]], row["value_type"])
                             for _, row in new.iterrows()]

    # 1. Identical rows once; conflicting copies -> the newest wins.
    new = new.drop_duplicates(subset=KEY_COLUMNS + ["value_hash", "event_date"])
    if "source_modified_at" in new.columns and new["source_modified_at"].notna().any():
        modified = pd.to_datetime(new["source_modified_at"], utc=True, errors="coerce")
        new = new.assign(_modified=modified).sort_values("_modified", kind="stable", na_position="first")
        new = new.drop(columns="_modified")
    new = new.drop_duplicates(subset=KEY_COLUMNS, keep="last")

    empty = new.iloc[0:0]
    if existing_long is None or existing_long.empty:
        return {"insert": new.reset_index(drop=True), "update": empty.copy(),
                "unchanged": empty.copy(), "stale": empty.copy()}

    # 2. Compare by key. Stored extras (row_id, ...) come along with "_old" names.
    old = existing_long.copy()
    extras = [c for c in old.columns if c not in new.columns]
    stored = old[KEY_COLUMNS + ["value_hash", "event_date"] + extras].rename(
        columns={"value_hash": "_old_hash", "event_date": "_old_date"})
    merged = new.merge(stored, on=KEY_COLUMNS, how="left", indicator=True)
    is_new = merged["_merge"] == "left_only"
    same_hash = merged["value_hash"] == merged["_old_hash"]
    same_date = merged["event_date"].map(_date_key) == merged["_old_date"].map(_date_key)
    helper = ["_old_hash", "_old_date", "_merge"]

    insert = merged[is_new].drop(columns=helper + extras)
    unchanged = merged[~is_new & same_hash & same_date].drop(columns=helper)
    update = merged[~is_new & ~(same_hash & same_date)].drop(columns=helper)

    # 3. Stale: stored fields of re-sent records that the new batch no longer has.
    resent = old[old["record_id"].isin(new["record_id"])]
    keys_now = set(map(tuple, new[KEY_COLUMNS].astype(str).to_numpy()))
    is_stale = [tuple(k) not in keys_now for k in resent[KEY_COLUMNS].astype(str).to_numpy()]
    stale = resent[is_stale]

    return {name: frame.reset_index(drop=True) for name, frame in
            {"insert": insert, "update": update, "unchanged": unchanged, "stale": stale}.items()}
