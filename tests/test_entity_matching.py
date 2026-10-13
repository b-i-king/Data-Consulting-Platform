"""
Tests for fuzzy entity matching (linking records that have no shared ID),
e.g. a client's sales export vs. their QuickBooks customer list.
"""

import pandas as pd

from api.services.entity_matching import (
    detect_match_columns,
    match_records,
    normalize_name,
    normalize_phone,
)

SALES = pd.DataFrame({
    "Customer": ["Acme Bakery", "Blue Door Cafe", "Cora's Flowers", "Delta Plumbing", "Zed Unknown"],
    "Customer Email": ["orders@acme.com", None, None, None, None],
    "Phone": [None, "(404) 555-0100", None, None, None],
})
QUICKBOOKS = pd.DataFrame({
    "DisplayName": ["ACME BAKERY, LLC", "Blue Door Café Inc", "Coras Flower Shop", "Delta Plumbing Co.", "Other Corp"],
    "PrimaryEmailAddr": ["Orders@Acme.com", None, None, None, None],
    "PrimaryPhone": [None, "+1 404 555 0100", None, None, None],
})


def test_normalizers():
    assert normalize_name("ACME BAKERY, LLC") == "acme bakery"
    # "&" and "and" are treated the same (both dropped), so these two agree:
    assert normalize_name("Smith & Sons Co.") == normalize_name("Smith and Sons") == "smith sons"
    assert normalize_phone("+1 (404) 555-0100") == "4045550100"
    assert normalize_phone("555-0100") == ""      # too short to trust


def test_detects_columns_by_name():
    cols = detect_match_columns(SALES)
    assert (cols.email, cols.phone, cols.name) == ("Customer Email", "Phone", "Customer")
    split = detect_match_columns(pd.DataFrame({"first_name": [], "last_name": [], "email": []}))
    assert (split.first_name, split.last_name, split.email) == ("first_name", "last_name", "email")


def test_match_records_end_to_end():
    m = match_records(SALES, QUICKBOOKS).set_index("left_index")
    assert m.loc[0, "method"] == "email" and m.loc[0, "right_index"] == 0      # exact email wins
    assert m.loc[1, "method"] == "phone" and m.loc[1, "right_index"] == 1      # exact phone
    assert m.loc[3, "right_index"] == 3 and m.loc[3, "status"] == "auto"      # "Delta Plumbing Co." suffix ignored
    assert m.loc[2, "right_index"] == 2                                       # fuzzy, maybe needs review
    assert 4 not in m.index                                                   # no false match for "Zed Unknown"
    assert m["right_index"].is_unique                                         # one-to-one


def test_review_band():
    left = pd.DataFrame({"name": ["Sunrise Dental Group"]})
    right = pd.DataFrame({"name": ["Sunrise Dental"]})
    [row] = match_records(left, right).to_dict("records")
    assert row["status"] == "review" and 80 <= row["score"] < 92
