"""
Account storage: create accounts, teams, team members, and usage records.

WHAT THIS FILE DOES
-------------------
All reads/writes of account data go through the functions below, so the
rest of the app never writes SQL directly. Each function has two paths:

  * DATABASE path  — when `DATABASE_URL` is set, rows are written to
                     Supabase/PostgreSQL: the `dim` schema of db/schema.sql
                     (every table name below is written as dim.<table>).
  * MEMORY path    — when no database is configured (local development),
                     rows live in Python dictionaries and vanish on restart.

The memory path exists ONLY so you can run and test the app without
setting up Supabase first. Never rely on it in production.

WHAT IS STORED (and what is NOT)
--------------------------------
Stored: name, email, account type, team membership, project interests, the
consultant (if any) assigned to each client, subscriptions (kept in sync by
Stripe), a usage record per download (file name, row count, runtime, amount,
and a one-way fingerprint of the result for fair use) for billing, each
connector's refresh schedule, consultations (free and paid calls), storage
consents and load-run summaries.
After "Delete my account": nothing about the person, only an ANONYMOUS row in
dim.account_deletions (type, role, age in days, paid or not, optional reason).
Passwords: only a Supabase user id, or a one-way hash in local mode.
NOT stored here: the client's uploaded data. Files are processed in memory
only. (Clients who consent to managed storage have cleaned rows loaded into
fact.account_data — api/services/warehouse_store.py.)
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timedelta, timezone

from api.core import database

# ── In-memory fallback (development only) ────────────────────────────────────
_accounts: dict[str, dict] = {}         # account_id -> account
_teams: dict[str, dict] = {}            # team_id -> team
_usage: list[dict] = []                 # usage events
_consultants: dict[str, dict] = {}       # consultant_id -> consultant
_subscriptions: dict[str, dict] = {}     # subscription id -> subscription (with "items")
_consultations: dict[str, dict] = {}     # consultation id -> consultations row
_consents: list[dict] = []               # storage_consents rows
_load_runs: list[dict] = []              # load_runs rows

_connections: dict[tuple[str, str], dict] = {}   # (account_id, connector) -> data_connections row


DEFAULT_CAPACITY = 3   # clients a new consultant takes at once (dim.consultants.capacity)


class AccountExistsError(ValueError):
    """Raised when someone signs up with an email that is already registered."""


def _now() -> datetime:
    """
    Return the current time in UTC (timezone-aware), used for created/updated stamps.

    Returns:
        datetime: the current UTC time.

    Example:
        >>> _now().tzinfo
        datetime.timezone.utc
    """
    return datetime.now(timezone.utc)


async def get_account_by_email(email: str) -> dict | None:
    """
    Look up an account by email address (letter case is ignored).

    Emails are stored lower-cased, so the address is lower-cased before searching.

    Args:
        email: str, the address to look up.

    Returns:
        dict | None: the accounts row, or None if no account uses that email.

    Example:
        >>> (await get_account_by_email("Ana@Bakery.com"))["email"]
        'ana@bakery.com'
    """
    email = email.lower()
    if database.pool is None:
        return next((a for a in _accounts.values() if a["email"] == email), None)
    row = await database.pool.fetchrow("SELECT * FROM dim.accounts WHERE email = $1", email)
    return dict(row) if row else None


async def get_account(account_id: str) -> dict | None:
    """
    Look up an account by its id.

    Args:
        account_id: str, the account's UUID as text.

    Returns:
        dict | None: the accounts row, or None if it does not exist.

    Raises:
        ValueError: on the database path, if `account_id` is not a valid UUID.

    Example:
        >>> (await get_account(account_id))["first_name"]
        'Ana'
    """
    if database.pool is None:
        return _accounts.get(account_id)
    row = await database.pool.fetchrow("SELECT * FROM dim.accounts WHERE id = $1", uuid.UUID(account_id))
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
    auth_user_id: str | None = None,
    password_hash: str | None = None,
    consultant_plan: str | None = None,
    terms_version: str | None = None,
    consultant_request: bool = False,
    is_admin: bool = False,
) -> dict:
    """
    Insert a new account. For a BUSINESS signup (no team_id given), a team
    is created automatically and the person becomes its owner.

    On the database path the account, the team and the account's team link are
    written in ONE transaction, so a failure never leaves half-created records.
    `last_login_at` is set only for "active" accounts. `consultant_id` is kept
    only when `wants_consultant` is True.

    Args:
        account_type: str, "individual" or "business".
        first_name: str, the person's first name.
        last_name: str, the person's last name.
        email: str, sign-in address (stored lower-cased; must be unique).
        company_name: str | None, business name (also used as the team name).
        team_id: str | None, join this existing team instead of creating one.
        team_role: str | None, role on that team (set to "owner" for a new team).
        status: str, account status, default "active".
        industry_supersector: str | None, industry grouping from the signup form.
        naics_sector: str | None, NAICS sector code.
        naics_subsector: str | None, NAICS subsector code.
        company_size: str | None, size band from the signup form.
        location: dict | None, a place from the signup picker
            (api/services/locations.py) with "id", "city", "region",
            "country_code" and "label".
        industry_other: str | None, free-text industry when none fits.
        wants_consultant: bool, True if the client asked for "done for you" help.
        consultant_id: str | None, the consultant assigned (kept only when
            `wants_consultant` is True).
        auth_user_id: str | None, the Supabase Auth user id (Supabase mode).
        password_hash: str | None, the scrypt hash (local mode). Both stay None
            for accounts made FOR someone; see api/services/auth.py.
        consultant_plan: str | None, "one_time" or "monthly" consultant help
            (kept only when `wants_consultant` is True).
        terms_version: str | None, the Terms version ticked at sign-up; also
            stamps terms_accepted_at. None for accounts made FOR someone.
        consultant_request: bool, True = "I'd like to work as a consultant"
            (consultant_request 'pending', waiting for an admin).
        is_admin: bool, True for an ADMIN_EMAILS address (also a consultant).

    Returns:
        dict: the new account (same fields as the accounts table).

    Raises:
        AccountExistsError: if an account with this email already exists.

    Example:
        >>> acc = await create_account(account_type="business", first_name="Ana",
        ...                            last_name="Diaz", email="Ana@Bakery.com",
        ...                            company_name="Sunrise Bakery")
        >>> acc["email"], acc["team_role"]
        ('ana@bakery.com', 'owner')
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
        "interested_services": [],      # "Projects with a consultant" (interest only)
        "stripe_customer_id": None,     # set the first time they pay (billing.py)
        "is_tester": False,             # an admin can make them a tester (try paid tools free)
        "failed_logins": 0,             # wrong passwords in a row (login_guard.py)
        "login_locked_until": None,
        "mfa_secret": None, "mfa_enabled_at": None, "mfa_recovery_codes": [],   # two-step sign-in (mfa.py)
        # Audit log on only once an admin APPROVES a HIPAA request (hipaa.py) — not just for picking NAICS 62.
        "is_healthcare": False,
        "hipaa_status": None, "hipaa_requested_at": None, "hipaa_organization": None, "hipaa_npi": None,
        "hipaa_note": None, "hipaa_decided_at": None, "hipaa_decided_by": None, "hipaa_decision_note": None,
        "updated_at": _now(),
        # Password (api/services/auth.py): Supabase user id OR local hash.
        "auth_user_id": auth_user_id,
        "password_hash": password_hash,
        "consultant_plan": consultant_plan if wants_consultant else None,
        # Roles: admins come from ADMIN_EMAILS; consultants only by approval.
        "is_admin": bool(is_admin),
        "is_consultant": bool(is_admin),
        "consultant_request": "pending" if consultant_request and not is_admin else None,
        "consultant_requested_at": _now() if consultant_request and not is_admin else None,
        "consultant_decided_at": None,
        "consultant_decided_by": None,
        "terms_accepted_at": _now() if terms_version else None,
        "terms_version": terms_version,
        "password_reset_hash": None, "password_reset_expires_at": None,
        "business_context": {},
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
                """INSERT INTO dim.accounts (id, account_type, first_name, last_name, email,
                                         company_name, team_id, team_role, status, last_login_at,
                                         industry_supersector, naics_sector, naics_subsector,
                                         company_size, location_geoname_id, location_city,
                                         location_region, location_country_code, location_label,
                                         industry_other, wants_consultant, consultant_id,
                                         auth_user_id, password_hash, consultant_plan,
                                         is_admin, is_consultant, consultant_request,
                                         consultant_requested_at, terms_accepted_at, terms_version,
                                         is_healthcare)
                   VALUES ($1, $2, $3, $4, $5, $6, NULL, $7, $8, $9, $10, $11, $12, $13,
                           $14, $15, $16, $17, $18, $19, $20, $21, $22, $23,
                           $24, $25, $26, $27, $28, $29, $30, $31)""",
                uuid.UUID(account_id), account_type, first_name, last_name, email,
                company_name, team_role, status, account["last_login_at"],
                industry_supersector, naics_sector, naics_subsector,
                company_size, account["location_geoname_id"], account["location_city"],
                account["location_region"], account["location_country_code"], account["location_label"],
                industry_other, account["wants_consultant"],
                uuid.UUID(account["consultant_id"]) if account["consultant_id"] else None,
                uuid.UUID(auth_user_id) if auth_user_id else None, password_hash,
                account["consultant_plan"], account["is_admin"], account["is_consultant"],
                account["consultant_request"], account["consultant_requested_at"],
                account["terms_accepted_at"], terms_version, account["is_healthcare"],
            )
            if team:
                await conn.execute(
                    "INSERT INTO dim.teams (id, name, owner_account_id) VALUES ($1, $2, $3)",
                    uuid.UUID(team_id), team["name"], uuid.UUID(account_id),
                )
            if team_id:
                await conn.execute(
                    "UPDATE dim.accounts SET team_id = $1 WHERE id = $2",
                    uuid.UUID(team_id), uuid.UUID(account_id),
                )
    return account


async def touch_last_login(account_id: str) -> None:
    """
    Record a sign-in time (useful for your consulting analysis: who is active?).

    A successful sign-in also clears the wrong-password count and any lock
    (api/services/login_guard.py). On the memory path an unknown account id is ignored.

    Args:
        account_id: str, the account that just signed in.

    Returns:
        None. Updates accounts.last_login_at to now.

    Example:
        >>> await touch_last_login(account_id)
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id].update(last_login_at=_now(), failed_logins=0, login_locked_until=None)
        return
    await database.pool.execute(
        "UPDATE dim.accounts SET last_login_at = now(), failed_logins = 0, login_locked_until = NULL WHERE id = $1",
        uuid.UUID(account_id),
    )


# ── Sign-in lockout (api/services/login_guard.py) ─────────────────────────────

async def record_failed_login(account_id: str, max_attempts: int, lock_minutes: int) -> dict:
    """
    Count one wrong password; lock sign-in once the count reaches max_attempts.

    Args:
        account_id: str, the account.
        max_attempts: int, wrong passwords in a row allowed (LOGIN_MAX_ATTEMPTS).
        lock_minutes: int, how long the lock lasts (LOGIN_LOCK_MINUTES).

    Returns:
        dict: {"failed_logins": int, "login_locked_until": datetime | None}.

    Example:
        >>> await record_failed_login(ana_id, 3, 15)
        {'failed_logins': 1, 'login_locked_until': None}
    """
    if database.pool is None:
        acc = _accounts.get(account_id, {})
        acc["failed_logins"] = (acc.get("failed_logins") or 0) + 1
        acc["login_locked_until"] = (_now() + timedelta(minutes=lock_minutes)
                                     if acc["failed_logins"] >= max_attempts else None)
        return {"failed_logins": acc["failed_logins"], "login_locked_until": acc["login_locked_until"]}
    # One statement, so two wrong tries at the same moment both count. In SET,
    # "failed_logins" means the value BEFORE this update.
    row = await database.pool.fetchrow(
        """UPDATE dim.accounts SET failed_logins = failed_logins + 1,
                  login_locked_until = CASE WHEN failed_logins + 1 >= $2
                                            THEN now() + make_interval(mins => $3) END
           WHERE id = $1 RETURNING failed_logins, login_locked_until""",
        uuid.UUID(account_id), max_attempts, lock_minutes)
    return dict(row) if row else {"failed_logins": 0, "login_locked_until": None}


async def clear_failed_logins(account_id: str) -> None:
    """
    Start the wrong-password count over and lift any lock (lock ended, or password reset).

    Args:
        account_id: str, the account.

    Returns:
        None.

    Example:
        >>> await clear_failed_logins(ana_id)
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id].update(failed_logins=0, login_locked_until=None)
        return
    await database.pool.execute(
        """UPDATE dim.accounts SET failed_logins = 0, login_locked_until = NULL
           WHERE id = $1 AND (failed_logins <> 0 OR login_locked_until IS NOT NULL)""",
        uuid.UUID(account_id))


