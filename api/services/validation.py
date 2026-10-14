"""
Rules for what people type into the signup form — the "foolproof" layer.

WHAT THIS FILE DOES
-------------------
Each function takes raw text, tidies it, and either returns the clean value or
raises ValueError with a message that is safe to show the person. The same
rules are mirrored in the browser (web/lib/validation.ts) so mistakes are
caught while typing, but THESE are the ones that count: anything reaching the
API is checked here (api/models/accounts.py calls them).

    clean_person_name("  josé   maría ")  -> raises: must start with a capital letter
    clean_person_name("José María")       -> "José María"
    clean_person_name("O'Brien-Smith")    -> "O'Brien-Smith"
    clean_company_name("Ben & Jerry's, LLC")  -> "Ben & Jerry's, LLC"
    clean_email("  Ana@Example.COM ")     -> "ana@example.com"

PERSON NAMES
------------
Letters from ANY alphabet (é, ñ, ü, ø, Chinese, Arabic…), joined by single
spaces, hyphens, apostrophes (' or ’) or periods (initials, "Jr."). No digits
or other symbols. The first character must be a capital letter. (The browser
capitalizes the first letter for you as you type.)

BUSINESS NAMES
--------------
2–200 characters: letters (any alphabet), digits, spaces, and the punctuation
real company names use: & ' ’ . , - ( ) / + ! # @ : ;  It must start with a
letter or digit. Emoji, angle brackets and other symbols are refused.

EMAIL ADDRESSES
---------------
Checked with the `email-validator` library (the same one Pydantic's EmailStr
uses): one @, a valid domain with a dot (example.com), no spaces. Stored in
lowercase, so Ana@Example.com and ana@example.com are the SAME address — and
each address can belong to only one account.
"""

from __future__ import annotations

import re
import unicodedata

from email_validator import EmailNotValidError, validate_email

NAME_SEPARATORS = " -'’."
COMPANY_PUNCTUATION = set("&'’.,-()/+!#@:; ")


def _tidy(value: str) -> str:
    """Unicode-normalize (é typed two ways becomes one) and collapse spaces."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value or "")).strip()


def _is_letter(ch: str) -> bool:
    # Letters (L*) and accent marks that combine with them (M*), in any alphabet.
    return unicodedata.category(ch)[0] in ("L", "M")


def clean_person_name(value: str, field: str = "Name") -> str:
    """Validate a first or last name; returns the tidied name."""
    name = _tidy(value)
    if not name:
        raise ValueError(f"{field} is required.")
    if len(name) > 100:
        raise ValueError(f"{field} must be 100 characters or fewer.")
    bad = sorted({ch for ch in name if not (_is_letter(ch) or ch in NAME_SEPARATORS)})
    if bad:
        raise ValueError(f"{field} can only use letters (accents are fine), spaces, hyphens, "
                         f"apostrophes and periods — remove: {' '.join(bad)}")
    first = name[0]
    if not first.isalpha():
        raise ValueError(f"{field} must start with a letter.")
    # Alphabets without capitals (e.g. Chinese) have upper == lower; skip the check for them.
    if first.upper() != first.lower() and first != first.upper():
        raise ValueError(f"{field} must start with a capital letter (for example, Ana — not ana).")
    if re.search(r"[ \-'’.]{2,}", name.replace(". ", ".")):
        raise ValueError(f"{field} has two separators in a row (like -- or '').")
    if name[-1] in " -'’":
        raise ValueError(f"{field} can't end with a hyphen or apostrophe.")
    return name


def clean_company_name(value: str) -> str:
    """Validate a business name; returns the tidied name."""
    name = _tidy(value)
    if len(name) < 2:
        raise ValueError("Business name must be at least 2 characters.")
    if len(name) > 200:
        raise ValueError("Business name must be 200 characters or fewer.")
    bad = sorted({ch for ch in name if not (_is_letter(ch) or ch.isdigit() or ch in COMPANY_PUNCTUATION)})
    if bad:
        raise ValueError("Business name can use letters, numbers, spaces and & ' . , - ( ) / + ! # @ : ; "
                         f"— remove: {' '.join(bad)}")
    if not (name[0].isalnum()):
        raise ValueError("Business name must start with a letter or a number.")
    return name


def clean_email(value: str) -> str:
    """Validate an email address's format; returns it in lowercase."""
    email = (value or "").strip()
    if not email:
        raise ValueError("Email address is required.")
    try:
        # check_deliverability=False: format only (no DNS lookup during signup).
        result = validate_email(email, check_deliverability=False)
    except EmailNotValidError as exc:
        raise ValueError(f"Enter a valid email address, like name@example.com ({exc}).") from None
    return result.normalized.lower()
