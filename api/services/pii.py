"""
Emails and phone numbers in STORED client data are encrypted; names are not.

WHAT THIS FILE DOES
-------------------
Data Engineering stores a client's rows (fact.account_data) and their
customers, vendors and patients once (fact.entities). Two kinds of value in
them can reach a real person directly and are rarely needed for analysis:
EMAIL ADDRESSES and PHONE NUMBERS. Those are encrypted before they're written
(api/core/crypto.py, purpose "pii") and decrypted only when the client's own
data is read back for them ("Use my stored data", Download as CSV).

NAMES STAY READABLE on purpose: reports need them ("top 10 customers by
revenue"), and matching the same customer across sources uses them. That's
the middle ground: someone who got hold of the database copy would see names
and amounts, but no way to email or call anyone.

  is_contact_field(name)  "customer_email", "patient_phone", "mobile", "fax" ... -> True
  protect(attrs)          encrypt those fields' values in a dict (before writing)
  reveal(attrs)           decrypt them again (after reading)

Matching across sources (entities.py) compares the DECRYPTED values in
memory, so "same email" still links two records. Values already encrypted are
never encrypted twice, and plain values stored before this change still read.
"""

from __future__ import annotations

import re

from api.core import crypto

_CONTACT_WORDS = {"email", "e_mail", "mail", "phone", "telephone", "tel", "mobile", "cell", "cellphone", "fax"}
PURPOSE = "pii"


def is_contact_field(name: str) -> bool:
    """
    Tell whether a field holds an email or phone number, from its name.

    A field counts when one of its words (split on "_", spaces and dashes) is
    email / e_mail / mail / phone / tel / mobile / cell / fax — so
    "customer_email" and "Patient Phone" count, "hotel_name" doesn't.

    Args:
        name: str, a column or field name.

    Returns:
        bool: True for contact fields.

    Example:
        >>> is_contact_field("customer_email"), is_contact_field("hotel_name")
        (True, False)
    """
    norm = re.sub(r"[^a-z0-9]+", "_", str(name).lower()).strip("_")
    if "e_mail" in norm or norm.endswith("email"):
        return True
    return any(word in _CONTACT_WORDS for word in norm.split("_"))


def _blank(value) -> bool:
    return value is None or (isinstance(value, float) and value != value) or str(value).strip() == ""


def protect(attrs: dict | None) -> dict:
    """
    Encrypt the email / phone values in a dict (a new dict; the input isn't changed).

    Args:
        attrs: dict | None, e.g. a stored row's attributes or an entity's details.

    Returns:
        dict: the same keys; contact values are "enc1:..." text.

    Example:
        >>> protect({"customer_email": "bd@x.com", "store": "Midtown"})["store"]
        'Midtown'
    """
    out = dict(attrs or {})
    for key, value in out.items():
        if is_contact_field(key) and not _blank(value) and not crypto.is_encrypted(value):
            out[key] = crypto.encrypt(str(value), PURPOSE)
    return out


def reveal(attrs: dict | None) -> dict:
    """
    Decrypt the email / phone values in a dict (a new dict).

    Args:
        attrs: dict | None, read from storage.

    Returns:
        dict: the same keys with readable values.
    """
    return {k: crypto.decrypt(v, PURPOSE) for k, v in (attrs or {}).items()}