async def set_credentials(account_id: str, *, auth_user_id: str | None, password_hash: str | None) -> None:
    """
    Save a password (Supabase user id or local hash) and mark the account active.

    Used when an account made FOR someone (an invited colleague, or a client a
    consultant added) signs in for the first time and chooses a password. Also
    used to link an existing Supabase user id after a successful sign-in.

    Args:
        account_id: str, the account's UUID as text.
        auth_user_id: str | None, the Supabase Auth user id (Supabase mode).
        password_hash: str | None, the scrypt hash (local mode).

    Returns:
        None. Updates accounts.auth_user_id / password_hash and sets status "active".

    Example:
        >>> await set_credentials(account_id, auth_user_id=None, password_hash="scrypt$...")
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id].update(auth_user_id=auth_user_id, password_hash=password_hash, status="active")
        return
    await database.pool.execute(
        """UPDATE dim.accounts SET auth_user_id = $2, password_hash = $3, status = 'active'
           WHERE id = $1""",
        uuid.UUID(account_id), uuid.UUID(auth_user_id) if auth_user_id else None, password_hash,
    )


async def list_team_members(team_id: str) -> list[dict]:
    """
    List every account on a team (the owner included), oldest first on the database path.

    Args:
        team_id: str, the team's UUID as text.

    Returns:
        list[dict]: accounts rows on that team ([] if none).

    Example:
        >>> [m["team_role"] for m in await list_team_members(team_id)]
        ['owner', 'developer', 'member']
    """
    if database.pool is None:
        return [a for a in _accounts.values() if a["team_id"] == team_id]
    rows = await database.pool.fetch(
        "SELECT * FROM dim.accounts WHERE team_id = $1 ORDER BY created_at", uuid.UUID(team_id)
    )
    return [dict(r) for r in rows]


async def get_team(team_id: str) -> dict | None:
    """
    Look up a team by its id.

    Args:
        team_id: str, the team's UUID as text.

    Returns:
        dict | None: the teams row ("id", "name", "owner_account_id", ...), or None.

    Example:
        >>> (await get_team(team_id))["name"]
        'Sunrise Bakery'
    """
    if database.pool is None:
        return _teams.get(team_id)
    row = await database.pool.fetchrow("SELECT * FROM dim.teams WHERE id = $1", uuid.UUID(team_id))
    return dict(row) if row else None


async def record_usage(
    *, account_id: str, dataset_name: str, rows: int, runtime_seconds: float,
    billable_minutes: float, amount_usd: float, consultant_id: str | None = None,
    data_fingerprint: str | None = None,
) -> None:
    """
    Save one billing record. Only metadata — never the data itself.
    `consultant_id` is set when a consultant downloaded on the client's behalf.

    Args:
        account_id: str, the client account billed.
        dataset_name: str, the downloaded file's name.
        rows: int, row count of the downloaded table (stored as row_count).
        runtime_seconds: float, measured processing time.
        billable_minutes: float, minutes charged after rounding up.
        amount_usd: float, amount charged in US dollars.
        consultant_id: str | None, the consultant who downloaded, if any.
        data_fingerprint: str | None, the one-way SHA-256 fingerprint of the
            downloaded table (fair_use.data_fingerprint) — a hash, never the data.

    Returns:
        None. Inserts one usage_events row.

    Example:
        >>> await record_usage(account_id=aid, dataset_name="sales.csv", rows=18210,
        ...                    runtime_seconds=0.2, billable_minutes=1.0, amount_usd=0.5)
    """
    event = {"account_id": account_id, "dataset_name": dataset_name, "rows": rows,
             "runtime_seconds": runtime_seconds, "billable_minutes": billable_minutes,
             "amount_usd": amount_usd, "consultant_id": consultant_id,
             "data_fingerprint": data_fingerprint, "created_at": _now()}
    if database.pool is None:
        _usage.append(event)
        return
    await database.pool.execute(
        """INSERT INTO dim.usage_events (account_id, dataset_name, row_count, runtime_seconds,
                                     billable_minutes, amount_usd, consultant_id, data_fingerprint)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8)""",
        uuid.UUID(account_id), dataset_name, rows, runtime_seconds, billable_minutes, amount_usd,
        uuid.UUID(consultant_id) if consultant_id else None, data_fingerprint,
    )


async def recent_usage(account_id: str, since: datetime) -> list[dict]:
    """
    List an account's download records made at or after `since` (for fair use).

    Only the fields the fair-use check needs are returned, newest first.

    Args:
        account_id: str, the client account that was billed.
        since: datetime, timezone-aware start of the window (e.g. 24 hours ago).

    Returns:
        list[dict]: [{"created_at": datetime, "data_fingerprint": str | None,
        "amount_usd": float}, ...].

    Example:
        >>> rows = await recent_usage(aid, datetime.now(timezone.utc) - timedelta(hours=24))
        >>> len(rows)
        2
    """
    if database.pool is None:
        rows = [u for u in _usage if u["account_id"] == account_id and u["created_at"] >= since]
        return sorted(({"created_at": u["created_at"], "data_fingerprint": u.get("data_fingerprint"),
                        "amount_usd": u["amount_usd"]} for u in rows),
                      key=lambda r: r["created_at"], reverse=True)
    rows = await database.pool.fetch(
        """SELECT created_at, data_fingerprint, amount_usd FROM dim.usage_events
           WHERE account_id = $1 AND created_at >= $2 ORDER BY created_at DESC""",
        uuid.UUID(account_id), since,
    )
    return [{"created_at": r["created_at"], "data_fingerprint": r["data_fingerprint"],
             "amount_usd": float(r["amount_usd"])} for r in rows]


# ── Project interests (column dim.accounts.interested_services) ─────────────

async def list_interests(account_id: str) -> list[str]:
    """
    List the services this account would like a consultant's help with (interest only).

    These do NOT unlock tools (only a subscription does); see entitlements.py.

    Args:
        account_id: str, the plan holder's account id.

    Returns:
        list[str]: service ids, in the order they were added.

    Example:
        >>> await list_interests(account_id)
        ['data_visualization']
    """
    if database.pool is None:
        return list((_accounts.get(account_id) or {}).get("interested_services") or [])
    value = await database.pool.fetchval(
        "SELECT interested_services FROM dim.accounts WHERE id = $1", uuid.UUID(account_id))
    return list(value or [])


async def add_interests(account_id: str, service_ids: list[str]) -> None:
    """
    Add services to an account's project interests (ids already there are kept once).

    Args:
        account_id: str, the plan holder's account id.
        service_ids: list[str], service ids to add.

    Returns:
        None. Updates dim.accounts.interested_services.

    Example:
        >>> await add_interests(account_id, ["data_visualization"])
    """
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc is not None:
            acc["interested_services"] = list(dict.fromkeys([*(acc.get("interested_services") or []), *service_ids]))
        return
    # array(SELECT DISTINCT ...) would lose the order, so add only the new ids, in order.
    await database.pool.execute(
        """UPDATE dim.accounts
           SET interested_services = interested_services
               || ARRAY(SELECT s FROM unnest($2::text[]) WITH ORDINALITY AS t(s, n)
                        WHERE NOT s = ANY(interested_services) ORDER BY n)
           WHERE id = $1""",
        uuid.UUID(account_id), list(dict.fromkeys(service_ids)),
    )


# ── Consultants (table dim.consultants) and their clients ───────────────────────

async def get_consultant_by_account(account_id: str) -> dict | None:
    """
    Return the consultant row for a signed-in account, or None if they aren't one.

    Args:
        account_id: str, the account's UUID as text.

    Returns:
        dict | None: the consultants row, or None for clients.

    Example:
        >>> await get_consultant_by_account(client_account_id) is None
        True
    """
    if database.pool is None:
        return next((c for c in _consultants.values() if c["account_id"] == account_id), None)
    row = await database.pool.fetchrow("SELECT * FROM dim.consultants WHERE account_id = $1", uuid.UUID(account_id))
    return dict(row) if row else None


async def get_consultant(consultant_id: str) -> dict | None:
    """
    Look up a consultant by the consultant row's own id (not the account id).

    Args:
        consultant_id: str, the consultants row UUID as text.

    Returns:
        dict | None: the consultants row, or None if it does not exist.

    Example:
        >>> (await get_consultant(cid))["display_name"]
        'Pat'
    """
    if database.pool is None:
        return _consultants.get(consultant_id)
    row = await database.pool.fetchrow("SELECT * FROM dim.consultants WHERE id = $1", uuid.UUID(consultant_id))
    return dict(row) if row else None


async def get_consultant_by_email(email: str) -> dict | None:
    """
    Look up a consultant by email address (letter case is ignored).

    Args:
        email: str, the consultant's address.

    Returns:
        dict | None: the consultants row, or None if no consultant uses that email.

    Example:
        >>> (await get_consultant_by_email("PAT@example.com"))["email"]
        'pat@example.com'
    """
    email = email.lower()
    if database.pool is None:
        return next((c for c in _consultants.values() if c["email"] == email), None)
    row = await database.pool.fetchrow("SELECT * FROM dim.consultants WHERE email = $1", email)
    return dict(row) if row else None


async def ensure_consultant(account: dict, *, is_default: bool) -> dict:
    """
    Create (or refresh) the consultant row for a consultant's account.

    If the row already exists only `is_default` is updated. A new row uses the
    account's first name as the display name and starts active.

    Args:
        account: dict, the consultant's accounts row (needs "id", "first_name", "email").
        is_default: bool, True for the default consultant (first ADMIN_EMAILS address).

    Returns:
        dict: the consultants row.

    Example:
        >>> (await ensure_consultant(pat_account, is_default=True))["is_default"]
        True
    """
    existing = await get_consultant_by_account(str(account["id"]))
    name = account["first_name"]
    if database.pool is None:
        if existing:
            existing["is_default"] = is_default
            return existing
        cid = str(uuid.uuid4())
        _consultants[cid] = {"id": cid, "account_id": str(account["id"]), "display_name": name,
                             "email": account["email"], "is_default": is_default, "active": True,
                             "capacity": DEFAULT_CAPACITY, "free_booking_url": None, "paid_booking_url": None,
                             "calcom_webhook_secret": None, "deactivated_at": None, "deactivated_by": None,
                             "deactivation_reason": None, "reactivated_at": None, "reactivated_by": None,
                             "created_at": _now(), "updated_at": _now()}
        return _consultants[cid]
    row = await database.pool.fetchrow(
        """INSERT INTO dim.consultants (account_id, display_name, email, is_default)
           VALUES ($1, $2, $3, $4)
           ON CONFLICT (account_id) DO UPDATE SET is_default = EXCLUDED.is_default
           RETURNING *""",
        uuid.UUID(str(account["id"])), name, account["email"], is_default,
    )
    return dict(row)


async def list_clients(consultant_id: str) -> list[dict]:
    """
    List the accounts assigned to this consultant (plan holders: individuals and business owners).

    Sorted by company name, or first name when there is no company, ignoring case.

    Args:
        consultant_id: str, the consultants row id.

    Returns:
        list[dict]: accounts rows whose consultant_id is this consultant.

    Example:
        >>> [c["company_name"] for c in await list_clients(cid)]
        ['Acme Logistics', 'Sunrise Bakery']
    """
    if database.pool is None:
        return sorted((a for a in _accounts.values() if a.get("consultant_id") == consultant_id),
                      key=lambda a: (a.get("company_name") or a["first_name"]).lower())
    rows = await database.pool.fetch(
        """SELECT * FROM dim.accounts WHERE consultant_id = $1
           ORDER BY lower(coalesce(company_name, first_name))""",
        uuid.UUID(consultant_id),
    )
    return [dict(r) for r in rows]


async def assign_waiting_clients(consultant_id: str, limit: int) -> int:
    """
    Give clients who asked for a consultant (but have none yet) to this consultant.

    Oldest sign-ups first, and never more than `limit` (the consultant's free
    places), so a consultant is never pushed over their capacity.

    Args:
        consultant_id: str, the consultants row id to assign them to.
        limit: int, the most clients to assign (0 or less assigns nobody).

    Returns:
        int: how many accounts were assigned (on the database path, read from the
            "UPDATE <n>" status PostgreSQL returns).

    Example:
        >>> await assign_waiting_clients(cid, limit=2)
        2
    """
    if limit <= 0:
        return 0
    if database.pool is None:
        waiting = sorted((a for a in _accounts.values() if a.get("wants_consultant") and not a.get("consultant_id")
                          and a.get("team_role") not in ("developer", "member")), key=lambda a: a["created_at"])[:limit]
        for a in waiting:
            a["consultant_id"] = consultant_id
        return len(waiting)
    result = await database.pool.execute(
        """UPDATE dim.accounts SET consultant_id = $1
           WHERE id IN (SELECT id FROM dim.accounts
                        WHERE wants_consultant AND consultant_id IS NULL
                          AND coalesce(team_role, '') NOT IN ('developer', 'member')
                        ORDER BY created_at LIMIT $2)""",
        uuid.UUID(consultant_id), limit,
    )
    return int(result.split()[-1]) if result else 0


async def list_consultants(active_only: bool = True) -> list[dict]:
    """
    Every consultant (active ones by default), each with how many clients they have.

    Args:
        active_only: bool, leave out consultants switched off (active = false).

    Returns:
        list[dict]: consultants rows plus "client_count", sorted by display name.

    Example:
        >>> [(c["display_name"], c["client_count"], c["capacity"]) for c in await list_consultants()]
        [('Brandon', 1, 3), ('Kim', 3, 3)]
    """
    if database.pool is None:
        rows = [dict(c) for c in _consultants.values() if c.get("active", True) or not active_only]
        for c in rows:
            c["client_count"] = sum(1 for a in _accounts.values() if a.get("consultant_id") == c["id"])
        return sorted(rows, key=lambda c: c["display_name"].lower())
    rows = await database.pool.fetch(
        f"""SELECT c.*, (SELECT count(*) FROM dim.accounts a WHERE a.consultant_id = c.id)::int AS client_count
            FROM dim.consultants c {"WHERE c.active" if active_only else ""}
            ORDER BY lower(c.display_name)""")
    return [dict(r) for r in rows]


async def count_clients(consultant_id: str) -> int:
    """
    How many clients (plan holders) are assigned to this consultant.

    Args:
        consultant_id: str, the consultants row id.

    Returns:
        int: the number of accounts whose consultant_id is this consultant.

    Example:
        >>> await count_clients(cid)
        2
    """
    if database.pool is None:
        return sum(1 for a in _accounts.values() if a.get("consultant_id") == consultant_id)
    return await database.pool.fetchval("SELECT count(*) FROM dim.accounts WHERE consultant_id = $1",
                                        uuid.UUID(consultant_id))


async def set_consultant_capacity(consultant_id: str, capacity: int) -> dict | None:
    """
    Save how many clients a consultant takes at once.

    Args:
        consultant_id: str, the consultants row id.
        capacity: int, 1 to MAX_CAPACITY (checked by api/services/consultants.py
            and by the table's CHECK).

    Returns:
        dict | None: the updated consultants row, or None if it doesn't exist.

    Example:
        >>> (await set_consultant_capacity(cid, 5))["capacity"]
        5
    """
    if database.pool is None:
        row = _consultants.get(consultant_id)
        if row:
            row["capacity"] = capacity
        return row
    row = await database.pool.fetchrow("UPDATE dim.consultants SET capacity = $2 WHERE id = $1 RETURNING *",
                                       uuid.UUID(consultant_id), capacity)
    return dict(row) if row else None


async def set_consultant_for(account_id: str, consultant_id: str | None, plan: str | None = None) -> None:
    """
    Assign an account's consultant, and how they help.

    Args:
        account_id: str, the client's account (the plan holder).
        consultant_id: str | None, the consultants row id; None = waiting for one.
        plan: str | None, "one_time" or "monthly".

    Returns:
        None. Sets accounts.wants_consultant, consultant_id and consultant_plan.

    Example:
        >>> await set_consultant_for(client_id, cid, "monthly")
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id].update(wants_consultant=True, consultant_id=consultant_id, consultant_plan=plan)
        return
    await database.pool.execute(
        """UPDATE dim.accounts SET wants_consultant = true, consultant_id = $2, consultant_plan = $3
           WHERE id = $1""",
        uuid.UUID(account_id), uuid.UUID(consultant_id) if consultant_id else None, plan,
    )


