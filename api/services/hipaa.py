"""
HIPAA: health information is stored only for a healthcare business an admin has CONFIRMED.

WHY
---
Anyone can pick "Health Care and Social Assistance" (NAICS 62) at sign-up. A
business that will store protected health information (patients' records,
diagnoses, visits, measurements...) must be a real healthcare business, and
that needs a person to check — so it's a request and an admin decision, like
consultant approval.

THE STEPS
---------
  1. REQUEST  automatic for a NAICS 62 sign-up, or by any business owner in
              Settings -> Account -> "Health information (HIPAA)": the legal
              name of the practice, its NPI (National Provider Identifier,
              10 digits — checked here with the official check digit), and
              what health data they'd store. hipaa_status = 'pending'.
  2. PAUSED   while pending, storing data (loads, syncs) is refused with a
              clear message: the data doesn't go into the database before
              someone has looked. Cleaning and analyzing files (kept in
              memory only) still work.
  3. DECIDE   an admin (Admin -> HIPAA requests) checks the NPI in the public
              NPPES registry (link shown) and approves or denies, with a note.
              * approved: is_healthcare = true -> every access is written to
                the audit log (api/services/audit.py). Sign a Business Associate
                Agreement with them before they upload patient data.
              * denied: storing works again for ordinary business data; the
                Terms say no patient health information without approval.
  A pending request can be withdrawn; a denied one can be asked again.

  npi_valid(npi)                    the 10-digit NPI check (Luhn, with prefix 80840)
  request(account, org, npi, note)  ask (plan holders of business accounts)
  withdraw(account)                 take back a pending request
  decide(account_id, ok, admin, note)
  require_storage_allowed(holder)   403 while a request is pending
  pending_requests()                the admin's list
"""

from __future__ import annotations

import re

from fastapi import HTTPException, status

from api.services import account_store

NPPES_LOOKUP = "https://npiregistry.cms.hhs.gov/provider-view/{npi}"   # the public NPI registry (CMS)
PAUSED = ("Storing data is paused while we confirm your healthcare business for HIPAA (Settings -> Account -> "
          "Health information). You can still clean and analyze files; nothing is stored until it's approved.")
STATUS_LABELS = {None: "Not requested", "pending": "Waiting for review", "approved": "Approved", "denied": "Not approved"}


def npi_valid(npi: str) -> bool:
    """
    Check an NPI's format: 10 digits whose last digit is the Luhn check digit of "80840" + the first 9.

    (The official NPI check-digit rule from CMS. A valid format doesn't prove the
    business is real — the admin still looks it up in the NPPES registry.)

    Args:
        npi: str, e.g. "1234567893".

    Returns:
        bool: True when the format and check digit are right.

    Example:
        >>> npi_valid("1234567893"), npi_valid("1234567890")
        (True, False)
    """
    digits = re.sub(r"\D", "", npi or "")
    if len(digits) != 10:
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + digits[:9])):
        d = int(ch)
        if i % 2 == 0:          # double every second digit from the right (the check digit's neighbor first)
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return (10 - total % 10) % 10 == int(digits[9])


def _plan_holder_only(account: dict) -> None:
    """Only the account that holds the plan (an individual or a business owner) asks."""
    if account.get("team_role") in ("developer", "member"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only your business's owner can ask for HIPAA.")


async def request(account: dict, organization: str, npi: str | None, note: str | None) -> dict:
    """
    Ask to store health information (HIPAA); an admin reviews it. While it's still
    waiting, asking again UPDATES the details (e.g. adding the NPI to a sign-up request).

    Args:
        account: dict, the plan holder's accounts row.
        organization: str, the practice's / organization's legal name.
        npi: str | None, its 10-digit NPI (strongly recommended; checked when given).
        note: str | None, what health data they'd store.

    Returns:
        dict: the updated accounts row (hipaa_status 'pending').

    Raises:
        HTTPException: 403 for a team colleague; 400 for a bad NPI or no organization;
            409 when it's already approved.
    """
    _plan_holder_only(account)
    if account.get("hipaa_status") == "approved":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Your HIPAA request is already approved.")
    organization = (organization or "").strip()
    if not 2 <= len(organization) <= 200:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Type your practice's or organization's legal name.")
    digits = re.sub(r"\D", "", npi or "") or None
    if digits and not npi_valid(digits):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="That NPI doesn't look right: it's 10 digits, from the NPPES registry.")
    return await account_store.set_hipaa(str(account["id"]), status="pending", organization=organization, npi=digits,
                                         note=(note or "").strip()[:1000] or None)


async def request_at_signup(account: dict) -> dict:
    """A NAICS 62 (health care) business: open a pending request at sign-up; they add details in Settings."""
    if account.get("naics_sector") != "62" or account.get("team_role") in ("developer", "member"):
        return account
    return await account_store.set_hipaa(str(account["id"]), status="pending", organization=account.get("company_name"),
                                         npi=None, note=None) or account


async def withdraw(account: dict) -> dict:
    """Take back a PENDING request (storing works again, as an ordinary business)."""
    _plan_holder_only(account)
    if account.get("hipaa_status") != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="There's no request waiting to withdraw.")
    return await account_store.clear_hipaa(str(account["id"]))


async def decide(account_id: str, approve: bool, admin: dict, note: str | None) -> dict:
    """
    An admin approves (audit log on) or denies a HIPAA request.

    Raises:
        HTTPException: 404 unknown account; 400 when nothing is pending.
    """
    acc = await account_store.get_account(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That account wasn't found.")
    if acc.get("hipaa_status") != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="That account has no request waiting.")
    return await account_store.decide_hipaa(account_id, approve, str(admin["id"]), (note or "").strip()[:500] or None)


def require_storage_allowed(holder: dict) -> None:
    """
    Refuse to store data while a HIPAA request is waiting for an admin.

    Raises:
        HTTPException: 403 PAUSED.
    """
    if holder.get("hipaa_status") == "pending":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=PAUSED)


def status_out(acc: dict) -> dict:
    """What the client (and the admin list) sees about a HIPAA request."""
    def iso(d):
        return d.isoformat() if d else None

    npi = acc.get("hipaa_npi")
    return {"status": acc.get("hipaa_status"), "label": STATUS_LABELS.get(acc.get("hipaa_status"), "Not requested"),
            "organization": acc.get("hipaa_organization"), "npi": npi, "note": acc.get("hipaa_note"),
            "requested_at": iso(acc.get("hipaa_requested_at")), "decided_at": iso(acc.get("hipaa_decided_at")),
            "decision_note": acc.get("hipaa_decision_note"),
            "lookup_url": NPPES_LOOKUP.format(npi=npi) if npi else None,
            "storing_paused": acc.get("hipaa_status") == "pending"}


async def pending_requests() -> list[dict]:
    """The admin's list: waiting requests first (oldest first), then decided ones."""
    out = []
    for acc in await account_store.list_hipaa_requests():
        out.append({**status_out(acc), "account_id": str(acc["id"]),
                    "name": f"{acc['first_name']} {acc['last_name']}", "email": acc["email"],
                    "company_name": acc.get("company_name"), "naics_sector": acc.get("naics_sector")})
    return out
