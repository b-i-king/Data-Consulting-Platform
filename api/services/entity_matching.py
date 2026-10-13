"""
Entity matching: link records from two sources when there is no shared ID.

WHY THIS EXISTS
---------------
Integrations like QuickBooks and Gusto are most useful as ENHANCERS: they add
invoices, payments, or payroll to the client's own data. But the client's
spreadsheet rarely carries QuickBooks' customer ID. The same customer might be
"Acme Bakery, LLC" in QuickBooks and "ACME BAKERY" in the sales export. This
module finds those pairs.

THE ALGORITHM, STEP BY STEP
---------------------------
1. Pick the matching columns on each side (`detect_match_columns`): email,
   phone, and a name (company/customer/vendor name, or first + last name).
2. Normalize values so trivial differences disappear:
     names  -> lowercase, punctuation removed, business suffixes dropped
               ("Acme Bakery, LLC" -> "acme bakery")
     emails -> lowercase, trimmed
     phones -> digits only, last 10 digits (US numbers; drops "+1")
3. Match in order of confidence, each record matched at most once:
     a. exact email      -> score 100
     b. exact phone      -> score 100
     c. fuzzy name       -> rapidfuzz token_sort_ratio (0–100), which ignores
                            word order ("Bakery Acme" == "Acme Bakery")
   Fuzzy comparisons are "blocked" by the first letter of the name, so we only
   compare plausible pairs — this keeps big files fast. (A typo in the very
   first letter will be missed; that trade-off is usually worth it.)
4. Label each match:
     score >= auto_threshold (default 92)   -> "auto"   (accept)
     score >= review_threshold (default 80) -> "review" (a person should confirm)
     lower                                  -> not returned

Fuzzy matches are SUGGESTIONS. Always show "review" pairs to the client or a
consultant before using them in billing or reporting.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import pandas as pd
from rapidfuzz import fuzz, process

# Words that don't help tell businesses apart.
_BUSINESS_SUFFIXES = {
    "inc", "incorporated", "llc", "l l c", "ltd", "limited", "co", "company", "corp",
    "corporation", "pllc", "lp", "llp", "pc", "the", "and", "dba",
}

_EMAIL_HINTS = ("email", "e_mail", "mail")
_PHONE_HINTS = ("phone", "mobile", "cell", "tel")
_NAME_HINTS = ("company", "business", "customer", "client", "vendor", "supplier",
               "employee", "display_name", "full_name", "name")


@dataclass
class MatchColumns:
    """Which columns to use on one side. Any of them may be None."""

    email: str | None = None
    phone: str | None = None
    name: str | None = None            # a single name column, or
    first_name: str | None = None      # first + last, combined into a name
    last_name: str | None = None

    def usable(self) -> bool:
        return bool(self.email or self.phone or self.name or (self.first_name and self.last_name))


def _norm_col(c: str) -> str:
    return re.sub(r"[\s\-./]+", "_", str(c).strip().lower())


def detect_match_columns(df: pd.DataFrame) -> MatchColumns:
    """Guess the email / phone / name columns from column names."""
    cols = {_norm_col(c): c for c in df.columns}
    found = MatchColumns()
    for norm, original in cols.items():
        if found.email is None and any(h in norm for h in _EMAIL_HINTS):
            found.email = original
        elif found.phone is None and any(h in norm for h in _PHONE_HINTS):
            found.phone = original
    found.first_name = cols.get("first_name") or cols.get("firstname")
    found.last_name = cols.get("last_name") or cols.get("lastname")
    if not (found.first_name and found.last_name):
        found.first_name = found.last_name = None
        # Prefer the most specific name-like column ("company_name" over "name").
        for hint in _NAME_HINTS:
            match = next(
                (orig for norm, orig in cols.items()
                 if hint in norm and not norm.endswith("_id") and orig not in (found.email, found.phone)),
                None,
            )
            if match is not None:
                found.name = match
                break
    return found


def normalize_name(value) -> str:
    """'Acme Bakery, LLC' -> 'acme bakery'."""
    if pd.isna(value):
        return ""
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value).lower().replace("&", " and "))
    words = [w for w in text.split() if w not in _BUSINESS_SUFFIXES]
    return " ".join(words)


def normalize_email(value) -> str:
    return "" if pd.isna(value) else str(value).strip().lower()


def normalize_phone(value) -> str:
    """'(404) 555-0100' / '+1 404 555 0100' -> '4045550100'."""
    if pd.isna(value):
        return ""
    digits = re.sub(r"\D", "", str(value))
    return digits[-10:] if len(digits) >= 10 else ""


def _keys(df: pd.DataFrame, cols: MatchColumns) -> pd.DataFrame:
    """One row per record with normalized email / phone / name."""
    out = pd.DataFrame(index=df.index)
    out["email"] = df[cols.email].map(normalize_email) if cols.email else ""
    out["phone"] = df[cols.phone].map(normalize_phone) if cols.phone else ""
    if cols.first_name and cols.last_name:
        full = df[cols.first_name].fillna("").astype(str) + " " + df[cols.last_name].fillna("").astype(str)
        out["name"] = full.map(normalize_name)
    elif cols.name:
        out["name"] = df[cols.name].map(normalize_name)
    else:
        out["name"] = ""
    return out


def match_records(
    left: pd.DataFrame,
    right: pd.DataFrame,
    left_cols: MatchColumns | None = None,
    right_cols: MatchColumns | None = None,
    auto_threshold: int = 92,
    review_threshold: int = 80,
) -> pd.DataFrame:
    """
    Link rows of `left` to rows of `right` (steps 1–4 above).

    Returns a DataFrame with columns:
        left_index, right_index, method ("email" | "phone" | "name"),
        score (0–100), status ("auto" | "review"), left_value, right_value
    Each left row and each right row appears at most once.
    """
    left_cols = left_cols or detect_match_columns(left)
    right_cols = right_cols or detect_match_columns(right)
    lk, rk = _keys(left, left_cols), _keys(right, right_cols)

    matches: list[dict] = []
    used_left: set = set()
    used_right: set = set()

    # 3a / 3b — exact matches on email, then phone.
    for field in ("email", "phone"):
        lookup: dict[str, object] = {}
        for idx, val in rk[field].items():
            if val and idx not in used_right:
                lookup.setdefault(val, idx)
        for idx, val in lk[field].items():
            if not val or idx in used_left:
                continue
            r_idx = lookup.get(val)
            if r_idx is not None and r_idx not in used_right:
                matches.append({"left_index": idx, "right_index": r_idx, "method": field,
                                "score": 100.0, "status": "auto", "left_value": val, "right_value": val})
                used_left.add(idx)
                used_right.add(r_idx)

    # 3c — fuzzy names, compared only within the same first-letter block.
    blocks: dict[str, dict] = {}
    for idx, name in rk["name"].items():
        if name and idx not in used_right:
            blocks.setdefault(name[:1], {})[idx] = name
    for idx, name in lk["name"].items():
        if not name or idx in used_left:
            continue
        candidates = {k: v for k, v in blocks.get(name[:1], {}).items() if k not in used_right}
        if not candidates:
            continue
        best = process.extractOne(name, candidates, scorer=fuzz.token_sort_ratio, score_cutoff=review_threshold)
        if best is None:
            continue
        right_name, score, r_idx = best
        matches.append({"left_index": idx, "right_index": r_idx, "method": "name", "score": round(score, 1),
                        "status": "auto" if score >= auto_threshold else "review",
                        "left_value": name, "right_value": right_name})
        used_left.add(idx)
        used_right.add(r_idx)

    columns = ["left_index", "right_index", "method", "score", "status", "left_value", "right_value"]
    return pd.DataFrame(matches, columns=columns)