async def set_consultant_cal(consultant_id: str, **fields) -> dict | None:
    """
    Save a consultant's Cal.com settings: booking links and/or webhook secret.

    Only the keys given are written (free_booking_url, paid_booking_url,
    calcom_webhook_secret); None clears one.

    Args:
        consultant_id: str, the consultants row id.
        **fields: any of free_booking_url, paid_booking_url, calcom_webhook_secret.

    Returns:
        dict | None: the updated consultants row, or None if it doesn't exist.

    Example:
        >>> (await set_consultant_cal(cid, free_booking_url="https://cal.com/kim/30min"))["free_booking_url"]
        'https://cal.com/kim/30min'
    """
    allowed = ("free_booking_url", "paid_booking_url", "calcom_webhook_secret")
    fields = {k: v for k, v in fields.items() if k in allowed}
    if database.pool is None:
        row = _consultants.get(consultant_id)
        if row:
            row.update(fields)
        return row
    if not fields:
        return await get_consultant(consultant_id)
    sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
    row = await database.pool.fetchrow(f"UPDATE dim.consultants SET {sets} WHERE id = $1 RETURNING *",
                                       uuid.UUID(consultant_id), *fields.values())
    return dict(row) if row else None


async def list_all_clients() -> list[dict]:
    """
    Every client plan holder (admins see them all in "Working on").

    Leaves out consultants/admins and business colleagues (they share their
    owner's plan). Sorted like list_clients.

    Returns:
        list[dict]: accounts rows.

    Example:
        >>> [c["email"] for c in await list_all_clients()]
        ['ana@example.com', 'kai@example.com']
    """
    if database.pool is None:
        rows = [a for a in _accounts.values() if not a.get("is_consultant") and not a.get("is_admin")
                and a.get("team_role") not in ("developer", "member")]
        return sorted(rows, key=lambda a: (a.get("company_name") or a["first_name"]).lower())
    rows = await database.pool.fetch(
        """SELECT * FROM dim.accounts
           WHERE NOT is_consultant AND NOT is_admin AND coalesce(team_role, '') NOT IN ('developer', 'member')
           ORDER BY lower(coalesce(company_name, first_name))""")
    return [dict(r) for r in rows]


async def delete_account(account_id: str, *, reason: str | None = None, had_paid_plan: bool = False,
                         by_team_owner: bool = False) -> list[str]:
    """
    Delete an account and EVERYTHING that belongs to it ("Delete my account").

    One ANONYMOUS row per account removed is written to dim.account_deletions
    (deletion_record: account type, role, age in days, whether they paid, the
    optional reason) in the same transaction — never who they were.

    A business team OWNER takes the whole team with them (every colleague's
    account too, since they share the owner's plan and data). The database
    removes the rest by ON DELETE CASCADE: the consultant row, subscriptions,
    usage, consents, load history, connectors and every stored row in
    fact.account_data. Consultations are removed too (they'd otherwise keep the
    person's email). Clients of a deleted consultant are kept and go back to
    "waiting for a consultant" (consultant_id is set to NULL).

    Args:
        account_id: str, the account to delete.
        reason: str | None, the optional reason picked in the delete dialog (DELETION_REASONS).
        had_paid_plan: bool, they had a live paid package (checked by the caller
            BEFORE billing was canceled).
        by_team_owner: bool, True when the owner removed this colleague
            (api/services/teams.py remove) — recorded as removed_by "team_owner".

    Returns:
        list[str]: the account ids deleted (the owner first).

    Example:
        >>> await delete_account(owner_id)
        ['<owner id>', '<colleague id>']
    """
    acc = await get_account(account_id)
    if acc is None:
        return []
    ids = [account_id]
    if acc.get("team_role") == "owner" and acc.get("team_id"):
        ids += [str(m["id"]) for m in await list_team_members(str(acc["team_id"])) if str(m["id"]) != account_id]
    rows = [a for a in [await get_account(i) for i in ids] if a]
    emails = [a["email"] for a in rows]
    records = [deletion_record(a, is_consultant=await get_consultant_by_account(str(a["id"])) is not None,
                               removed_by="self" if str(a["id"]) == account_id and not by_team_owner
                               else "team_owner",
                               had_paid_plan=had_paid_plan, reason=reason) for a in rows]
    if database.pool is None:
        _deletions.extend({**r, "deleted_at": _now()} for r in records)
        cids = {c["id"] for c in _consultants.values() if c["account_id"] in ids}
        for a in _accounts.values():
            if a.get("consultant_id") in cids:
                a["consultant_id"] = None
        for cid in cids:
            _consultants.pop(cid, None)
        for i in ids:
            _accounts.pop(i, None)
        for t in [t for t, row in _teams.items() if row["owner_account_id"] in ids]:
            _teams.pop(t)
        for k in [k for k, s in _subscriptions.items() if s["account_id"] in ids]:
            _subscriptions.pop(k)
        for k in [k for k, c in _consultations.items() if c.get("account_id") in ids or c.get("attendee_email") in emails]:
            _consultations.pop(k)
        _usage[:] = [u for u in _usage if u["account_id"] not in ids]
        _consents[:] = [c for c in _consents if c["account_id"] not in ids]
        _load_runs[:] = [r for r in _load_runs if r["account_id"] not in ids]
        for k in [k for k in _connections if k[0] in ids]:
            _connections.pop(k)
        for k in [k for k, t in _tabs.items() if t["account_id"] in ids]:
            _tabs.pop(k)
        _questions[:] = [q for q in _questions if q["account_id"] not in ids]
        return ids
    uids = [uuid.UUID(i) for i in ids]
    async with database.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM dim.consultations WHERE account_id = ANY($1) OR attendee_email = ANY($2)",
                               uids, emails)
            # Colleagues first (their team row points at the owner), then the owner.
            await conn.execute("DELETE FROM dim.accounts WHERE id = ANY($1) AND id <> $2", uids, uids[0])
            await conn.execute("DELETE FROM dim.accounts WHERE id = $1", uids[0])
            await conn.executemany(
                """INSERT INTO dim.account_deletions (account_type, role, removed_by, account_age_days,
                                                    had_paid_plan, reason)
                   VALUES ($1, $2, $3, $4, $5, $6)""",
                [(r["account_type"], r["role"], r["removed_by"], r["account_age_days"], r["had_paid_plan"],
                  r["reason"]) for r in records])
    return ids


async def set_team_role(account_id: str, role: str) -> dict | None:
    """
    Change a colleague's team role: "developer" or "member" (api/services/teams.py checks who may).

    Args:
        account_id: str, the colleague's account id.
        role: str, "developer" or "member".

    Returns:
        dict | None: the updated accounts row, or None if it doesn't exist.

    Example:
        >>> (await set_team_role(bo_id, "member"))["team_role"]
        'member'
    """
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc:
            acc.update(team_role=role, updated_at=_now())
        return acc
    row = await database.pool.fetchrow("UPDATE dim.accounts SET team_role = $2 WHERE id = $1 RETURNING *",
                                       uuid.UUID(account_id), role)
    return dict(row) if row else None


async def mark_trial_used(account_id: str) -> None:
    """
    Remember that this account has had its one free trial (accounts.trial_used_at; first time only).

    Args:
        account_id: str, the plan holder.

    Returns:
        None.

    Example:
        >>> await mark_trial_used(ana_id)
    """
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc and not acc.get("trial_used_at"):
            acc["trial_used_at"] = _now()
        return
    await database.pool.execute(
        "UPDATE dim.accounts SET trial_used_at = now() WHERE id = $1 AND trial_used_at IS NULL", uuid.UUID(account_id))

# ── Testers (dim.accounts.is_tester) and the admin's people list ─────────────

async def set_tester(account_id: str, is_tester: bool) -> dict | None:
    """
    Make an account a tester (try paid tools free, even with Stripe live), or a normal client again.

    Args:
        account_id: str, the account (a plan holder: individual or team owner).
        is_tester: bool, True = tester.

    Returns:
        dict | None: the updated accounts row, or None if it doesn't exist.

    Example:
        >>> (await set_tester(ana_id, True))["is_tester"]
        True
    """
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc:
            acc.update(is_tester=bool(is_tester), updated_at=_now())
        return acc
    row = await database.pool.fetchrow("UPDATE dim.accounts SET is_tester = $2 WHERE id = $1 RETURNING *",
                                       uuid.UUID(account_id), bool(is_tester))
    return dict(row) if row else None


