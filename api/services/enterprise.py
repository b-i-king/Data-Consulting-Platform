"""
enterprise — enterprise clients: a custom contract instead of self-serve plans.

Large companies don't buy through Stripe checkout: they agree a price and
terms with the owner (on a call), get raised limits, accept an Enterprise
agreement IN THE APP, and are invoiced outside the platform (e.g. from Wave).

Where it lives: COLUMNS on dim.accounts (the plan holder's row) — one client =
one row, nothing new per client (db/schema.sql, "Enterprise"):

    is_enterprise                 the tag (an admin turns it on)
    enterprise_price_usd          the agreed price ...
    enterprise_billing            ... "monthly", "yearly" or "one_time"
    enterprise_setup_fee_usd      a one-time implementation fee (optional)
    enterprise_contract_start/end the contract dates (end empty = open-ended)
    enterprise_services           the services the contract includes (catalog ids)
    enterprise_limits             raised limits, JSON: {"max_upload_mb", "team_seats", "table_rows"}
    enterprise_terms_version /    the Enterprise agreement the client's signer accepted
      _accepted_at / _accepted_by   (which version, when, typed name)
    enterprise_notes              the admin's private notes (Wave invoice numbers, contacts)

While the contract is ACTIVE (tag on, today within the dates):
  * its services are unlocked (entitlements.active_services) — no Stripe needed;
  * it counts as a paid plan (business-tool connections, free downloads);
  * its limits replace the defaults (`limit`): upload size, team seats, rows
    read from a database table. Each has a hard ceiling (LIMITS) so a typo
    can't ask the server for more than it can hold — raising uploads beyond
    ~100 MB also needs a bigger Render instance (more memory).
The agreement: the account's OWNER types their name and ticks "I agree"; the
version, time and name are recorded (a "click to accept" agreement). Text:
web/app/enterprise-agreement/page.tsx (version AGREEMENT_VERSION) — have it
reviewed by a lawyer before the first enterprise client.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store
from api.services.pricing import SERVICES_BY_ID

AGREEMENT_VERSION = "2026-10-23"
BILLING = ("monthly", "yearly", "one_time")
# Each raisable limit: (where the default comes from, the hard ceiling).
LIMITS = {
    "max_upload_mb": ("MAX_UPLOAD_MB", 200),     # file size per upload
    "team_seats": ("TEAM_MAX_PEOPLE", 500),      # people on the team, owner included
    "table_rows": ("cloud_sources.MAX_ROWS", 1_000_000),   # rows read from one database table
}
FIELDS = ("is_enterprise", "enterprise_price_usd", "enterprise_billing", "enterprise_setup_fee_usd",
          "enterprise_contract_start", "enterprise_contract_end", "enterprise_services", "enterprise_limits",
          "enterprise_notes")


def defaults() -> dict[str, int]:
    """The normal limits everyone has (from the settings)."""
    from api.services import cloud_sources

    s = get_settings()
    return {"max_upload_mb": s.max_upload_mb, "team_seats": max(1, s.team_max_people),
            "table_rows": cloud_sources.MAX_ROWS}


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _as_date(v) -> date | None:
    if v in (None, ""):
        return None
    return v if isinstance(v, date) else date.fromisoformat(str(v)[:10])


def is_active(holder: dict | None) -> bool:
    """The tag is on and today is within the contract dates (an empty end = open-ended)."""
    if not holder or not holder.get("is_enterprise"):
        return False
    start, end = _as_date(holder.get("enterprise_contract_start")), _as_date(holder.get("enterprise_contract_end"))
    today = _today()
    return (start is None or start <= today) and (end is None or today <= end)


def services(holder: dict | None) -> list[str]:
    """The services an active contract unlocks (none when it isn't active)."""
    return [s for s in (holder or {}).get("enterprise_services") or [] if s in SERVICES_BY_ID] if is_active(holder) else []


def limits(holder: dict | None) -> dict[str, int]:
    """The limits that apply: the contract's raised ones while it's active, else the defaults."""
    out = defaults()
    if is_active(holder):
        for key, value in _limits_json(holder).items():
            if key in LIMITS and value:
                out[key] = max(out[key], min(int(value), LIMITS[key][1]))   # never below the default
    return out


async def limit(account: dict | None, key: str) -> int:
    """One limit for this account's plan holder (a team member uses the owner's contract)."""
    from api.services.entitlements import plan_holder   # here: entitlements imports billing, which imports a lot

    holder = await plan_holder(account) if account else None
    return limits(holder)[key]


def summary(holder: dict, admin: bool = False) -> dict:
    """The contract as the client (or, with admin=True, the admin) sees it."""
    out = {"is_enterprise": bool(holder.get("is_enterprise")), "active": is_active(holder),
           "price_usd": _num(holder.get("enterprise_price_usd")), "billing": holder.get("enterprise_billing"),
           "setup_fee_usd": _num(holder.get("enterprise_setup_fee_usd")),
           "contract_start": _iso(holder.get("enterprise_contract_start")),
           "contract_end": _iso(holder.get("enterprise_contract_end")),
           "services": list(holder.get("enterprise_services") or []), "limits": limits(holder),
           "defaults": defaults(),
           "agreement": {"version": AGREEMENT_VERSION,
                         "accepted_version": holder.get("enterprise_terms_version"),
                         "accepted_at": _iso(holder.get("enterprise_terms_accepted_at")),
                         "accepted_by": holder.get("enterprise_terms_accepted_by"),
                         "needs_acceptance": bool(holder.get("is_enterprise"))
                         and holder.get("enterprise_terms_version") != AGREEMENT_VERSION}}
    if admin:
        out["raw_limits"] = _limits_json(holder)
        out["notes"] = holder.get("enterprise_notes")
        out["ceilings"] = {k: v[1] for k, v in LIMITS.items()}
    return out


def _limits_json(holder: dict | None) -> dict:
    """enterprise_limits as a dict (the database hands JSONB back as text)."""
    raw = (holder or {}).get("enterprise_limits") or {}
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def _num(v) -> float | None:
    return None if v is None else float(v)


def _iso(v) -> str | None:
    return v.isoformat() if hasattr(v, "isoformat") else v


def _bad(detail: str) -> HTTPException:
    return HTTPException(status.HTTP_400_BAD_REQUEST, detail=detail)


async def admin_update(account_id: str, body: dict) -> dict:
    """
    An admin sets (or changes) a client's enterprise contract.

    Args:
        account_id: str, the client's account (must be a plan holder, not a team member).
        body: dict, any of FIELDS (left out = unchanged).

    Raises:
        HTTPException: 404 unknown account; 400 a team member, or a bad value.
    """
    from api.services.entitlements import plan_holder_id

    acc = await account_store.get_account(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That account wasn't found.")
    if await plan_holder_id(acc) != account_id:
        raise _bad("That's a team member — set the contract on the team's owner.")
    fields: dict = {}
    if "is_enterprise" in body:
        fields["is_enterprise"] = bool(body["is_enterprise"])
    for key in ("enterprise_price_usd", "enterprise_setup_fee_usd"):
        if key in body:
            v = body[key]
            if v not in (None, ""):
                try:
                    v = round(float(v), 2)
                except (TypeError, ValueError):
                    raise _bad("Prices must be numbers.")
                if v < 0:
                    raise _bad("Prices can't be negative.")
            fields[key] = None if v in (None, "") else v
    if "enterprise_billing" in body:
        if body["enterprise_billing"] not in (*BILLING, None, ""):
            raise _bad(f"Billing must be one of: {', '.join(BILLING)}.")
        fields["enterprise_billing"] = body["enterprise_billing"] or None
    for key in ("enterprise_contract_start", "enterprise_contract_end"):
        if key in body:
            try:
                fields[key] = _as_date(body[key])
            except ValueError:
                raise _bad("Dates must look like 2026-11-13.")
    start = fields.get("enterprise_contract_start", _as_date(acc.get("enterprise_contract_start")))
    end = fields.get("enterprise_contract_end", _as_date(acc.get("enterprise_contract_end")))
    if start and end and end < start:
        raise _bad("The contract ends before it starts.")
    if "enterprise_services" in body:
        chosen = [s for s in (body["enterprise_services"] or [])]
        unknown = [s for s in chosen if s not in SERVICES_BY_ID]
        if unknown:
            raise _bad(f"Unknown service(s): {', '.join(unknown)}.")
        fields["enterprise_services"] = [s for s in SERVICES_BY_ID if s in chosen]   # catalog order
    if "enterprise_limits" in body:
        clean = {}
        for key, value in (body["enterprise_limits"] or {}).items():
            if key not in LIMITS:
                raise _bad(f"Unknown limit: {key}.")
            if value in (None, ""):
                continue
            try:
                n = int(value)
            except (TypeError, ValueError):
                raise _bad("Limits must be whole numbers.")
            if not 1 <= n <= LIMITS[key][1]:
                raise _bad(f"{key} must be between 1 and {LIMITS[key][1]:,}.")
            clean[key] = n
        fields["enterprise_limits"] = clean
    if "enterprise_notes" in body:
        fields["enterprise_notes"] = (str(body["enterprise_notes"] or "").strip() or None)
    row = await account_store.update_enterprise(account_id, fields)
    return summary(row, admin=True)


async def accept(account: dict, signer_name: str, agreed: bool) -> dict:
    """
    The client's OWNER accepts the current Enterprise agreement (name typed + "I agree").

    Raises:
        HTTPException: 403 not the account's owner; 400 not an enterprise client, not agreed, or no name.
    """
    from api.services.entitlements import plan_holder_id

    holder_id = await plan_holder_id(account)
    if str(account["id"]) != holder_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the account's owner can accept the agreement.")
    holder = await account_store.get_account(holder_id)
    if not holder or not holder.get("is_enterprise"):
        raise _bad("This account doesn't have an enterprise contract.")
    name = " ".join(str(signer_name or "").split())[:120]
    if not agreed or len(name) < 3:
        raise _bad("Type your full name and tick \"I agree\" to accept the agreement.")
    row = await account_store.accept_enterprise_terms(holder_id, AGREEMENT_VERSION, name)
    return summary(row)
