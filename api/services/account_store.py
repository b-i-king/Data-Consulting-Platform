"""
Account storage: create accounts, teams, team members, and usage records.

WHAT THIS FILE DOES
-------------------
All reads/writes of account data go through the functions below, so the
rest of the app never writes SQL directly. Each function has two paths:

  * DATABASE path  — when `DATABASE_URL` is set, rows are written to
                     Supabase/PostgreSQL (tables defined in db/schema.sql).
  * MEMORY path    — when no database is configured (local development),
                     rows live in Python dictionaries and vanish on restart.

The memory path exists ONLY so you can run and test the app without
setting up Supabase first. Never rely on it in production.

WHAT IS STORED (and what is NOT)
--------------------------------
Stored: name, email, account type, team membership, the services on each
account's plan, the consultant (if any) assigned to each client, the
subscription choice, and a usage record per download (file name, row count,
runtime, amount) for billing.
NOT stored: the client's uploaded data. Files are processed in memory only.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from api.core import database

# ── In-memory fallback (development only) ────────────────────────────────────
_accounts: dict[str, dict] = {}         # account_id -> account
_teams: dict[str, dict] = {}            # team_id -> team
_usage: list[dict] = []                 # usage events
_services: dict[str, dict[str, dict]] = {}   # account_id -> service_id -> row
_consultants: dict[str, dict] = {}       # consultant_id -> consultant


class AccountExistsError(ValueError):
    """Raised when someone signs up with an email that is already registered."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def get_account_by_email(email: str) -> dict | None:
    email = email.lower()
    if database.pool is None:
        return next((a for a in _accounts.values() if a["email"] == email), None)
    row = await database.pool.fetchrow("SELECT * FROM accounts WHERE email = $1", email)
    return dict(row) if row else None


async def get_account(account_id: str) -> dict | None:
    if database.pool is None:
        return _accounts.get(account_id)
    row = await database.pool.fetchrow("SELECT * FROM accounts WHERE id = $1", uuid.UUID(account_id))
    return dict(row) if row else None


async def create_account(
    *,
    account_type: str,
    first_name: str,
    last_name: str,
    email: str,
    company_name: str | None = None,
    team_id: str | None = None,
    team_role: str | None = None,
    status: str = "active",
    industry_supersector: str | None = None,
    naics_sector: str | None = None,
    naics_subsector: str | None = None,
    company_size: str | None = None,
    location: dict | None = None,
    industry_other: str | None = None,
    wants_consultant: bool = False,
    consultant_id: str | None = None,
    subscription_plan: str | None = None,
    subscription_interval: str | None = None,
) -> dict:
    """
    Insert a new account. For a BUSINESS signup (no team_id given), a team
    is created automatically and the person becomes its owner.
    """
    email = email.lower()
    if await get_account_by_email(email):
        raise AccountExistsError("An account with this email already exists. Please sign in instead.")

    account_id = str(uuid.uuid4())

    # Business owners get a team; individuals do not (team_id stays NULL).
    if account_type == "business" and team_id is None:
        team_id = str(uuid.uuid4())
        team_role = "owner"
        team = {"id": team_id, "name": company_name or f"{first_name}'s team",
                "owner_account_id": account_id, "created_at": _now()}
    else:
        team = None

    account = {
        "id": account_id, "account_type": account_type, "first_name": first_name,
        "last_name": last_name, "email": email, "company_name": company_name,
        "team_id": team_id, "team_role": team_role, "status": status, "created_at": _now(),
        "last_login_at": _now() if status == "active" else None,
        "industry_supersector": industry_supersector, "naics_sector": naics_sector,
        "naics_subsector": naics_subsector, "company_size": company_size,
        # Optional location from the signup picker (api/services/locations.py).
        "location_geoname_id": location["id"] if location else None,
        "location_city": location["city"] if location else None,
        "location_region": (location["region"] or None) if location else None,
        "location_country_code": location["country_code"] if location else None,
        "location_label": location["label"] if location else None,
        "industry_other": industry_other,
        # Consultant: wants_consultant false -> consultant_id stays NULL.
        "wants_consultant": bool(wants_consultant),
        "consultant_id": consultant_id if wants_consultant else None,
        # Subscription choice (placeholder until payments).
        "subscription_plan": subscription_plan,
        "subscription_interval": subscription_interval if subscription_plan else None,
        "subscription_status": "active" if subscription_plan else None,
    }

    if database.pool is None:
        if team:
            _teams[team_id] = team
        _accounts[account_id] = account
        return account

    # Database path: create the account and (optionally) the team in ONE
    # transaction so we never end up with half-created records.
    async with database.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                """INSERT INTO accounts (id, account_type, first_name, last_name, email,
                                         company_name, team_id, team_role, status, last_login_at,
                                         industry_supersector, naics_sector, naics_subsector,
                                         company_size, location_geoname_id, location_city,
                                         location_region, location_country_code, location_label,
                                         industry_other, wants_consultant, consultant_id,
                                         subscription_plan, subscription_interval, subscription_status)
                   VALUES ($1, $2, $3, $4, $5, $6, NULL, $7, $8, $9, $10, $11, $12, $13,
                           $14, $15, $16, $17, $18, $19, $20, $21, $22, $23, $24)""",
                uuid.UUID(account_id), account_type, first_name, last_name, email,
                company_name, team_role, status, account["last_login_at"],
                industry_supersector, naics_sector, naics_subsector,
                company_size, account["location_geoname_id"], account["location_city"],
                account["location_region"], account["location_country_code"], account["location_label"],
                industry_other, account["wants_consultant"],
                uuid.UUID(account["consultant_id"]) if account["consultant_id"] else None,
                subscription_plan, account["subscription_interval"], account["subscription_status"],
            )
            if team:
                await conn.execute(
                    "INSERT INTO teams (id, name, owner_account_id) VALUES ($1, $2, $3)",
                    uuid.UUID(team_id), team["name"], uuid.UUID(account_id),
                )
            if team_id:
                await conn.execute(
                    "UPDATE accounts SET team_id = $1 WHERE id = $2",
                    uuid.UUID(team_id), uuid.UUID(account_id),
                )
    return account