async def search_accounts(q: str = "", limit: int = 25) -> list[dict]:
    """
    Plan holders (individuals, team owners, consultants) for the Admin panel, newest first.

    Business colleagues are left out: they share their owner's plan, so the
    tester switch is on the owner.

    Args:
        q: str, part of a name, email or company ("" = everyone).
        limit: int, the most rows to return (1 to 100).

    Returns:
        list[dict]: accounts rows.

    Example:
        >>> [a["email"] for a in await search_accounts("bakery")]
        ['ana@sunrisebakery.com']
    """
    limit = max(1, min(limit, 100))
    needle = q.strip().lower()
    if database.pool is None:
        rows = [a for a in _accounts.values() if a.get("team_role") not in ("developer", "member")
                and (not needle or needle in " ".join(str(a.get(k) or "") for k in
                                                      ("first_name", "last_name", "email", "company_name")).lower())]
        return sorted(rows, key=lambda a: a["created_at"], reverse=True)[:limit]
    rows = await database.pool.fetch(
        """SELECT * FROM dim.accounts
           WHERE coalesce(team_role, '') NOT IN ('developer', 'member')
             AND ($1 = '' OR lower(first_name || ' ' || last_name || ' ' || email || ' ' || coalesce(company_name, ''))
                             LIKE '%' || $1 || '%')
           ORDER BY created_at DESC LIMIT $2""",
        needle, limit)
    return [dict(r) for r in rows]


# ── Consultant lifecycle: deactivate / reactivate (dim.consultants) ──────────

async def set_consultant_active(consultant_id: str, active: bool, admin_id: str, reason: str | None = None) -> dict | None:
    """
    Switch a consultant off (deactivate) or back on (reactivate), stamping who and when.

    Deactivating sets active = false, deactivated_at = now, deactivated_by and
    the optional reason. Reactivating sets active = true, reactivated_at = now
    and reactivated_by (the last deactivation stays on the row as history).
    Moving their clients is done by api/services/consultants.py.

    Args:
        consultant_id: str, the consultants row id.
        active: bool, False = deactivate, True = reactivate.
        admin_id: str, the admin's account id.
        reason: str | None, a short note (deactivating only).

    Returns:
        dict | None: the updated consultants row, or None if it doesn't exist.

    Example:
        >>> (await set_consultant_active(cid, False, admin_id, "On leave"))["active"]
        False
    """
    if database.pool is None:
        row = _consultants.get(consultant_id)
        if row:
            if active:
                row.update(active=True, reactivated_at=_now(), reactivated_by=admin_id, updated_at=_now())
            else:
                row.update(active=False, deactivated_at=_now(), deactivated_by=admin_id,
                           deactivation_reason=reason, updated_at=_now())
        return row
    if active:
        row = await database.pool.fetchrow(
            """UPDATE dim.consultants SET active = true, reactivated_at = now(), reactivated_by = $2
               WHERE id = $1 RETURNING *""", uuid.UUID(consultant_id), uuid.UUID(admin_id))
    else:
        row = await database.pool.fetchrow(
            """UPDATE dim.consultants SET active = false, deactivated_at = now(), deactivated_by = $2,
                      deactivation_reason = $3
               WHERE id = $1 RETURNING *""", uuid.UUID(consultant_id), uuid.UUID(admin_id), reason)
    return dict(row) if row else None


# ── Account deletions (dim.account_deletions): THAT, never WHO ───────────────

_deletions: list[dict] = []    # account_deletions rows (memory path)

DELETION_REASONS = ("too_expensive", "missing_features", "not_needed", "switching_tools", "privacy", "other")


def deletion_record(acc: dict, *, is_consultant: bool, removed_by: str, had_paid_plan: bool,
                    reason: str | None) -> dict:
    """
    The ANONYMOUS row kept when an account is deleted: no id, name, email, company or place.

    Args:
        acc: dict, the accounts row about to be deleted.
        is_consultant: bool, the account had a consultants row.
        removed_by: str, "self" (they deleted it) or "team_owner" (deleted with their owner's account).
        had_paid_plan: bool, a live (paid) package when they left.
        reason: str | None, one of DELETION_REASONS (from the delete dialog), or None.

    Returns:
        dict: {"account_type", "role", "removed_by", "account_age_days", "had_paid_plan", "reason"}.

    Example:
        >>> deletion_record(ana, is_consultant=False, removed_by="self", had_paid_plan=True, reason="not_needed")
        {'account_type': 'individual', 'role': 'client', 'removed_by': 'self', 'account_age_days': 41, ...}
    """
    role = ("consultant" if is_consultant else
            "team_owner" if acc.get("team_role") == "owner" else
            "team_member" if acc.get("team_role") in ("developer", "member") else "client")
    created = acc.get("created_at")
    age = max(0, (_now() - created).days) if isinstance(created, datetime) else None
    return {"account_type": acc["account_type"], "role": role, "removed_by": removed_by,
            "account_age_days": age, "had_paid_plan": bool(had_paid_plan),
            "reason": reason if reason in DELETION_REASONS and removed_by == "self" else None}


async def deletion_summary(days: int = 30) -> dict:
    """
    How many accounts were deleted, and why (the Admin panel's "Account deletions" card).

    Args:
        days: int, the window for "recent" (default the last 30 days).

    Returns:
        dict: {"total", "recent", "recent_days", "by_reason": {reason|"none": n},
        "by_month": [{"month": "2026-10", "count": n}, ...] (last 6 months, newest first)}.

    Example:
        >>> (await deletion_summary())["total"]
        3
    """
    if database.pool is None:
        rows = list(_deletions)
    else:
        rows = [dict(r) for r in await database.pool.fetch(
            "SELECT deleted_at, reason FROM dim.account_deletions WHERE deleted_at > now() - interval '400 days'")]
    since = _now().timestamp() - days * 86400
    by_reason: dict[str, int] = {}
    by_month: dict[str, int] = {}
    for r in rows:
        by_reason[r.get("reason") or "none"] = by_reason.get(r.get("reason") or "none", 0) + 1
        month = r["deleted_at"].strftime("%Y-%m")
        by_month[month] = by_month.get(month, 0) + 1
    total = len(rows) if database.pool is None else await database.pool.fetchval(
        "SELECT count(*) FROM dim.account_deletions")
    return {"total": int(total), "recent": sum(1 for r in rows if r["deleted_at"].timestamp() > since),
            "recent_days": days, "by_reason": by_reason,
            "by_month": [{"month": m, "count": by_month[m]} for m in sorted(by_month, reverse=True)[:6]]}


# ── Roles: admins and consultant requests ──────────────────────────────────────

async def set_admin(account_id: str) -> None:
    """
    Mark an account as an admin (and so a consultant). Called at sign-in for ADMIN_EMAILS.

    Args:
        account_id: str, the admin's account id.

    Returns:
        None. Sets is_admin and is_consultant to true.

    Example:
        >>> await set_admin(my_account_id)
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id].update(is_admin=True, is_consultant=True)
        return
    await database.pool.execute("UPDATE dim.accounts SET is_admin = true, is_consultant = true WHERE id = $1",
                                uuid.UUID(account_id))


async def request_consultant(account_id: str) -> None:
    """
    Record "I'd like to work as a consultant" (waiting for an admin).

    Args:
        account_id: str, the person asking.

    Returns:
        None. Sets consultant_request 'pending' and the request time.

    Example:
        >>> await request_consultant(account_id)
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id].update(consultant_request="pending", consultant_requested_at=_now(),
                                         consultant_decided_at=None, consultant_decided_by=None)
        return
    await database.pool.execute(
        """UPDATE dim.accounts SET consultant_request = 'pending', consultant_requested_at = now(),
                  consultant_decided_at = NULL, consultant_decided_by = NULL
           WHERE id = $1""", uuid.UUID(account_id))


async def decide_consultant_request(account_id: str, approved: bool, admin_id: str) -> None:
    """
    Save an admin's decision on a consultant request.

    Approving sets is_consultant (the caller also creates the consultants row);
    denying leaves the person a normal client.

    Args:
        account_id: str, the person who asked.
        approved: bool, True = approve, False = deny.
        admin_id: str, the admin's account id (consultant_decided_by).

    Returns:
        None.

    Example:
        >>> await decide_consultant_request(kim_id, True, my_account_id)
    """
    decision = "approved" if approved else "denied"
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id].update(consultant_request=decision, consultant_decided_at=_now(),
                                         consultant_decided_by=admin_id, is_consultant=approved)
        return
    await database.pool.execute(
        """UPDATE dim.accounts SET consultant_request = $2, consultant_decided_at = now(),
                  consultant_decided_by = $3, is_consultant = $4
           WHERE id = $1""",
        uuid.UUID(account_id), decision, uuid.UUID(admin_id), approved)


async def list_consultant_requests() -> list[dict]:
    """
    Everyone who asked to be a consultant: pending first (oldest first), then decided.

    Returns:
        list[dict]: accounts rows with a consultant_request.

    Example:
        >>> [(a["email"], a["consultant_request"]) for a in await list_consultant_requests()]
        [('kim@example.com', 'pending'), ('lee@example.com', 'approved')]
    """
    order = {"pending": 0, "approved": 1, "denied": 2}
    if database.pool is None:
        rows = [a for a in _accounts.values() if a.get("consultant_request")]
        return sorted(rows, key=lambda a: (order[a["consultant_request"]],
                                           a.get("consultant_requested_at") or a["created_at"]))
    rows = await database.pool.fetch(
        """SELECT * FROM dim.accounts WHERE consultant_request IS NOT NULL
           ORDER BY CASE consultant_request WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END,
                    consultant_requested_at""")
    return [dict(r) for r in rows]


# ── Payments: subscriptions (see api/services/billing.py) ───────────────────

LIVE_STATUSES = ("active", "trialing", "past_due")   # these keep a plan switched on


