"""
Plan a load into the warehouse — and explain every change BEFORE anything is stored.

WHAT THIS FILE DOES
-------------------
Data Engineering clients can have their cleaned data stored in the shared
warehouse (db/warehouse.sql, docs/DATA_MODEL.md). A spreadsheet doesn't fit
those tables as-is: one sales file has to become a customers table, a
products table, and one "event" row per sale. This module works out that
TRANSFORMATION and produces:

  * the STEPS, each with a plain-English WHY (shown to the client),
  * a PREVIEW of the first rows of each warehouse table as it will look,
  * the COUNTS (rows to load, rows skipped and why, customers, products),
  * a FINGERPRINT of the plan.

The client reads the preview and either accepts or walks away. Only an
accepted plan is loaded (api/services/warehouse_store.py), and only if its
fingerprint still matches — so what gets stored is exactly what they saw.

Everything here is plain pandas and stores nothing.

THE MAPPING
-----------
Each warehouse field ("role") is filled from one of the client's columns:

    date             when it happened (required)          -> fact_event.date_key
    amount           money                                -> fact_event.amount
    quantity         units                                -> fact_event.quantity
    score            CSAT / rating / NPS                  -> fact_event.score
    record_id        the row's own id (order line id)     -> fact_event.source_record_id
    reference        invoice # / order #                  -> fact_event.reference_number
    status           open / paid / ...                    -> fact_event.status
    customer_id      the customer's id in their system    -> dim_customer.source_customer_id
    customer_name    \
    customer_email    }-> dim_customer
    customer_phone   /
    product          product or service name              -> dim_product.name
    product_category                                      -> dim_product.category

The columns are guessed from their names (and types); the client can change
any guess in the preview. Columns not used go into `attributes` (JSON), so
nothing is lost and no client ever needs tables of their own.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field

import pandas as pd

from api.services.entity_matching import normalize_email, normalize_name
from data_skills._core import as_datetime, date_columns, numeric_columns

# The event types a file can be loaded as. Mirrors the seed rows of
# warehouse.dim_event_type in db/warehouse.sql ("type/subtype").
EVENT_TYPES: dict[str, dict] = {
    "transaction/income": {"label": "Sales / income (money in)", "measure": "amount"},
    "transaction/expense": {"label": "Expenses (money out)", "measure": "amount"},
    "transaction/refund": {"label": "Refunds to customers", "measure": "amount"},
    "invoice/sent": {"label": "Invoices you sent", "measure": "amount"},
    "invoice/received": {"label": "Invoices (bills) you received", "measure": "amount"},
    "invoice/paid": {"label": "Invoices paid", "measure": "amount"},
    "feedback/csat": {"label": "Customer satisfaction (CSAT) scores", "measure": "score"},
    "feedback/rating": {"label": "Ratings / reviews", "measure": "score"},
    "feedback/nps": {"label": "Net Promoter Score responses", "measure": "score"},
    "inventory/adjustment": {"label": "Inventory changes", "measure": "quantity"},
}

# A negative amount means the money went the OTHER way. The warehouse keeps
# amounts positive, so the row moves to the opposite event type.
FLIP_NEGATIVE = {
    "transaction/income": "transaction/refund",
    "transaction/expense": "transaction/income",
    "transaction/refund": "transaction/income",
}

# (role id, label shown in the preview, words in a column name that suggest it)
ROLES: list[tuple[str, str, tuple[str, ...]]] = [
    ("date", "Date (required)", ("order_date", "date", "day", "created", "time", "_at")),
    ("amount", "Amount ($)", ("amount", "total", "revenue", "sales", "price", "cost", "paid", "balance", "value")),
    ("quantity", "Quantity", ("quantity", "qty", "units")),
    ("score", "Score (CSAT / rating / NPS)", ("csat", "score", "rating", "nps", "stars")),
    ("record_id", "Row ID", ("line_id", "transaction_id", "record_id", "row_id", "event_id")),
    ("reference", "Reference # (order / invoice)", ("order_id", "invoice", "order_number", "receipt", "reference")),
    ("status", "Status", ("status", "state_of")),
    ("customer_id", "Customer ID", ("customer_id", "client_id", "cust_id")),
    ("customer_name", "Customer name", ("customer_name", "client_name", "displayname", "full_name", "company", "name")),
    ("customer_email", "Customer email", ("email", "e_mail")),
    ("customer_phone", "Customer phone", ("phone", "mobile", "cell")),
    ("product", "Product / service", ("product", "item", "service", "sku")),
    ("product_category", "Product category", ("category", "product_type", "department")),
]
ROLE_IDS = [r[0] for r in ROLES]
ID_ROLES = {"record_id", "reference", "customer_id"}   # the only roles an "..._id" column may fill
PREVIEW_ROWS = 5
FIRST_DAY, LAST_DAY = pd.Timestamp("2000-01-01"), pd.Timestamp("2040-12-31")   # dim_date's range


@dataclass
class LoadPlan:
    """A worked-out load: what the client sees, plus the rows ready to write."""

    source_name: str
    event_type: str
    mapping: dict[str, str | None]
    columns: list[str]
    steps: list[dict]
    tables: dict[str, dict]
    counts: dict[str, int]
    skipped: dict[str, int]
    fingerprint: str
    problem: str | None = None
    # Prepared rows for warehouse_store (not sent to the browser).
    facts: list[dict] = field(default_factory=list)
    customers: list[dict] = field(default_factory=list)
    products: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        """
        The plan as sent to the browser (web/lib/types.ts: LoadPlan).

        Returns:
            dict: everything except the prepared rows.
        """
        return {
            "source_name": self.source_name, "event_type": self.event_type,
            "event_types": [{"id": k, **v} for k, v in EVENT_TYPES.items()],
            "mapping": self.mapping, "columns": self.columns,
            "roles": [{"id": rid, "label": label} for rid, label, _ in ROLES],
            "steps": self.steps, "tables": self.tables, "counts": self.counts,
            "skipped": self.skipped, "fingerprint": self.fingerprint,
            "problem": self.problem, "ready": self.problem is None,
        }


def _norm(col: str) -> str:
    """Lowercase a column name with spaces/dashes as underscores, for hint matching."""
    return str(col).strip().lower().replace(" ", "_").replace("-", "_")


def suggest_mapping(df: pd.DataFrame) -> dict[str, str | None]:
    """
    Guess which column fills each warehouse field, from column names and types.

    Each column is used at most once, roles are filled in the order of ROLES,
    and earlier hints win ("order_date" before any other "date"). The date must
    actually hold dates; amount / quantity / score must be numbers.

    Args:
        df: pd.DataFrame, the cleaned table.

    Returns:
        dict: role id -> column name (or None when nothing fits).

    Example:
        >>> suggest_mapping(pd.DataFrame({"order_date": ["2024-10-11"], "amount": [21.0],
        ...                               "product": ["Sourdough Loaf"]}))["product"]
        'product'
    """
    dates = set(date_columns(df))
    numbers = set(numeric_columns(df, exclude_ids=False))
    used: set[str] = set()
    mapping: dict[str, str | None] = {}
    for role, _, hints in ROLES:
        choice = None
        for hint in hints:
            for col in df.columns:
                name = _norm(col)
                if col in used or hint not in name:
                    continue
                if role == "date" and col not in dates:
                    continue
                if role in ("amount", "quantity", "score") and col not in numbers:
                    continue
                if role != "date" and col in dates:
                    continue
                if role not in ID_ROLES and name.endswith("_id"):
                    continue    # "line_item_id" is not a product name
                choice = col
                break
            if choice:
                break
        if role == "date" and choice is None and dates:
            choice = next(c for c in df.columns if c in dates)   # any date column will do
        mapping[role] = choice
        if choice:
            used.add(choice)
    return mapping


def suggest_event_type(mapping: dict[str, str | None], columns: list[str] | None = None,
                       source_name: str = "") -> str:
    """
    Guess what the rows ARE from the fields found, the column names and the file name.

    A score and no amount -> CSAT feedback; expense-like words (expense,
    vendor, supplier, bill, spend) in a column or the file name -> expenses;
    otherwise sales / income. The client can change the guess in the preview.

    Args:
        mapping: dict, role id -> column name.
        columns: list[str] | None, every column name in the table.
        source_name: str, the table's label or file name.

    Returns:
        str: an EVENT_TYPES key.

    Example:
        >>> suggest_event_type({"score": "csat", "amount": None})
        'feedback/csat'
        >>> suggest_event_type({"amount": "amount"}, ["expense_date", "vendor", "amount"])
        'transaction/expense'
    """
    if mapping.get("score") and not mapping.get("amount"):
        return "feedback/nps" if "nps" in _norm(mapping["score"]) else "feedback/csat"
    words = " ".join(_norm(c) for c in [*(columns or []), mapping.get("amount") or "", source_name])
    if any(w in words for w in ("expense", "vendor", "supplier", "bill", "spend")):
        return "transaction/expense"
    return "transaction/income"


def _clean_text(value) -> str | None:
    """A cell as trimmed text; None for blanks and missing values."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].lstrip("-").isdigit():   # 1155.0 (an id read as a float) -> 1155
        text = text[:-2]
    return text or None