async def touch_last_login(account_id: str) -> None:
    """Record a sign-in time (useful for your consulting analysis: who is active?)."""
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id]["last_login_at"] = _now()
        return
    await database.pool.execute(
        "UPDATE accounts SET last_login_at = now() WHERE id = $1", uuid.UUID(account_id)
    )


async def list_team_members(team_id: str) -> list[dict]:
    if database.pool is None:
        return [a for a in _accounts.values() if a["team_id"] == team_id]
    rows = await database.pool.fetch(
        "SELECT * FROM accounts WHERE team_id = $1 ORDER BY created_at", uuid.UUID(team_id)
    )
    return [dict(r) for r in rows]


async def get_team(team_id: str) -> dict | None:
    if database.pool is None:
        return _teams.get(team_id)
    row = await database.pool.fetchrow("SELECT * FROM teams WHERE id = $1", uuid.UUID(team_id))
    return dict(row) if row else None


async def record_usage(
    *, account_id: str, dataset_name: str, rows: int, runtime_seconds: float,
    billable_minutes: float, amount_usd: float, consultant_id: str | None = None,
) -> None:
    """Save one billing record. Only metadata — never the data itself.
    `consultant_id` is set when a consultant downloaded on the client's behalf."""
    event = {"account_id": account_id, "dataset_name": dataset_name, "rows": rows,
             "runtime_seconds": runtime_seconds, "billable_minutes": billable_minutes,
             "amount_usd": amount_usd, "consultant_id": consultant_id, "created_at": _now()}
    if database.pool is None:
        _usage.append(event)
        return
    await database.pool.execute(
        """INSERT INTO usage_events (account_id, dataset_name, row_count, runtime_seconds,
                                     billable_minutes, amount_usd, consultant_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7)""",
        uuid.UUID(account_id), dataset_name, rows, runtime_seconds, billable_minutes, amount_usd,
        uuid.UUID(consultant_id) if consultant_id else None,
    )


# ── Services on the plan (table account_services) ───────────────────────────

async def list_services(account_id: str) -> list[str]:
    """Active service ids stored for this account (no defaults added here)."""
    if database.pool is None:
        return [sid for sid, row in _services.get(account_id, {}).items() if row["status"] == "active"]
    rows = await database.pool.fetch(
        "SELECT service_id FROM account_services WHERE account_id = $1 AND status = 'active'",
        uuid.UUID(account_id),
    )
    return [r["service_id"] for r in rows]