async def set_stripe_customer(account_id: str, customer_id: str) -> None:
    """
    Save the Stripe customer id on an account (set the first time they pay).

    On the memory path an unknown account id is ignored.

    Args:
        account_id: str, the account's UUID as text.
        customer_id: str, the Stripe customer id ("cus_...").

    Returns:
        None. Updates accounts.stripe_customer_id.

    Example:
        >>> await set_stripe_customer(account_id, "cus_Q1w2e3")
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id]["stripe_customer_id"] = customer_id
        return
    await database.pool.execute("UPDATE dim.accounts SET stripe_customer_id = $2 WHERE id = $1",
                                uuid.UUID(account_id), customer_id)


async def get_account_by_stripe_customer(customer_id: str) -> dict | None:
    """
    Look up an account by its Stripe customer id (used by the webhook).

    Args:
        customer_id: str, the Stripe customer id ("cus_...").

    Returns:
        dict | None: the accounts row, or None if no account has that customer id.

    Example:
        >>> (await get_account_by_stripe_customer("cus_Q1w2e3"))["email"]
        'ana@bakery.com'
    """
    if database.pool is None:
        return next((a for a in _accounts.values() if a.get("stripe_customer_id") == customer_id), None)
    row = await database.pool.fetchrow("SELECT * FROM dim.accounts WHERE stripe_customer_id = $1", customer_id)
    return dict(row) if row else None


async def upsert_subscription(*, account_id: str, billing_interval: str, status: str, items: list[dict],
                              stripe_subscription_id: str | None = None, stripe_customer_id: str | None = None,
                              cancel_at_period_end: bool = False, current_period_end: datetime | None = None,
                              subscription_id: str | None = None) -> dict:
    """
    Create or update one subscription and REPLACE its tools
    (items = [{"plan_id": ..., "stripe_subscription_item_id": ...}], stored as
    one JSON list in dim.subscriptions.items). Matched by our id (test mode) or
    Stripe's id.

    On the database path, `subscription_id` given -> UPDATE that row; otherwise
    INSERT, or update the existing row with the same `stripe_subscription_id`.

    Args:
        account_id: str, the plan holder's account id.
        billing_interval: str, "month" or "year".
        status: str, Stripe status ("active", "trialing", "past_due", "canceled", ...).
        items: list[dict], one {"plan_id", "stripe_subscription_item_id" (optional)}
            per tool.
        stripe_subscription_id: str | None, Stripe's subscription id ("sub_...").
        stripe_customer_id: str | None, Stripe's customer id.
        cancel_at_period_end: bool, True if it ends at the end of the period.
        current_period_end: datetime | None, when the current period ends.
        subscription_id: str | None, our own subscription id (test mode updates).

    Returns:
        dict: the subscription row with its "items" list.

    Example:
        >>> sub = await upsert_subscription(account_id=aid, billing_interval="month",
        ...                                 status="active", items=[{"plan_id": "all_tools"}])
        >>> sub["items"]
        [{'plan_id': 'all_tools', 'stripe_subscription_item_id': None}]
    """
    row = {"account_id": account_id, "billing_interval": billing_interval, "status": status,
           "stripe_subscription_id": stripe_subscription_id, "stripe_customer_id": stripe_customer_id,
           "cancel_at_period_end": cancel_at_period_end, "current_period_end": current_period_end,
           "updated_at": _now()}
    items = [{"plan_id": i["plan_id"], "stripe_subscription_item_id": i.get("stripe_subscription_item_id")}
             for i in items]
    if database.pool is None:
        existing = _subscriptions.get(subscription_id or "") or next(
            (s for s in _subscriptions.values()
             if stripe_subscription_id and s["stripe_subscription_id"] == stripe_subscription_id), None)
        if existing:
            existing.update(row, items=items)
            return existing
        sid = str(uuid.uuid4())
        _subscriptions[sid] = {"id": sid, "created_at": _now(), **row, "items": items}
        return _subscriptions[sid]
    # The tools are one JSON list in the row, so the row and its tools always change together.
    items_json = json.dumps(items)
    if subscription_id:
        rec = await database.pool.fetchrow(
            """UPDATE dim.subscriptions SET billing_interval = $2, status = $3, cancel_at_period_end = $4,
                      current_period_end = $5, items = $6::jsonb, updated_at = now() WHERE id = $1 RETURNING *""",
            uuid.UUID(subscription_id), billing_interval, status, cancel_at_period_end, current_period_end,
            items_json)
    else:
        rec = await database.pool.fetchrow(
            """INSERT INTO dim.subscriptions (account_id, billing_interval, status, items, stripe_subscription_id,
                                              stripe_customer_id, cancel_at_period_end, current_period_end)
               VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7, $8)
               ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                   billing_interval = EXCLUDED.billing_interval, status = EXCLUDED.status, items = EXCLUDED.items,
                   cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                   current_period_end = EXCLUDED.current_period_end, updated_at = now()
               RETURNING *""",
            uuid.UUID(account_id), billing_interval, status, items_json, stripe_subscription_id,
            stripe_customer_id, cancel_at_period_end, current_period_end)
    return {**dict(rec), "items": items}


async def list_subscriptions(account_id: str, live_only: bool = True) -> list[dict]:
    """
    List an account's subscriptions (each with its "items"), newest first; by default only live ones.

    Live means status in LIVE_STATUSES ("active", "trialing", "past_due").

    Args:
        account_id: str, the plan holder's account id.
        live_only: bool, True (default) to skip canceled/ended subscriptions.

    Returns:
        list[dict]: subscription rows, each with "items" (plan_id and
            stripe_subscription_item_id).

    Example:
        >>> [i["plan_id"] for i in (await list_subscriptions(aid))[0]["items"]]
        ['all_tools']
    """
    if database.pool is None:
        rows = [s for s in _subscriptions.values() if s["account_id"] == account_id
                and (not live_only or s["status"] in LIVE_STATUSES)]
        return sorted(rows, key=lambda s: s["created_at"], reverse=True)
    sql = "SELECT * FROM dim.subscriptions WHERE account_id = $1"
    if live_only:
        sql += " AND status = ANY($2::text[])"
    sql += " ORDER BY created_at DESC"
    args = [uuid.UUID(account_id)] + ([list(LIVE_STATUSES)] if live_only else [])
    subs = [dict(r) for r in await database.pool.fetch(sql, *args)]
    for sub in subs:
        sub["items"] = _json_list(sub.get("items"))
    return subs


def _json_list(value) -> list:
    """
    A JSONB list from asyncpg (it arrives as text) as a Python list.

    Example:
        >>> _json_list('[{"plan_id": "ai"}]')
        [{'plan_id': 'ai'}]
    """
    if value is None:
        return []
    return json.loads(value) if isinstance(value, str) else list(value)


async def set_subscription_status(subscription_id: str, status: str, cancel_at_period_end: bool = False) -> None:
    """
    Set a subscription's status by our own id. Test mode only (no Stripe): cancel by our own id.

    Args:
        subscription_id: str, our subscription id.
        status: str, the new status (e.g. "canceled").
        cancel_at_period_end: bool, stored alongside (default False).

    Returns:
        None.

    Example:
        >>> await set_subscription_status(sub_id, "canceled")
    """
    if database.pool is None:
        if subscription_id in _subscriptions:
            _subscriptions[subscription_id].update(status=status, cancel_at_period_end=cancel_at_period_end,
                                                   updated_at=_now())
        return
    await database.pool.execute(
        "UPDATE dim.subscriptions SET status = $2, cancel_at_period_end = $3, updated_at = now() WHERE id = $1",
        uuid.UUID(subscription_id), status, cancel_at_period_end,
    )


# ── Consultations (table dim.consultations; see api/services/consultations.py) ──

_CONSULTATION_FIELDS = ("account_id", "attendee_email", "attendee_name", "kind", "status", "source",
                        "calcom_booking_uid", "starts_at", "ends_at", "consultant_id", "confirmed_by",
                        "confirmed_at", "note")
_UUID_FIELDS = {"account_id", "consultant_id", "confirmed_by"}   # stored as UUID in Postgres


def _db_value(field: str, value):
    """
    Convert one consultation value for asyncpg (UUID columns need uuid.UUID).

    Args:
        field: str, the column name.
        value: the value from Python (str ids, datetimes, text).

    Returns:
        The value ready for the query.
    """
    return uuid.UUID(str(value)) if field in _UUID_FIELDS and value else value


async def save_consultation(row: dict) -> dict:
    """
    Insert a consultation, or update it when `row` has an "id".

    Only the keys in `row` are written on an update, so a caller can change
    just the status, say. `updated_at` is always refreshed.

    Args:
        row: dict, consultations columns (see db/schema.sql). Include "id" to update.

    Returns:
        dict: the full consultations row after the change.

    Example:
        >>> c = await save_consultation({"account_id": aid, "kind": "free", "status": "booked",
        ...                              "source": "calcom", "calcom_booking_uid": "abc"})
        >>> (await save_consultation({"id": c["id"], "status": "completed"}))["status"]
        'completed'
    """
    fields = {k: v for k, v in row.items() if k in _CONSULTATION_FIELDS}
    if database.pool is None:
        if row.get("id") and str(row["id"]) in _consultations:
            existing = _consultations[str(row["id"])]
            existing.update(fields, updated_at=_now())
            return existing
        cid = str(uuid.uuid4())
        base = dict.fromkeys(_CONSULTATION_FIELDS)
        _consultations[cid] = {**base, **fields, "id": cid, "created_at": _now(), "updated_at": _now()}
        return _consultations[cid]
    if row.get("id"):
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
        rec = await database.pool.fetchrow(
            f"UPDATE dim.consultations SET {sets}, updated_at = now() WHERE id = $1 RETURNING *",
            uuid.UUID(str(row["id"])), *(_db_value(k, v) for k, v in fields.items()),
        )
    else:
        cols = ", ".join(fields)
        marks = ", ".join(f"${i + 1}" for i in range(len(fields)))
        rec = await database.pool.fetchrow(
            f"INSERT INTO dim.consultations ({cols}) VALUES ({marks}) RETURNING *",
            *(_db_value(k, v) for k, v in fields.items()),
        )
    return dict(rec)


async def get_consultation(consultation_id: str) -> dict | None:
    """
    Look up one consultation by its id.

    Args:
        consultation_id: str, the consultation's UUID as text.

    Returns:
        dict | None: the consultations row, or None.

    Example:
        >>> (await get_consultation(cid))["kind"]
        'free'
    """
    if database.pool is None:
        return _consultations.get(consultation_id)
    try:
        key = uuid.UUID(consultation_id)
    except ValueError:
        return None
    row = await database.pool.fetchrow("SELECT * FROM dim.consultations WHERE id = $1", key)
    return dict(row) if row else None


async def get_consultation_by_booking(uid: str) -> dict | None:
    """
    Look up a consultation by Cal.com's booking uid (used by the webhook).

    Args:
        uid: str, Cal.com's booking uid.

    Returns:
        dict | None: the consultations row, or None.

    Example:
        >>> (await get_consultation_by_booking("bk_123"))["status"]
        'booked'
    """
    if database.pool is None:
        return next((c for c in _consultations.values() if c["calcom_booking_uid"] == uid), None)
    row = await database.pool.fetchrow("SELECT * FROM dim.consultations WHERE calcom_booking_uid = $1", uid)
    return dict(row) if row else None


async def list_consultations(account_id: str, email: str | None = None) -> list[dict]:
    """
    List a client's consultations, newest first.

    Matches rows linked to the account, plus rows booked with the account's
    email before the account existed (account_id still NULL).

    Args:
        account_id: str, the plan holder's account id.
        email: str | None, the plan holder's email (lowercase).

    Returns:
        list[dict]: consultations rows.

    Example:
        >>> [c["status"] for c in await list_consultations(aid, "ana@example.com")]
        ['completed']
    """
    email = (email or "").lower()
    if database.pool is None:
        rows = [c for c in _consultations.values()
                if str(c.get("account_id") or "") == account_id
                or (email and not c.get("account_id") and c.get("attendee_email") == email)]
        return sorted(rows, key=lambda c: c["created_at"], reverse=True)
    rows = await database.pool.fetch(
        """SELECT * FROM dim.consultations
           WHERE account_id = $1 OR (account_id IS NULL AND attendee_email = $2)
           ORDER BY created_at DESC""",
        uuid.UUID(account_id), email,
    )
    return [dict(r) for r in rows]


# ── Managed storage: consents and load runs ─────────────────────────────────

async def active_storage_consent(account_id: str) -> dict | None:
    """
    Return the client's current "store my data" consent, or None.

    Args:
        account_id: str, the plan holder's account id.

    Returns:
        dict | None: the newest storage_consents row that hasn't been withdrawn.

    Example:
        >>> (await active_storage_consent(aid))["terms_version"]
        '2026-10-15'
    """
    if database.pool is None:
        live = [c for c in _consents if c["account_id"] == account_id and c["withdrawn_at"] is None]
        return live[-1] if live else None
    row = await database.pool.fetchrow(
        """SELECT * FROM dim.storage_consents WHERE account_id = $1 AND withdrawn_at IS NULL
           ORDER BY consented_at DESC LIMIT 1""",
        uuid.UUID(account_id),
    )
    return dict(row) if row else None


async def add_storage_consent(account_id: str, terms_version: str, consented_by: str) -> dict:
    """
    Record a client's consent to managed storage.

    Args:
        account_id: str, the plan holder's account id.
        terms_version: str, which storage terms they agreed to.
        consented_by: str, the account that clicked (the client, or a consultant acting).

    Returns:
        dict: the new storage_consents row.

    Example:
        >>> await add_storage_consent(aid, "2026-10-15", aid)
    """
    row = {"account_id": account_id, "terms_version": terms_version, "consented_by": consented_by,
           "consented_at": _now(), "withdrawn_at": None}
    if database.pool is None:
        row["id"] = len(_consents) + 1
        _consents.append(row)
        return row
    rec = await database.pool.fetchrow(
        """INSERT INTO dim.storage_consents (account_id, terms_version, consented_by)
           VALUES ($1, $2, $3) RETURNING *""",
        uuid.UUID(account_id), terms_version, uuid.UUID(consented_by),
    )
    return dict(rec)


async def withdraw_storage_consent(account_id: str) -> int:
    """
    Mark every active storage consent of a client as withdrawn (now).

    Called when the client deletes their stored data: loading again then needs
    a fresh consent.

    Args:
        account_id: str, the plan holder's account id.

    Returns:
        int: how many consents were withdrawn.

    Example:
        >>> await withdraw_storage_consent(aid)
        1
    """
    if database.pool is None:
        live = [c for c in _consents if c["account_id"] == account_id and c["withdrawn_at"] is None]
        for c in live:
            c["withdrawn_at"] = _now()
        return len(live)
    result = await database.pool.execute(
        "UPDATE dim.storage_consents SET withdrawn_at = now() WHERE account_id = $1 AND withdrawn_at IS NULL",
        uuid.UUID(account_id),
    )
    return int(result.split()[-1]) if result else 0


async def save_load_run(row: dict) -> dict:
    """
    Insert a load run, or update it when `row` has an "id".

    Args:
        row: dict, load_runs columns (see db/schema.sql). Include "id" to update.

    Returns:
        dict: the load_runs row after the change.

    Example:
        >>> run = await save_load_run({"account_id": aid, "source_name": "sales.csv",
        ...                            "event_type": "transaction/income", "status": "running"})
        >>> (await save_load_run({"id": run["id"], "status": "succeeded"}))["status"]
        'succeeded'
    """
    allowed = ("account_id", "source", "source_name", "event_type", "rows_in", "rows_loaded", "rows_updated",
               "rows_skipped", "column_plan", "plan_fingerprint", "status", "error", "started_by", "finished_at")
    fields = {k: v for k, v in row.items() if k in allowed}
    uuid_fields = {"account_id", "started_by"}
    if database.pool is None:
        if row.get("id"):
            existing = next(r for r in _load_runs if r["id"] == row["id"])
            existing.update(fields)
            return existing
        new = {"source": "file_upload", "rows_in": 0, "rows_loaded": 0, "rows_updated": 0, "rows_skipped": 0,
               "column_plan": None, "error": None, "plan_fingerprint": None, "started_by": None,
               "finished_at": None, **fields, "id": len(_load_runs) + 1, "started_at": _now()}
        _load_runs.append(new)
        return new
    values = [uuid.UUID(str(v)) if k in uuid_fields and v
              else json.dumps(v, default=str) if k == "column_plan" and v is not None else v
              for k, v in fields.items()]
    cast = lambda k, i: f"${i}::jsonb" if k == "column_plan" else f"${i}"   # noqa: E731 — JSON goes in as text
    if row.get("id"):
        sets = ", ".join(f"{k} = {cast(k, i + 2)}" for i, k in enumerate(fields))
        rec = await database.pool.fetchrow(f"UPDATE dim.load_runs SET {sets} WHERE id = $1 RETURNING *",
                                           int(row["id"]), *values)
    else:
        cols = ", ".join(fields)
        marks = ", ".join(cast(k, i + 1) for i, k in enumerate(fields))
        rec = await database.pool.fetchrow(f"INSERT INTO dim.load_runs ({cols}) VALUES ({marks}) RETURNING *", *values)
    return _load_run_out(rec)


def _load_run_out(rec) -> dict:
    """A dim.load_runs row as a dict, with column_plan as a Python list (asyncpg gives JSON as text)."""
    out = dict(rec)
    if isinstance(out.get("column_plan"), str):
        out["column_plan"] = json.loads(out["column_plan"])
    return out


async def list_load_runs(account_id: str, limit: int = 20) -> list[dict]:
    """
    List a client's most recent loads into the warehouse, newest first.

    Args:
        account_id: str, the plan holder's account id.
        limit: int, how many to return (default 20).

    Returns:
        list[dict]: load_runs rows.

    Example:
        >>> [r["source_name"] for r in await list_load_runs(aid)]
        ['sales.csv']
    """
    if database.pool is None:
        rows = [r for r in _load_runs if r["account_id"] == account_id]
        return sorted(rows, key=lambda r: r["started_at"], reverse=True)[:limit]
    rows = await database.pool.fetch(
        "SELECT * FROM dim.load_runs WHERE account_id = $1 ORDER BY started_at DESC LIMIT $2",
        uuid.UUID(account_id), limit,
    )
    return [_load_run_out(r) for r in rows]


async def latest_column_plan(account_id: str, source: str, source_name: str) -> list[dict] | None:
    """
    How a source's columns were stored in its most recent successful load.

    "Use my stored data" needs it to give the rebuilt table the client's own
    column names and order (api/services/warehouse_store.py export).

    Args:
        account_id: str, the plan holder's account id.
        source: str, the source system, e.g. "file_upload".
        source_name: str, e.g. "sales.csv".

    Returns:
        list[dict] | None: the column plan (unified.plan_columns), or None when
        no successful load recorded one.

    Example:
        >>> [c["column"] for c in await latest_column_plan(aid, "file_upload", "sales.csv")][:2]
        ['order_id', 'order_date']
    """
    if database.pool is None:
        runs = [r for r in _load_runs if r["account_id"] == account_id and r.get("source", "file_upload") == source
                and r["source_name"] == source_name and r["status"] == "succeeded" and r.get("column_plan")]
        return runs[-1]["column_plan"] if runs else None
    value = await database.pool.fetchval(
        """SELECT column_plan FROM dim.load_runs
           WHERE account_id = $1 AND source = $2 AND source_name = $3 AND status = 'succeeded'
             AND column_plan IS NOT NULL
           ORDER BY started_at DESC LIMIT 1""",
        uuid.UUID(account_id), source, source_name)
    return json.loads(value) if isinstance(value, str) else value

# ── Data connections (table dim.data_connections) ───────────────────────────

async def list_connections(account_id: str) -> list[dict]:
    """
    List a client's connector settings (schedule and status — never the data).

    Args:
        account_id: str, the client account (plan holder).

    Returns:
        list[dict]: data_connections rows as dicts ("connector", "refresh_frequency",
        "status", "last_synced_at", "next_sync_at", ...), ordered by connector.

    Example:
        >>> [c["connector"] for c in await list_connections(aid)]
        ['quickbooks']
    """
    if database.pool is None:
        return sorted((dict(c) for (aid, _), c in _connections.items() if aid == account_id),
                      key=lambda c: c["connector"])
    rows = await database.pool.fetch(
        "SELECT * FROM dim.data_connections WHERE account_id = $1 ORDER BY connector", uuid.UUID(account_id))
    return [dict(r) for r in rows]


async def set_connection_frequency(*, account_id: str, connector: str, refresh_frequency: str,
                                   next_sync_at: datetime | None, consultant_id: str | None = None) -> dict:
    """
    Create or update a client's connector with a refresh frequency.

    A new row starts with status "pending" (schedule chosen, not connected yet).
    An existing row keeps its status and last sync; only the frequency, the next
    sync time and updated_at change.

    Args:
        account_id: str, the client account (plan holder).
        connector: str, e.g. "quickbooks" (validated by connections.validate first).
        refresh_frequency: str, a pricing.REFRESH_OPTIONS id.
        next_sync_at: datetime | None, from connections.next_sync_at (None = manual).
        consultant_id: str | None, the consultant who set it up, if any.

    Returns:
        dict: the saved row.

    Example:
        >>> row = await set_connection_frequency(account_id=aid, connector="quickbooks",
        ...                                      refresh_frequency="weekly", next_sync_at=None)
        >>> row["status"], row["refresh_frequency"]
        ('pending', 'weekly')
    """
    if database.pool is None:
        row = _connections.setdefault((account_id, connector), {
            "id": str(uuid.uuid4()), "account_id": account_id, "connector": connector, "status": "pending",
            "last_synced_at": None, "consultant_id": consultant_id, "created_at": _now()})
        row.update(refresh_frequency=refresh_frequency, next_sync_at=next_sync_at, updated_at=_now())
        return dict(row)
    row = await database.pool.fetchrow(
        """INSERT INTO dim.data_connections (account_id, consultant_id, connector, refresh_frequency, next_sync_at)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (account_id, connector) DO UPDATE
               SET refresh_frequency = EXCLUDED.refresh_frequency,
                   next_sync_at = EXCLUDED.next_sync_at, updated_at = now()
           RETURNING *""",
        uuid.UUID(account_id), uuid.UUID(consultant_id) if consultant_id else None, connector,
        refresh_frequency, next_sync_at,
    )
    return dict(row)


# ── Passwords: "forgot password" in local mode (api/services/auth.py) ─────────

async def set_password_reset(account_id: str, token_hash: str | None, expires_at: datetime | None) -> None:
    """
    Save (or clear, with None) the one-way hash of a password-reset token and its expiry.

    Args:
        account_id: str, the account.
        token_hash: str | None, sha256 of the emailed token (never the token itself).
        expires_at: datetime | None, when the link stops working.

    Returns:
        None.

    Example:
        >>> await set_password_reset(aid, "9f2c...", datetime.now(timezone.utc) + timedelta(hours=1))
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id].update(password_reset_hash=token_hash, password_reset_expires_at=expires_at)
        return
    await database.pool.execute(
        "UPDATE dim.accounts SET password_reset_hash = $2, password_reset_expires_at = $3 WHERE id = $1",
        uuid.UUID(account_id), token_hash, expires_at)


