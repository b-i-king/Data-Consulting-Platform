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
        """
        Say whether this side has at least one column we can match on.

        A side is usable when it has an email column, a phone column, a single name
        column, or BOTH a first-name and a last-name column (a first name alone is
        too weak to link records).

        Returns:
            bool: True if `match_records` has something to compare on this side.

        Example:
            >>> MatchColumns(email="Email").usable()
            True
            >>> MatchColumns(first_name="first_name").usable()
            False
        """
        return bool(self.email or self.phone or self.name or (self.first_name and self.last_name))


def _norm_col(c: str) -> str:
    """
    Normalize a column name for hint matching (lowercase, separators -> "_").

    Spaces, dashes, dots and slashes become one underscore, so "E-mail Address"
    and "e_mail_address" look the same to `detect_match_columns`. Used only for
    comparing; the original column name is what gets returned to callers.

    Args:
        c: str: the column name as it appears in the DataFrame (any value is
            converted with `str()` first).

    Returns:
        str: the normalized name.

    Example:
        >>> _norm_col("E-mail Address")
        'e_mail_address'
    """
    return re.sub(r"[\s\-./]+", "_", str(c).strip().lower())


def detect_match_columns(df: pd.DataFrame) -> MatchColumns:
    """
    Guess the email / phone / name columns from column names.

    Rules, applied to the normalized names (see `_norm_col`):
    - email: the first column whose name contains "email", "e_mail" or "mail"
    - phone: the first (non-email) column containing "phone", "mobile", "cell" or "tel"
    - name: if the table has BOTH first_name/firstname and last_name/lastname, those
      two are used together; otherwise the first column matching the most specific
      hint in `_NAME_HINTS` ("company" before "customer" before plain "name"),
      skipping columns ending in "_id" and the email/phone columns already chosen.
    Only the column NAMES are inspected, never the values.

    Args:
        df: pd.DataFrame: one side of the match (e.g. the client's sales export or a
            QuickBooks customer list).

    Returns:
        MatchColumns: the original column names found; any of them may be None.

    Example:
        >>> df = pd.DataFrame(columns=["Customer Name", "Email", "Phone", "customer_id"])
        >>> detect_match_columns(df)
        MatchColumns(email='Email', phone='Phone', name='Customer Name', first_name=None, last_name=None)
    """
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
    """
    Normalize a person or business name so trivial differences disappear.

    Steps: lowercase; "&" becomes "and"; every character that is not a letter,
    digit or space becomes a space; then words in `_BUSINESS_SUFFIXES` ("llc",
    "inc", "co", "the", "and", ...) are dropped and the rest are joined with
    single spaces. Accented letters are removed, not converted ("Café" -> "caf").

    Args:
        value: the raw cell value (str, number, or missing). Missing values
            (None / NaN) give "".

    Returns:
        str: the normalized name, or "" for a missing value.

    Example:
        >>> normalize_name("Acme Bakery, LLC")
        'acme bakery'
        >>> normalize_name("Smith & Sons Co.")
        'smith sons'
    """
    if pd.isna(value):
        return ""
    text = re.sub(r"[^a-z0-9 ]+", " ", str(value).lower().replace("&", " and "))
    words = [w for w in text.split() if w not in _BUSINESS_SUFFIXES]
    return " ".join(words)


def normalize_email(value) -> str:
    """
    Normalize an email address: trim spaces and lowercase it.

    Email addresses are effectively case-insensitive, so "Ann@Example.COM " and
    "ann@example.com" should match exactly.

    Args:
        value: the raw cell value (str or missing). Missing values (None / NaN) give "".

    Returns:
        str: the trimmed, lowercase email, or "" for a missing value.

    Example:
        >>> normalize_email(" Ann@Example.COM ")
        'ann@example.com'
    """
    return "" if pd.isna(value) else str(value).strip().lower()


def normalize_phone(value) -> str:
    """
    Normalize a phone number to its last 10 digits (US numbers).

    All non-digit characters are removed, then the last 10 digits are kept, which
    drops a leading "+1" country code. Numbers with fewer than 10 digits (for
    example a 7-digit local number) cannot be compared reliably and give "".

    Args:
        value: the raw cell value (str, number, or missing). Missing values give "".

    Returns:
        str: 10 digits, or "" when the value is missing or too short.

    Example:
        >>> normalize_phone("(404) 555-0100")
        '4045550100'
        >>> normalize_phone("+1 404 555 0100")
        '4045550100'
        >>> normalize_phone("555-0100")
        ''
    """
    if pd.isna(value):
        return ""
    digits = re.sub(r"\D", "", str(value))
    return digits[-10:] if len(digits) >= 10 else ""


def _keys(df: pd.DataFrame, cols: MatchColumns) -> pd.DataFrame:
    """
    Build one row per record with the normalized email / phone / name.

    The output keeps the SAME index as `df`, so a match can point back to the
    original row. A column the side does not have is filled with "" (and ""
    never matches anything). When both first and last name are set they are
    joined with a space before normalizing; otherwise the single name column is used.

    Args:
        df: pd.DataFrame: one side of the match.
        cols: MatchColumns: which columns of `df` hold email, phone and name.

    Returns:
        pd.DataFrame: columns "email", "phone", "name" (all str), indexed like `df`.

    Example:
        >>> df = pd.DataFrame({"Company": ["Acme Bakery, LLC"], "Email": [" A@X.com"]})
        >>> _keys(df, MatchColumns(email="Email", name="Company")).iloc[0].tolist()
        ['a@x.com', '', 'acme bakery']
    """
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

    Matching runs in order of confidence and each record is matched at most once:
    1. exact normalized email (score 100, status "auto")
    2. exact normalized phone (score 100, status "auto")
    3. fuzzy name with rapidfuzz `token_sort_ratio`, comparing only names that
       start with the same letter ("blocking"); the best candidate scoring at
       least `review_threshold` is kept. Score >= `auto_threshold` -> "auto",
       otherwise "review" (a person should confirm it).
    If one right-hand email/phone appears on several rows, the first row is used.

    Args:
        left: pd.DataFrame: the first source (e.g. the client's own sales data).
        right: pd.DataFrame: the second source (e.g. QuickBooks customers).
        left_cols: MatchColumns | None: columns to use on the left; None = guess
            with `detect_match_columns`.
        right_cols: MatchColumns | None: columns to use on the right; None = guess.
        auto_threshold: int: fuzzy score (0–100) at or above which a name match is
            accepted automatically. Default 92.
        review_threshold: int: lowest fuzzy score (0–100) returned at all; scores
            between this and `auto_threshold` are labeled "review". Default 80.

    Returns:
        pd.DataFrame: one row per match, with columns
            left_index, right_index, method ("email" | "phone" | "name"),
            score (0–100), status ("auto" | "review"), left_value, right_value
        (the values are the NORMALIZED keys that matched). Each left row and each
        right row appears at most once. Empty (with these columns) if nothing matched.

    Example:
        >>> left = pd.DataFrame({"name": ["Acme Bakery, LLC", "Blue Cafe"], "email": ["a@x.com", None]})
        >>> right = pd.DataFrame({"company": ["ACME BAKERY", "Blue Cafe Inc"], "email": ["a@x.com", "b@y.com"]})
        >>> match_records(left, right)[["left_index", "right_index", "method", "score", "status"]]
           left_index  right_index method  score status
        0           0            0  email  100.0   auto
        1           1            1   name  100.0   auto
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