async def add_services(account_id: str, service_ids: list[str], source: str) -> None:
    """Add (or re-activate) services on an account's plan."""
    if database.pool is None:
        plan = _services.setdefault(account_id, {})
        for sid in service_ids:
            plan[sid] = {"status": "active", "source": source, "added_at": _now()}
        return
    for sid in service_ids:
        await database.pool.execute(
            """INSERT INTO account_services (account_id, service_id, status, source)
               VALUES ($1, $2, 'active', $3)
               ON CONFLICT (account_id, service_id) DO UPDATE SET status = 'active'""",
            uuid.UUID(account_id), sid, source,
        )


# ── Consultants (table consultants) and their clients ───────────────────────

async def get_consultant_by_account(account_id: str) -> dict | None:
    """The consultant row for a signed-in account, or None if they aren't one."""
    if database.pool is None:
        return next((c for c in _consultants.values() if c["account_id"] == account_id), None)
    row = await database.pool.fetchrow("SELECT * FROM consultants WHERE account_id = $1", uuid.UUID(account_id))
    return dict(row) if row else None


async def get_consultant(consultant_id: str) -> dict | None:
    if database.pool is None:
        return _consultants.get(consultant_id)
    row = await database.pool.fetchrow("SELECT * FROM consultants WHERE id = $1", uuid.UUID(consultant_id))
    return dict(row) if row else None


async def get_consultant_by_email(email: str) -> dict | None:
    email = email.lower()
    if database.pool is None:
        return next((c for c in _consultants.values() if c["email"] == email), None)
    row = await database.pool.fetchrow("SELECT * FROM consultants WHERE email = $1", email)
    return dict(row) if row else None


async def ensure_consultant(account: dict, *, is_default: bool) -> dict:
    """Create (or refresh) the consultant row for a consultant's account."""
    existing = await get_consultant_by_account(str(account["id"]))
    name = account["first_name"]
    if database.pool is None:
        if existing:
            existing["is_default"] = is_default
            return existing
        cid = str(uuid.uuid4())
        _consultants[cid] = {"id": cid, "account_id": str(account["id"]), "display_name": name,
                             "email": account["email"], "is_default": is_default, "active": True,
                             "created_at": _now()}
        return _consultants[cid]
    row = await database.pool.fetchrow(
        """INSERT INTO consultants (account_id, display_name, email, is_default)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (account_id) DO UPDATE SET is_default = EXCLUDED.is_default
           RETURNING *""",
        uuid.UUID(str(account["id"])), name, account["email"], is_default,
    )
    return dict(row)


async def list_clients(consultant_id: str) -> list[dict]:
    """Accounts assigned to this consultant (plan holders: individuals and business owners)."""
    if database.pool is None:
        return sorted((a for a in _accounts.values() if a.get("consultant_id") == consultant_id),
                      key=lambda a: (a.get("company_name") or a["first_name"]).lower())
    rows = await database.pool.fetch(
        """SELECT * FROM accounts WHERE consultant_id = $1
           ORDER BY lower(coalesce(company_name, first_name))""",
        uuid.UUID(consultant_id),
    )
    return [dict(r) for r in rows]


async def set_subscription(account_id: str, *, plan: str | None, interval: str | None, status: str | None) -> None:
    """Record the subscription choice, or cancel it (status 'canceled')."""
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc:
            acc.update(subscription_plan=plan, subscription_interval=interval, subscription_status=status)
        return
    await database.pool.execute(
        """UPDATE accounts SET subscription_plan = $2, subscription_interval = $3, subscription_status = $4
           WHERE id = $1""",
        uuid.UUID(account_id), plan, interval, status,
    )


async def assign_waiting_clients(consultant_id: str) -> int:
    """Give clients who asked for a consultant (but have none yet) to this consultant."""
    if database.pool is None:
        waiting = [a for a in _accounts.values() if a.get("wants_consultant") and not a.get("consultant_id")]
        for a in waiting:
            a["consultant_id"] = consultant_id
        return len(waiting)
    result = await database.pool.execute(
        "UPDATE accounts SET consultant_id = $1 WHERE wants_consultant AND consultant_id IS NULL",
        uuid.UUID(consultant_id),
    )
    return int(result.split()[-1]) if result else 0