async def get_account_by_reset_hash(token_hash: str) -> dict | None:
    """
    Find the account whose reset link this is, if it hasn't expired.

    Args:
        token_hash: str, sha256 of the token from the link.

    Returns:
        dict | None: the accounts row, or None (unknown or expired).
    """
    if database.pool is None:
        return next((a for a in _accounts.values() if a.get("password_reset_hash") == token_hash
                     and a.get("password_reset_expires_at") and a["password_reset_expires_at"] > _now()), None)
    row = await database.pool.fetchrow(
        "SELECT * FROM dim.accounts WHERE password_reset_hash = $1 AND password_reset_expires_at > now()", token_hash)
    return dict(row) if row else None


async def get_account_by_auth_user(auth_user_id: str) -> dict | None:
    """
    Find an account by its Supabase Auth user id (after a Supabase password reset).

    Args:
        auth_user_id: str, the Supabase user id.

    Returns:
        dict | None: the accounts row.
    """
    if database.pool is None:
        return next((a for a in _accounts.values() if str(a.get("auth_user_id") or "") == auth_user_id), None)
    row = await database.pool.fetchrow("SELECT * FROM dim.accounts WHERE auth_user_id = $1", uuid.UUID(auth_user_id))
    return dict(row) if row else None


# ── Business context (dashboards) ─────────────────────────────────────────────

async def set_business_context(account_id: str, context: dict) -> None:
    """
    Save "About your business" ({"about", "goals", "key_questions"}) on the plan holder.

    Args:
        account_id: str, the plan holder.
        context: dict, the cleaned-up context (api/services/dashboards.py).

    Returns:
        None.

    Example:
        >>> await set_business_context(aid, {"about": "Two bakeries in Atlanta", "goals": "", "key_questions": []})
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id]["business_context"] = dict(context)
        return
    await database.pool.execute("UPDATE dim.accounts SET business_context = $2::jsonb WHERE id = $1",
                                uuid.UUID(account_id), json.dumps(context))


def business_context_of(account: dict) -> dict:
    """The account's business context as a dict (asyncpg returns JSON as text)."""
    raw = account.get("business_context") or {}
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


# ── Integrations: saved logins (encrypted) ──────────────────────────────────────

async def save_integration(*, account_id: str, connector: str, kind: str, display_name: str,
                           credentials_encrypted: str | None, consultant_id: str | None = None,
                           refresh_frequency: str = "manual") -> dict:
    """
    Create or update an integration as CONNECTED, with its (already encrypted) login.

    Args:
        account_id: str, the client account (plan holder).
        connector: str, e.g. "supabase" (api/services/connections.py CONNECTORS).
        kind: str, "cloud_storage" or "business_tool".
        display_name: str, what to show ("db.abcd.supabase.co / postgres").
        credentials_encrypted: str | None, from cloud_sources.encrypt — never plain text.
        consultant_id: str | None, the consultant who set it up, if any (an
            existing row keeps the one it has).
        refresh_frequency: str, for a NEW row only ("daily" for business tools,
            whose stored sync has a schedule); an existing row keeps its choice.

    Returns:
        dict: the saved data_connections row.
    """
    if database.pool is None:
        row = _connections.setdefault((account_id, connector), {
            "id": str(uuid.uuid4()), "account_id": account_id, "connector": connector,
            "refresh_frequency": refresh_frequency, "next_sync_at": None, "last_synced_at": None,
            "consultant_id": consultant_id, "created_at": _now()})
        row.update(kind=kind, display_name=display_name, credentials_encrypted=credentials_encrypted,
                   status="connected", updated_at=_now())
        return dict(row)
    row = await database.pool.fetchrow(
        """INSERT INTO dim.data_connections (account_id, consultant_id, connector, refresh_frequency, status,
                                            kind, display_name, credentials_encrypted)
           VALUES ($1, $2, $3, $7, 'connected', $4, $5, $6)
           ON CONFLICT (account_id, connector) DO UPDATE
               SET status = 'connected', kind = EXCLUDED.kind, display_name = EXCLUDED.display_name,
                   credentials_encrypted = EXCLUDED.credentials_encrypted, updated_at = now()
           RETURNING *""",
        uuid.UUID(account_id), uuid.UUID(consultant_id) if consultant_id else None, connector, kind,
        display_name, credentials_encrypted, refresh_frequency)
    return dict(row)