def _json_value(value):
    """One cell made JSON-safe for `attributes` (dates as ISO text; NaN dropped by the caller)."""
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):     # numpy number -> plain Python number
        return value.item()
    return value


def _table(rows: list[dict], columns: list[str], total: int) -> dict:
    """A preview table: the first PREVIEW_ROWS rows and the full count."""
    return {"columns": columns, "rows": [{c: r.get(c) for c in columns} for r in rows[:PREVIEW_ROWS]], "total": total}


def build_plan(df: pd.DataFrame, source_name: str, mapping: dict | None = None,
               event_type: str | None = None) -> LoadPlan:
    """
    Work out how a cleaned table becomes warehouse rows, with the WHY of each step.

    Args:
        df: pd.DataFrame, the cleaned table (the same data the client previewed).
        source_name: str, what the source is called (the table's label / file name).
        mapping: dict | None, role id -> column the client chose. Missing roles
            are guessed; a role sent as "" or None is switched OFF.
        event_type: str | None, an EVENT_TYPES key; guessed when None.

    Returns:
        LoadPlan: steps, previews, counts, fingerprint and the prepared rows.
        `problem` is set (and nothing can be loaded) when no date column is
        mapped or no row is left to load.

    Raises:
        ValueError: for an unknown event type or a mapped column that doesn't exist.

    Example:
        >>> plan = build_plan(pd.DataFrame({"order_date": ["2024-10-11"], "amount": [21.0]}), "sales.csv")
        >>> plan.event_type, plan.counts["rows_to_load"]
        ('transaction/income', 1)
    """
    guessed = suggest_mapping(df)
    chosen = dict(guessed)
    for role, col in (mapping or {}).items():
        if role not in ROLE_IDS:
            raise ValueError(f"Unknown field '{role}'.")
        if col and col not in df.columns:
            raise ValueError(f"Column '{col}' isn't in this table.")
        chosen[role] = col or None
    if not chosen["product"]:
        chosen["product_category"] = None   # a category with no product stays in the extras
    event_type = event_type or suggest_event_type(chosen, [str(c) for c in df.columns], source_name)
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Unknown event type '{event_type}'.")
    measure = EVENT_TYPES[event_type]["measure"]
    steps: list[dict] = []
    skipped: dict[str, int] = {}
    m = chosen

    def plan_with_problem(problem: str) -> LoadPlan:
        return LoadPlan(source_name=source_name, event_type=event_type, mapping=m, columns=[str(c) for c in df.columns],
                        steps=steps, tables={}, counts={"rows_in": len(df), "rows_to_load": 0, "rows_skipped": len(df),
                                                        "customers": 0, "products": 0},
                        skipped=skipped, fingerprint="", problem=problem)

    steps.append({
        "title": f"Store each row as one \"{EVENT_TYPES[event_type]['label']}\" event",
        "why": "The warehouse keeps every kind of business event (sales, expenses, invoices, feedback) in ONE "
               "table, fact_event. A type and subtype say what each row is, so one dashboard or query works "
               "across all of your sources.",
    })
    if not m["date"]:
        return plan_with_problem("Pick the date column: every event needs the day it happened.")

    # 1. Dates -> date keys, dropping rows that can't be placed on the calendar.
    when = as_datetime(df[m["date"]])
    no_date = when.isna()
    out_of_range = ~no_date & ((when < FIRST_DAY) | (when > LAST_DAY))
    if no_date.any():
        skipped["no readable date"] = int(no_date.sum())
    if out_of_range.any():
        skipped["date outside 2000–2040"] = int(out_of_range.sum())
    keep = ~(no_date | out_of_range)
    rows = df[keep].copy()
    when = when[keep]
    steps.append({
        "title": f"Turn \"{m['date']}\" into a date key (YYYYMMDD)",
        "why": "Each event links to the calendar table (dim_date), which already knows the month, quarter, "
               "week and weekday — so reports can group by any of them without recalculating.",
    })
    if skipped:
        steps.append({
            "title": f"Skip {sum(skipped.values()):,} row(s) that can't be placed on the calendar",
            "why": "An event must happen on a day the calendar table knows. "
                   + "; ".join(f"{n:,} with {reason}" for reason, n in skipped.items()) + ".",
        })
    if rows.empty:
        return plan_with_problem("No rows have a usable date, so there is nothing to load.")

    # 2. The measure (amount / quantity / score), and negative amounts.
    types = pd.Series(event_type, index=rows.index)
    amount = pd.to_numeric(rows[m["amount"]], errors="coerce") if m["amount"] else pd.Series(pd.NA, index=rows.index)
    if m["amount"]:
        negative = amount < 0
        n_neg = int(negative.sum())
        if n_neg:
            flipped = FLIP_NEGATIVE.get(event_type)
            if flipped:
                types[negative] = flipped
            steps.append({
                "title": f"Make {n_neg:,} negative amount(s) positive"
                         + (f" and store them as \"{EVENT_TYPES[flipped]['label']}\"" if flipped else ""),
                "why": "The warehouse stores every amount as a positive number; the event type says which way "
                       "the money moved (in or out). That keeps totals right when sources mix signs, and net "
                       "cash is simply money in minus money out.",
            })
        amount = amount.abs()
    elif measure == "amount":
        steps.append({"title": "No amount column picked",
                      "why": "These events will be counted but carry no dollar value. Pick an amount column to "
                             "include money."})
    quantity = pd.to_numeric(rows[m["quantity"]], errors="coerce") if m["quantity"] else None
    score = pd.to_numeric(rows[m["score"]], errors="coerce") if m["score"] else None

    # 3. Customers: one row per real customer, events point to it.
    def customer_key(r) -> tuple[str | None, str]:
        if m["customer_id"] and (v := _clean_text(r[m["customer_id"]])):
            return f"id:{v}", "source_id"
        if m["customer_email"] and (v := normalize_email(r[m["customer_email"]])):
            return f"email:{v}", "exact_email"
        if m["customer_name"] and (v := normalize_name(r[m["customer_name"]])):
            return f"name:{v}", "unmatched"
        return None, ""

    customers: dict[str, dict] = {}
    cust_keys: list[str | None] = []
    for _, r in rows.iterrows():
        key, how = customer_key(r)
        cust_keys.append(key)
        if key and key not in customers:
            customers[key] = {
                "source_customer_id": key, "match_status": how,
                "name": _clean_text(r[m["customer_name"]]) if m["customer_name"] else None,
                "email": normalize_email(r[m["customer_email"]]) or None if m["customer_email"] else None,
                "phone": _clean_text(r[m["customer_phone"]]) if m["customer_phone"] else None,
            }
    if customers:
        used = [m[k] for k in ("customer_id", "customer_name", "customer_email", "customer_phone") if m[k]]
        steps.append({
            "title": f"Move customers into their own table: {len(customers):,} customer(s) from {len(rows):,} row(s)",
            "why": f"Customer details ({', '.join(used)}) are stored ONCE in dim_customer and every sale points to "
                   "that record. A fixed email or name then updates everywhere, and customers from other sources "
                   "(QuickBooks later) can be matched to the same person.",
        })

    # 4. Products: one row per product, with its category.
    products: dict[str, dict] = {}
    prod_keys: list[str | None] = []
    for _, r in rows.iterrows():
        name = _clean_text(r[m["product"]]) if m["product"] else None
        key = f"name:{name.lower()}" if name else None
        prod_keys.append(key)
        if key and key not in products:
            products[key] = {"source_product_id": key, "name": name,
                             "category": _clean_text(r[m["product_category"]]) if m["product_category"] else None}
    if products:
        steps.append({
            "title": f"Move products into their own table: {len(products):,} product(s)",
            "why": "Product names and categories are stored once in dim_product, so renaming a product or "
                   "changing its category doesn't mean editing every sale.",
        })

    # 5. A stable id per row, so loading the same file twice never duplicates.
    record_col = m["record_id"]
    unique_ids = record_col and rows[record_col].notna().all() and rows[record_col].is_unique
    mapped_cols = {c for c in m.values() if c}
    extras = [c for c in df.columns if c not in mapped_cols]
    if unique_ids:
        record_ids = [_clean_text(v) or "" for v in rows[record_col]]
        steps.append({"title": f"Use \"{record_col}\" as each row's ID",
                      "why": "Loading the same rows again (say, next month's export that overlaps) won't create "
                             "duplicates: a row whose ID is already stored is skipped."})
    else:
        snapshot = rows.astype("string").fillna("")
        record_ids = [hashlib.sha1("\x1f".join(vals).encode()).hexdigest()[:20] for vals in snapshot.itertuples(index=False)]
        why = ("Each row gets an ID made from its contents (a fingerprint), so loading the same rows again "
               "won't create duplicates.")
        if record_col:
            why += f" \"{record_col}\" can't be used: some values are blank or repeated."
        steps.append({"title": "Give each row a fingerprint ID", "why": why})
    if extras:
        steps.append({
            "title": f"Keep {len(extras)} other column(s) as extras: {', '.join(map(str, extras[:8]))}"
                     + ("…" if len(extras) > 8 else ""),
            "why": "Columns that aren't standard warehouse fields go into each event's \"attributes\" (JSON). "
                   "Nothing is lost, and no client ever needs tables or columns of their own.",
        })
    steps.append({
        "title": "Tag every row with your client ID",
        "why": "All clients share the same tables; row-level security means a query only ever sees your rows.",
    })

    # The fact rows, ready to write.
    facts: list[dict] = []
    for i, (idx, r) in enumerate(rows.iterrows()):
        attrs = {str(c): _json_value(r[c]) for c in extras if not (not isinstance(r[c], str) and pd.isna(r[c]))}
        amt = amount.loc[idx]
        facts.append({
            "event_type": types.loc[idx],
            "date_key": int(when.loc[idx].strftime("%Y%m%d")),
            "source_record_id": record_ids[i],
            "customer": cust_keys[i], "product": prod_keys[i],
            "amount": None if pd.isna(amt) else round(float(amt), 2),
            "quantity": None if quantity is None or pd.isna(quantity.loc[idx]) else round(float(quantity.loc[idx]), 3),
            "score": None if score is None or pd.isna(score.loc[idx]) else round(float(score.loc[idx]), 2),
            "reference_number": _clean_text(r[m["reference"]]) if m["reference"] else None,
            "status": _clean_text(r[m["status"]]) if m["status"] else None,
            "attributes": attrs,
        })
    # Two rows with the same id: only the first can be stored (same as the database's UNIQUE rule).
    seen: set[str] = set()
    unique_facts = []
    for f in facts:
        if f["source_record_id"] not in seen:
            seen.add(f["source_record_id"])
            unique_facts.append(f)
    if len(unique_facts) < len(facts):
        skipped["repeated row ID"] = len(facts) - len(unique_facts)
        steps.insert(-1, {
            "title": f"Skip {skipped['repeated row ID']:,} repeated row(s)",
            "why": "These rows have the same ID as an earlier row (exact copies), so only the first is stored.",
        })
    facts = unique_facts

    fact_cols = ["date_key", "event_type", "amount", "quantity", "score", "customer", "product",
                 "reference_number", "status", "source_record_id", "attributes"]
    preview_facts = [{**f, "attributes": json.dumps(f["attributes"], default=str)} for f in facts]
    tables = {"fact_event": _table(preview_facts, fact_cols, len(facts))}
    if customers:
        tables["dim_customer"] = _table(list(customers.values()),
                                        ["source_customer_id", "name", "email", "phone", "match_status"], len(customers))
    if products:
        tables["dim_product"] = _table(list(products.values()), ["source_product_id", "name", "category"], len(products))

    # The fingerprint covers the choices AND the data, so a changed file or
    # mapping can't be loaded under an old acceptance.
    digest = hashlib.sha256(json.dumps(
        {"source": source_name, "type": event_type, "mapping": m,
         "rows": [(f["source_record_id"], f["event_type"], f["amount"]) for f in facts]},
        sort_keys=True, default=str).encode()).hexdigest()[:24]

    return LoadPlan(
        source_name=source_name, event_type=event_type, mapping=m, columns=[str(c) for c in df.columns],
        steps=steps, tables=tables,
        counts={"rows_in": len(df), "rows_to_load": len(facts), "rows_skipped": sum(skipped.values()),
                "customers": len(customers), "products": len(products)},
        skipped=skipped, fingerprint=digest, facts=facts,
        customers=list(customers.values()), products=list(products.values()),
    )
