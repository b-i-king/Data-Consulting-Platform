"""
xero_guard — keep data that came from Xero's API away from AI and machine learning.

WHY
---
Xero's developer terms (developer.xero.com/xero-developer-platform-terms-conditions,
clause 7(b)) say data from its API "cannot be used to train, fine tune, adapt,
or enhance any AI Models" (predictive analytics included), and may not be passed
to another company (e.g. an AI provider such as Google Gemini) without the
user's consent. Even though it's the client's own data, the platform plays it
safe: Xero data can be cleaned, explored and turned into dashboards, but the
Data Science & ML service (predictions, segments, reorder planning, multi-factor
analysis) and the AI service (Beacon questions and the AI skills) are off for it.

HOW (server-side, so hiding a button isn't the only protection)
---------------------------------------------------------------
The browser gets Xero data from the API as a CSV file, then sends that file
back with each request (that's how every tool works: nothing is stored).
So whenever the API hands out Xero data — live records (`GET
/api/connections/xero/dataset`) or a stored-data export that includes Xero
records (`GET /api/warehouse/export`) — `remember()` keeps a fingerprint of the
exact file (SHA-256 of its bytes). The AI endpoint and the ML / AI skills call
`refuse_if_xero()`, which compares each uploaded file's fingerprint (and its
"xero." file name, the name the website gives live Xero records) and answers
403 with XERO_BLOCKED.

Fingerprints are kept in this server's memory for FINGERPRINT_DAYS (a
fingerprint is 64 characters, so even thousands cost nothing). A server
restart forgets them; the "xero." name check still applies, and the website
hides the tools for Xero data too.
"""

from __future__ import annotations

import hashlib
import time

from fastapi import HTTPException, status

RESTRICTED_SERVICES = ("data_science_ml", "ai")   # service ids (= skill categories) off for Xero data
FINGERPRINT_DAYS = 7
XERO_BLOCKED = ("This data came from Xero. Xero's developer terms don't allow data from its API to be used with "
                "AI or machine-learning models, so AI questions and the Data Science & Machine Learning tools are "
                "off for it. Cleaning, exploring and dashboards work as usual.")

_served: dict[str, float] = {}   # sha256 of a file the API handed out -> when to forget it (epoch seconds)


def fingerprint(raw: bytes) -> str:
    """The SHA-256 of a file's bytes, as 64 hex characters."""
    return hashlib.sha256(raw).hexdigest()


def remember(raw: bytes) -> None:
    """
    Note a file of Xero data the API is about to send (live records or a stored export).

    Example:
        >>> remember(b"InvoiceID,Total\\n1,10\\n"); is_xero(b"InvoiceID,Total\\n1,10\\n", "invoices.csv")
        True
    """
    now = time.time()
    for key in [k for k, until in _served.items() if until < now]:   # forget the expired ones
        del _served[key]
    _served[fingerprint(raw)] = now + FINGERPRINT_DAYS * 86_400


def is_xero(raw_fingerprint_or_bytes: bytes | str, filename: str = "") -> bool:
    """True when the file is one the API sent as Xero data, or named like the live Xero records ("xero.Invoice.csv")."""
    fp = raw_fingerprint_or_bytes if isinstance(raw_fingerprint_or_bytes, str) else fingerprint(raw_fingerprint_or_bytes)
    until = _served.get(fp)
    return (until is not None and until >= time.time()) or filename.lower().startswith("xero.")


def refuse_if_xero(datasets) -> None:
    """
    Stop a request for AI or ML when any uploaded table came from Xero.

    Args:
        datasets: list[LoadedDataset] from pipeline.load_uploads (each has
            `sha256` of its upload and `source_file`).

    Raises:
        HTTPException: 403 with XERO_BLOCKED.
    """
    if any(is_xero(d.sha256 or "", d.source_file) for d in datasets):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=XERO_BLOCKED)


def restricted(service_id: str | None) -> bool:
    """True for the services that are off for Xero data (Data Science & ML, AI)."""
    return service_id in RESTRICTED_SERVICES