async def get_integration(account_id: str, connector: str) -> dict | None:
    """One integration row (with its encrypted login), or None."""
    if database.pool is None:
        row = _connections.get((account_id, connector))
        return dict(row) if row else None
    row = await database.pool.fetchrow("SELECT * FROM dim.data_connections WHERE account_id = $1 AND connector = $2",
                                       uuid.UUID(account_id), connector)
    return dict(row) if row else None


async def save_sync_state(account_id: str, connector: str, state: dict) -> None:
    """Save the stored sync's progress (per record type: the newest change already stored)."""
    if database.pool is None:
        row = _connections.get((account_id, connector))
        if row:
            row["sync_state"] = dict(state)
        return
    await database.pool.execute(
        "UPDATE dim.data_connections SET sync_state = $3::jsonb, updated_at = now() WHERE account_id = $1 AND connector = $2",
        uuid.UUID(account_id), connector, json.dumps(state))


async def finish_sync(account_id: str, connector: str, *, last_synced_at, next_sync_at, status: str) -> None:
    """After a sync: when it last finished, when the scheduler runs it next, and its status."""
    if database.pool is None:
        row = _connections.get((account_id, connector))
        if row:
            row.update(last_synced_at=last_synced_at, next_sync_at=next_sync_at, status=status, updated_at=_now())
        return
    await database.pool.execute(
        """UPDATE dim.data_connections SET last_synced_at = coalesce($3, last_synced_at), next_sync_at = $4,
                  status = $5, updated_at = now() WHERE account_id = $1 AND connector = $2""",
        uuid.UUID(account_id), connector, last_synced_at, next_sync_at, status)


async def list_due_connections(now: datetime) -> list[dict]:
    """Connections the scheduler should sync now: connected, on a schedule, and due."""
    if database.pool is None:
        return [dict(c) for c in _connections.values()
                if c.get("status") == "connected" and c.get("next_sync_at") and c["next_sync_at"] <= now
                and c.get("refresh_frequency") != "manual"]
    rows = await database.pool.fetch(
        """SELECT * FROM dim.data_connections WHERE status = 'connected' AND next_sync_at <= $1
           AND refresh_frequency <> 'manual' ORDER BY next_sync_at""", now)
    return [dict(r) for r in rows]


async def count_connected(connector: str) -> int:
    """
    How many clients have this integration connected, across the whole platform
    (Xero's free plan allows 5 connected organizations per app).

    Example:
        >>> await count_connected("xero")
        3
    """
    if database.pool is None:
        return sum(1 for (_, cid), c in _connections.items() if cid == connector and c.get("status") == "connected")
    return await database.pool.fetchval(
        "SELECT count(*) FROM dim.data_connections WHERE connector = $1 AND status = 'connected'", connector)


async def save_api_usage(account_id: str, connector: str, day, count: int) -> None:
    """Save how many API calls were made to a connection on `day` (UTC) — see CallMeter."""
    if database.pool is None:
        row = _connections.get((account_id, connector))
        if row:
            row.update(api_calls_day=day, api_calls_count=count)
        return
    await database.pool.execute(
        "UPDATE dim.data_connections SET api_calls_day = $3, api_calls_count = $4 WHERE account_id = $1 AND connector = $2",
        uuid.UUID(account_id), connector, day, count)


_api_usage: dict[tuple[str, date], int] = {}   # (connector, month) -> calls (memory path)


async def get_api_usage(connector: str, month: date) -> int:
    """Calls the whole app made to a tool in a month (dim.api_usage; 0 when none)."""
    if database.pool is None:
        return _api_usage.get((connector, month), 0)
    return await database.pool.fetchval(
        "SELECT coalesce((SELECT calls FROM dim.api_usage WHERE connector = $1 AND month = $2), 0)", connector, month)


async def add_api_usage(connector: str, month: date, calls: int) -> None:
    """Add calls to a tool's monthly count (one small upsert per request, never per call)."""
    if calls <= 0:
        return
    if database.pool is None:
        _api_usage[(connector, month)] = _api_usage.get((connector, month), 0) + calls
        return
    await database.pool.execute(
        """INSERT INTO dim.api_usage (connector, month, calls) VALUES ($1, $2, $3)
           ON CONFLICT (connector, month) DO UPDATE SET calls = dim.api_usage.calls + EXCLUDED.calls""",
        connector, month, calls)


_ai_usage: dict[tuple[str, date], int] = {}    # (account_id, day) -> AI questions (memory path)


async def ai_questions_since(account_id: str, since: date) -> int:
    """AI questions the plan holder asked since a day (the 1st of the month -> this month's count)."""
    if database.pool is None:
        return sum(n for (aid, day), n in _ai_usage.items() if aid == account_id and day >= since)
    return await database.pool.fetchval(
        "SELECT coalesce(sum(question_count), 0)::int FROM dim.ai_usage WHERE account_id = $1 AND usage_date >= $2",
        uuid.UUID(account_id), since)


async def add_ai_question(account_id: str, day: date) -> None:
    """Count one AI question for the plan holder on a day (UTC)."""
    if database.pool is None:
        _ai_usage[(account_id, day)] = _ai_usage.get((account_id, day), 0) + 1
        return
    await database.pool.execute(
        """INSERT INTO dim.ai_usage (account_id, usage_date, question_count) VALUES ($1, $2, 1)
           ON CONFLICT (account_id, usage_date) DO UPDATE SET question_count = dim.ai_usage.question_count + 1""",
        uuid.UUID(account_id), day)


async def delete_integration(account_id: str, connector: str) -> bool:
    """Disconnect: delete the integration row and its saved login. Returns True when one was removed."""
    if database.pool is None:
        return _connections.pop((account_id, connector), None) is not None
    result = await database.pool.execute("DELETE FROM dim.data_connections WHERE account_id = $1 AND connector = $2",
                                         uuid.UUID(account_id), connector)
    return result.endswith(" 1")


# ── Dashboard tabs and the question log (api/services/dashboards.py) ──────────

_tabs: dict[str, dict] = {}            # tab id -> dashboard_tabs row (memory path)
_questions: list[dict] = []            # question_log rows (memory path)


def _tab_out(row) -> dict:
    """A dashboard_tabs row as a dict, with questions as a Python list."""
    out = dict(row)
    if isinstance(out.get("questions"), str):
        out["questions"] = json.loads(out["questions"])
    return out


async def list_dashboard_tabs(account_id: str) -> list[dict]:
    """A client's dashboard tabs, left to right."""
    if database.pool is None:
        return sorted((dict(t) for t in _tabs.values() if t["account_id"] == account_id),
                      key=lambda t: (t["position"], t["created_at"]))
    rows = await database.pool.fetch(
        "SELECT * FROM dim.dashboard_tabs WHERE account_id = $1 ORDER BY position, created_at", uuid.UUID(account_id))
    return [_tab_out(r) for r in rows]


async def save_dashboard_tab(*, account_id: str, name: str, questions: list[str], tab_id: str | None = None,
                             created_by: str | None = None) -> dict | None:
    """
    Create a dashboard tab (no tab_id) or rename / change its questions.

    Returns:
        dict | None: the saved row; None when tab_id isn't this account's.
    """
    if database.pool is None:
        if tab_id:
            tab = _tabs.get(tab_id)
            if not tab or tab["account_id"] != account_id:
                return None
            tab.update(name=name, questions=list(questions), updated_at=_now())
            return dict(tab)
        tid = str(uuid.uuid4())
        position = len([t for t in _tabs.values() if t["account_id"] == account_id])
        _tabs[tid] = {"id": tid, "account_id": account_id, "name": name, "questions": list(questions),
                      "position": position, "created_by": created_by, "created_at": _now(), "updated_at": _now()}
        return dict(_tabs[tid])
    if tab_id:
        row = await database.pool.fetchrow(
            """UPDATE dim.dashboard_tabs SET name = $3, questions = $4::jsonb, updated_at = now()
               WHERE id = $1 AND account_id = $2 RETURNING *""",
            uuid.UUID(tab_id), uuid.UUID(account_id), name, json.dumps(questions))
        return _tab_out(row) if row else None
    row = await database.pool.fetchrow(
        """INSERT INTO dim.dashboard_tabs (account_id, name, questions, position, created_by)
           VALUES ($1, $2, $3::jsonb,
                   (SELECT count(*) FROM dim.dashboard_tabs WHERE account_id = $1), $4)
           RETURNING *""",
        uuid.UUID(account_id), name, json.dumps(questions), uuid.UUID(created_by) if created_by else None)
    return _tab_out(row)


async def delete_dashboard_tab(account_id: str, tab_id: str) -> bool:
    """Delete one of this account's tabs. Returns True when one was removed."""
    if database.pool is None:
        tab = _tabs.get(tab_id)
        if not tab or tab["account_id"] != account_id:
            return False
        del _tabs[tab_id]
        return True
    result = await database.pool.execute("DELETE FROM dim.dashboard_tabs WHERE id = $1 AND account_id = $2",
                                         uuid.UUID(tab_id), uuid.UUID(account_id))
    return result.endswith(" 1")


QUESTION_LOG_KEEP = 500   # newest questions kept per account; older ones are deleted (storage stays small)


async def log_question(account_id: str, question: str, answered: bool) -> None:
    """
    Remember one question asked of the data (the text only), keeping only the
    newest QUESTION_LOG_KEEP per account — enough to spot the questions asked
    most, without the table growing forever.
    """
    if database.pool is None:
        _questions.append({"account_id": account_id, "question": question, "answered": answered, "asked_at": _now()})
        mine = [q for q in _questions if q["account_id"] == account_id]
        for old in mine[:-QUESTION_LOG_KEEP]:   # list order = oldest first
            _questions.remove(old)
        return
    aid = uuid.UUID(account_id)
    async with database.pool.acquire() as conn, conn.transaction():
        await conn.execute("INSERT INTO dim.question_log (account_id, question, answered) VALUES ($1, $2, $3)",
                           aid, question, answered)
        # Delete everything older than this account's 500th-newest question (ids only go up).
        await conn.execute(
            """DELETE FROM dim.question_log WHERE account_id = $1 AND id < (
                   SELECT id FROM dim.question_log WHERE account_id = $1
                   ORDER BY id DESC OFFSET $2 LIMIT 1)""", aid, QUESTION_LOG_KEEP - 1)


async def frequent_questions(account_id: str, limit: int = 8) -> list[dict]:
    """
    The client's most-asked questions (answered ones, case and spaces ignored), most asked first.

    Returns:
        list[dict]: [{"question", "times"}].
    """
    if database.pool is None:
        counts: dict[str, list] = {}
        for q in _questions:
            if q["account_id"] == account_id and q["answered"]:
                key = " ".join(q["question"].lower().split())
                counts.setdefault(key, [q["question"], 0])[1] += 1
        ranked = sorted(counts.values(), key=lambda v: -v[1])[:limit]
        return [{"question": q, "times": n} for q, n in ranked]
    rows = await database.pool.fetch(
        """SELECT min(question) AS question, count(*)::int AS times FROM dim.question_log
           WHERE account_id = $1 AND answered
           GROUP BY lower(regexp_replace(question, '\\s+', ' ', 'g'))
           ORDER BY times DESC, max(asked_at) DESC LIMIT $2""", uuid.UUID(account_id), limit)
    return [dict(r) for r in rows]


# ── Signed-in devices (dim.sessions, api/services/sessions.py) ─────────────────

_sessions: dict[str, dict] = {}   # session id -> dim.sessions row (memory path)


async def create_session(account_id: str, device_id: str | None, device: str) -> str:
    """
    Open a session for one device; returns its id (it goes into the login token as "sid").

    Args:
        account_id: str, the account.
        device_id: str | None, the browser's random device id.
        device: str, the short label ("Chrome on Windows").

    Returns:
        str: the new session id.
    """
    if database.pool is None:
        sid = str(uuid.uuid4())
        _sessions[sid] = {"id": sid, "account_id": account_id, "device_id": device_id, "device": device,
                          "created_at": _now(), "last_seen_at": _now(), "revoked_at": None, "revoked_reason": None}
        return sid
    return str(await database.pool.fetchval(
        "INSERT INTO dim.sessions (account_id, device_id, device) VALUES ($1, $2, $3) RETURNING id",
        uuid.UUID(account_id), device_id, device))


async def get_session(session_id: str) -> dict | None:
    """One session row, or None (unknown or malformed id)."""
    if database.pool is None:
        return _sessions.get(session_id)
    try:
        sid = uuid.UUID(session_id)
    except ValueError:
        return None
    row = await database.pool.fetchrow("SELECT * FROM dim.sessions WHERE id = $1", sid)
    return dict(row) if row else None


async def list_sessions(account_id: str) -> list[dict]:
    """Every session of an account (signed out ones too), oldest first."""
    if database.pool is None:
        return sorted((s for s in _sessions.values() if s["account_id"] == account_id), key=lambda s: s["created_at"])
    rows = await database.pool.fetch("SELECT * FROM dim.sessions WHERE account_id = $1 ORDER BY created_at",
                                     uuid.UUID(account_id))
    return [dict(r) for r in rows]


async def touch_session(session_id: str, label: str | None = None) -> None:
    """Mark a session as just used (and refresh its device label when given)."""
    if database.pool is None:
        if session_id in _sessions:
            _sessions[session_id]["last_seen_at"] = _now()
            if label:
                _sessions[session_id]["device"] = label
        return
    await database.pool.execute(
        "UPDATE dim.sessions SET last_seen_at = now(), device = coalesce($2, device) WHERE id = $1",
        uuid.UUID(session_id), label)


async def revoke_session(session_id: str, reason: str) -> None:
    """Sign a session out (reason: signed_out, device_limit, password_reset, everywhere, admin, idle)."""
    if database.pool is None:
        if session_id in _sessions and _sessions[session_id]["revoked_at"] is None:
            _sessions[session_id].update(revoked_at=_now(), revoked_reason=reason)
        return
    await database.pool.execute(
        "UPDATE dim.sessions SET revoked_at = now(), revoked_reason = $2 WHERE id = $1 AND revoked_at IS NULL",
        uuid.UUID(session_id), reason)


# ── Two-step sign-in (api/services/mfa.py) and the healthcare flag ─────────────

async def set_mfa(account_id: str, *, secret: str | None, enabled: bool, recovery_hashes: list[str] | None) -> dict | None:
    """
    Save two-step sign-in: the ENCRYPTED authenticator key, whether it's on, and the backup-code hashes.

    Args:
        account_id: str, the account.
        secret: str | None, the encrypted key ("enc1:..."); None clears it.
        enabled: bool, True once the person proved their app works.
        recovery_hashes: list[str] | None, sha256 hashes of unused backup codes (None = keep).

    Returns:
        dict | None: the updated accounts row.
    """
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc:
            acc.update(mfa_secret=secret, mfa_enabled_at=_now() if enabled else None)
            if recovery_hashes is not None:
                acc["mfa_recovery_codes"] = list(recovery_hashes)
        return acc
    row = await database.pool.fetchrow(
        """UPDATE dim.accounts SET mfa_secret = $2, mfa_enabled_at = CASE WHEN $3 THEN coalesce(mfa_enabled_at, now()) END,
                  mfa_recovery_codes = coalesce($4::jsonb, mfa_recovery_codes)
           WHERE id = $1 RETURNING *""",
        uuid.UUID(account_id), secret, enabled, json.dumps(recovery_hashes) if recovery_hashes is not None else None)
    return dict(row) if row else None


async def use_recovery_code(account_id: str, code_hash: str) -> bool:
    """
    Spend one backup code (each works once). True if it was there.

    Args:
        account_id: str, the account.
        code_hash: str, sha256 of the code typed.

    Returns:
        bool: True when the code was valid (and is now used up).
    """
    if database.pool is None:
        acc = _accounts.get(account_id) or {}
        codes = list(acc.get("mfa_recovery_codes") or [])
        if code_hash not in codes:
            return False
        codes.remove(code_hash)
        acc["mfa_recovery_codes"] = codes
        return True
    done = await database.pool.execute(
        """UPDATE dim.accounts SET mfa_recovery_codes = mfa_recovery_codes - $2
           WHERE id = $1 AND mfa_recovery_codes ? $2""", uuid.UUID(account_id), code_hash)
    return done.endswith(" 1")


async def set_healthcare(account_id: str, is_healthcare: bool) -> dict | None:
    """Mark a plan holder as a healthcare client (their data access is audited) or not."""
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc:
            acc.update(is_healthcare=bool(is_healthcare), updated_at=_now())
        return acc
    row = await database.pool.fetchrow("UPDATE dim.accounts SET is_healthcare = $2 WHERE id = $1 RETURNING *",
                                       uuid.UUID(account_id), bool(is_healthcare))
    return dict(row) if row else None


# ── Audit log (dim.audit_log, healthcare clients only — api/services/audit.py) ──

_audit: list[dict] = []   # audit_log rows (memory path)


async def add_audit(account_id: str, actor_account_id: str | None, actor_role: str, action: str, detail: dict) -> None:
    """Write one audit row (the caller decides whether this account is audited)."""
    if database.pool is None:
        _audit.append({"id": len(_audit) + 1, "at": _now(), "account_id": account_id,
                       "actor_account_id": actor_account_id, "actor_role": actor_role, "action": action,
                       "detail": dict(detail)})
        return
    await database.pool.execute(
        """INSERT INTO dim.audit_log (account_id, actor_account_id, actor_role, action, detail)
           VALUES ($1, $2, $3, $4, $5::jsonb)""",
        uuid.UUID(account_id), uuid.UUID(actor_account_id) if actor_account_id else None, actor_role, action,
        json.dumps(detail, default=str))


async def list_audit(account_id: str, limit: int = 200) -> list[dict]:
    """An account's audit rows, newest first, with who did each (name and email)."""
    limit = max(1, min(limit, 5000))
    if database.pool is None:
        rows = sorted((r for r in _audit if r["account_id"] == account_id), key=lambda r: r["id"], reverse=True)[:limit]
        out = []
        for r in rows:
            actor = _accounts.get(r["actor_account_id"] or "")
            out.append({**r, "actor_name": f"{actor['first_name']} {actor['last_name']}" if actor else None,
                        "actor_email": actor["email"] if actor else None})
        return out
    rows = await database.pool.fetch(
        """SELECT l.*, a.first_name || ' ' || a.last_name AS actor_name, a.email AS actor_email
           FROM dim.audit_log l LEFT JOIN dim.accounts a ON a.id = l.actor_account_id
           WHERE l.account_id = $1 ORDER BY l.at DESC, l.id DESC LIMIT $2""", uuid.UUID(account_id), limit)
    return [{**dict(r), "detail": _json_dict(r["detail"])} for r in rows]


def _json_dict(value) -> dict:
    """A JSONB value as a dict (asyncpg returns JSON text)."""
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except (TypeError, ValueError):
        return {}


# ── HIPAA requests (api/services/hipaa.py) ───────────────────────────────────

_HIPAA_FIELDS = ("hipaa_status", "hipaa_requested_at", "hipaa_organization", "hipaa_npi", "hipaa_note",
                 "hipaa_decided_at", "hipaa_decided_by", "hipaa_decision_note")


async def set_hipaa(account_id: str, *, status: str, organization: str | None, npi: str | None,
                    note: str | None) -> dict | None:
    """Open (or update) a HIPAA request: status 'pending', the details, now as the request time."""
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc:
            acc.update(hipaa_status=status, hipaa_requested_at=_now(), hipaa_organization=organization,
                       hipaa_npi=npi, hipaa_note=note, hipaa_decided_at=None, hipaa_decided_by=None,
                       hipaa_decision_note=None, updated_at=_now())
        return acc
    row = await database.pool.fetchrow(
        """UPDATE dim.accounts SET hipaa_status = $2, hipaa_requested_at = now(), hipaa_organization = $3,
                  hipaa_npi = $4, hipaa_note = $5, hipaa_decided_at = NULL, hipaa_decided_by = NULL,
                  hipaa_decision_note = NULL
           WHERE id = $1 RETURNING *""", uuid.UUID(account_id), status, organization, npi, note)
    return dict(row) if row else None


async def clear_hipaa(account_id: str) -> dict | None:
    """Withdraw a request: every HIPAA field back to empty (and the audit log off)."""
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc:
            acc.update({f: None for f in _HIPAA_FIELDS}, is_healthcare=False, updated_at=_now())
        return acc
    row = await database.pool.fetchrow(
        "UPDATE dim.accounts SET " + ", ".join(f"{f} = NULL" for f in _HIPAA_FIELDS)
        + ", is_healthcare = false WHERE id = $1 RETURNING *", uuid.UUID(account_id))
    return dict(row) if row else None


async def decide_hipaa(account_id: str, approve: bool, admin_id: str, note: str | None) -> dict | None:
    """Save an admin's HIPAA decision; approval switches the audit log on (is_healthcare)."""
    decision = "approved" if approve else "denied"
    if database.pool is None:
        acc = _accounts.get(account_id)
        if acc:
            acc.update(hipaa_status=decision, hipaa_decided_at=_now(), hipaa_decided_by=admin_id,
                       hipaa_decision_note=note, is_healthcare=approve, updated_at=_now())
        return acc
    row = await database.pool.fetchrow(
        """UPDATE dim.accounts SET hipaa_status = $2, hipaa_decided_at = now(), hipaa_decided_by = $3,
                  hipaa_decision_note = $4, is_healthcare = $5
           WHERE id = $1 RETURNING *""", uuid.UUID(account_id), decision, uuid.UUID(admin_id), note, approve)
    return dict(row) if row else None


async def list_hipaa_requests() -> list[dict]:
    """Every account with a HIPAA request: waiting first (oldest first), then the newest decisions."""
    if database.pool is None:
        rows = [a for a in _accounts.values() if a.get("hipaa_status")]
    else:
        rows = [dict(r) for r in await database.pool.fetch("SELECT * FROM dim.accounts WHERE hipaa_status IS NOT NULL")]
    waiting = sorted((a for a in rows if a["hipaa_status"] == "pending"), key=lambda a: a["hipaa_requested_at"])
    decided = sorted((a for a in rows if a["hipaa_status"] != "pending"),
                     key=lambda a: a.get("hipaa_decided_at") or a["hipaa_requested_at"], reverse=True)
    return waiting + decided


# ── Why clients cancel (dim.subscriptions.cancel_feedback, from Stripe) ──────

async def set_cancellation(stripe_subscription_id: str, feedback: str | None, comment: str | None,
                           canceled_at: datetime | None) -> None:
    """
    Save why (and when) a Stripe subscription was canceled, from its cancellation_details.

    Args:
        stripe_subscription_id: str, "sub_...".
        feedback: str | None, Stripe's reason code ("too_expensive", ...).
        comment: str | None, what the client typed (kept to 500 characters).
        canceled_at: datetime | None, when they canceled.
    """
    comment = (comment or "").strip()[:500] or None
    if database.pool is None:
        for s in _subscriptions.values():
            if s.get("stripe_subscription_id") == stripe_subscription_id:
                s.update(cancel_feedback=feedback, cancel_comment=comment, canceled_at=canceled_at)
        return
    await database.pool.execute(
        """UPDATE dim.subscriptions SET cancel_feedback = $2, cancel_comment = $3, canceled_at = coalesce($4, canceled_at)
           WHERE stripe_subscription_id = $1""", stripe_subscription_id, feedback, comment, canceled_at)


async def cancellation_summary(days: int = 365) -> dict:
    """
    Why clients cancel (Admin panel): counts by Stripe's reason, and the latest comments (no names).

    Returns:
        dict: {"total", "by_reason": {reason | "none": n}, "comments": [{"at", "reason", "comment"}, ...] (newest 5)}.
    """
    if database.pool is None:
        rows = [s for s in _subscriptions.values() if s.get("canceled_at") or s.get("cancel_feedback")]
    else:
        rows = [dict(r) for r in await database.pool.fetch(
            """SELECT cancel_feedback, cancel_comment, canceled_at FROM dim.subscriptions
               WHERE (canceled_at IS NOT NULL OR cancel_feedback IS NOT NULL)
                 AND coalesce(canceled_at, updated_at) > now() - make_interval(days => $1)""", days)]
    by_reason: dict[str, int] = {}
    for r in rows:
        key = r.get("cancel_feedback") or "none"
        by_reason[key] = by_reason.get(key, 0) + 1
    with_comment = sorted((r for r in rows if r.get("cancel_comment")),
                          key=lambda r: r.get("canceled_at") or _now(), reverse=True)[:5]
    return {"total": len(rows), "by_reason": by_reason,
            "comments": [{"at": r["canceled_at"].isoformat() if r.get("canceled_at") else None,
                          "reason": r.get("cancel_feedback"), "comment": r["cancel_comment"]} for r in with_comment]}
