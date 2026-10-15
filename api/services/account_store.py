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
account's plan, the consultant (if any) assigned to each client, subscriptions
and prepaid consulting hours (kept in sync by Stripe), a usage record per
download (file name, row count, runtime, amount, and a one-way fingerprint of
the result for fair use) for billing, each connector's refresh schedule,
consultations (free and paid calls), storage consents and load-run summaries.
Passwords: only a Supabase user id, or a one-way hash in local mode.
NOT stored here: the client's uploaded data. Files are processed in memory
only. (Clients who consent to managed storage have cleaned rows loaded into
the separate warehouse schema — api/services/warehouse_store.py.)
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
_subscriptions: dict[str, dict] = {}     # subscription id -> subscription
_consulting: list[dict] = []             # prepaid consulting hours
_plans: dict[str, dict] = {}             # plan id -> subscription_plans row (Stripe ids)
_consultations: dict[str, dict] = {}     # consultation id -> consultations row
_consents: list[dict] = []               # storage_consents rows
_load_runs: list[dict] = []              # load_runs rows

_connections: dict[tuple[str, str], dict] = {}   # (account_id, connector) -> data_connections row


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
    row = await database.pool.fetchrow("SELECT * FROM accounts WHERE email = $1", email)
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
    auth_user_id: str | None = None,
    password_hash: str | None = None,
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
        "stripe_customer_id": None,     # set the first time they pay (billing.py)
        # Password (api/services/auth.py): Supabase user id OR local hash.
        "auth_user_id": auth_user_id,
        "password_hash": password_hash,
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
                                         auth_user_id, password_hash)
                   VALUES ($1, $2, $3, $4, $5, $6, NULL, $7, $8, $9, $10, $11, $12, $13,
                           $14, $15, $16, $17, $18, $19, $20, $21, $22, $23)""",
                uuid.UUID(account_id), account_type, first_name, last_name, email,
                company_name, team_role, status, account["last_login_at"],
                industry_supersector, naics_sector, naics_subsector,
                company_size, account["location_geoname_id"], account["location_city"],
                account["location_region"], account["location_country_code"], account["location_label"],
                industry_other, account["wants_consultant"],
                uuid.UUID(account["consultant_id"]) if account["consultant_id"] else None,
                uuid.UUID(auth_user_id) if auth_user_id else None, password_hash,
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
    """
    Record a sign-in time (useful for your consulting analysis: who is active?).

    On the memory path an unknown account id is ignored.

    Args:
        account_id: str, the account that just signed in.

    Returns:
        None. Updates accounts.last_login_at to now.

    Example:
        >>> await touch_last_login(account_id)
    """
    if database.pool is None:
        if account_id in _accounts:
            _accounts[account_id]["last_login_at"] = _now()
        return
    await database.pool.execute(
        "UPDATE accounts SET last_login_at = now() WHERE id = $1", uuid.UUID(account_id)
    )


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
        """UPDATE accounts SET auth_user_id = $2, password_hash = $3, status = 'active'
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
        ['owner', 'member']
    """
    if database.pool is None:
        return [a for a in _accounts.values() if a["team_id"] == team_id]
    rows = await database.pool.fetch(
        "SELECT * FROM accounts WHERE team_id = $1 ORDER BY created_at", uuid.UUID(team_id)
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
    row = await database.pool.fetchrow("SELECT * FROM teams WHERE id = $1", uuid.UUID(team_id))
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
        """INSERT INTO usage_events (account_id, dataset_name, row_count, runtime_seconds,
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
        """SELECT created_at, data_fingerprint, amount_usd FROM usage_events
           WHERE account_id = $1 AND created_at >= $2 ORDER BY created_at DESC""",
        uuid.UUID(account_id), since,
    )
    return [{"created_at": r["created_at"], "data_fingerprint": r["data_fingerprint"],
             "amount_usd": float(r["amount_usd"])} for r in rows]


# ── Services on the plan (table account_services) ───────────────────────────

async def list_services(account_id: str) -> list[str]:
    """
    List the active service ids stored for this account (no defaults added here).

    These are the account_services rows (project interests). Data Cleaning is not
    added automatically; see entitlements.py for that rule.

    Args:
        account_id: str, the plan holder's account id.

    Returns:
        list[str]: service ids with status "active" (database order, not catalog order).

    Example:
        >>> await list_services(account_id)
        ['data_visualization']
    """
    if database.pool is None:
        return [sid for sid, row in _services.get(account_id, {}).items() if row["status"] == "active"]
    rows = await database.pool.fetch(
        "SELECT service_id FROM account_services WHERE account_id = $1 AND status = 'active'",
        uuid.UUID(account_id),
    )
    return [r["service_id"] for r in rows]


async def add_services(account_id: str, service_ids: list[str], source: str) -> None:
    """
    Add (or re-activate) services on an account's plan.

    An id already on the plan is switched back to "active"; on the database path
    its original `source` is kept, on the memory path the row is replaced.

    Args:
        account_id: str, the plan holder's account id.
        service_ids: list[str], service ids to add.
        source: str, where the request came from (e.g. "signup", "added").

    Returns:
        None. Upserts account_services rows.

    Example:
        >>> await add_services(account_id, ["data_visualization"], "added")
    """
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
    row = await database.pool.fetchrow("SELECT * FROM consultants WHERE account_id = $1", uuid.UUID(account_id))
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
    row = await database.pool.fetchrow("SELECT * FROM consultants WHERE id = $1", uuid.UUID(consultant_id))
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
    row = await database.pool.fetchrow("SELECT * FROM consultants WHERE email = $1", email)
    return dict(row) if row else None


async def ensure_consultant(account: dict, *, is_default: bool) -> dict:
    """
    Create (or refresh) the consultant row for a consultant's account.

    If the row already exists only `is_default` is updated. A new row uses the
    account's first name as the display name and starts active.

    Args:
        account: dict, the consultant's accounts row (needs "id", "first_name", "email").
        is_default: bool, True for the default consultant (first CONSULTANT_EMAILS address).

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
        """SELECT * FROM accounts WHERE consultant_id = $1
           ORDER BY lower(coalesce(company_name, first_name))""",
        uuid.UUID(consultant_id),
    )
    return [dict(r) for r in rows]


async def assign_waiting_clients(consultant_id: str) -> int:
    """
    Give clients who asked for a consultant (but have none yet) to this consultant.

    Args:
        consultant_id: str, the consultants row id to assign them to.

    Returns:
        int: how many accounts were assigned (on the database path, read from the
            "UPDATE <n>" status PostgreSQL returns).

    Example:
        >>> await assign_waiting_clients(cid)
        2
    """
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


# ── Payments: plans, subscriptions, consulting hours (see api/services/billing.py) ──

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
    await database.pool.execute("UPDATE accounts SET stripe_customer_id = $2 WHERE id = $1",
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
    row = await database.pool.fetchrow("SELECT * FROM accounts WHERE stripe_customer_id = $1", customer_id)
    return dict(row) if row else None


async def upsert_subscription(*, account_id: str, billing_interval: str, status: str, items: list[dict],
                              stripe_subscription_id: str | None = None, stripe_customer_id: str | None = None,
                              cancel_at_period_end: bool = False, current_period_end: datetime | None = None,
                              subscription_id: str | None = None) -> dict:
    """
    Create or update one subscription and REPLACE its items
    (items = [{"plan_id": ..., "stripe_subscription_item_id": ...}]). Matched by
    our id (test mode) or Stripe's id.

    On the database path, `subscription_id` given -> UPDATE that row; otherwise
    INSERT, or update the existing row with the same `stripe_subscription_id`.
    The row and its items change in ONE transaction (old items are deleted, the
    new ones inserted).

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
    async with database.pool.acquire() as conn:
        async with conn.transaction():      # the row and its items change together
            if subscription_id:
                rec = await conn.fetchrow(
                    """UPDATE subscriptions SET billing_interval = $2, status = $3, cancel_at_period_end = $4,
                              current_period_end = $5, updated_at = now() WHERE id = $1 RETURNING *""",
                    uuid.UUID(subscription_id), billing_interval, status, cancel_at_period_end, current_period_end)
            else:
                rec = await conn.fetchrow(
                    """INSERT INTO subscriptions (account_id, billing_interval, status, stripe_subscription_id,
                                                  stripe_customer_id, cancel_at_period_end, current_period_end)
                       VALUES ($1, $2, $3, $4, $5, $6, $7)
                       ON CONFLICT (stripe_subscription_id) DO UPDATE SET
                           billing_interval = EXCLUDED.billing_interval, status = EXCLUDED.status,
                           cancel_at_period_end = EXCLUDED.cancel_at_period_end,
                           current_period_end = EXCLUDED.current_period_end, updated_at = now()
                       RETURNING *""",
                    uuid.UUID(account_id), billing_interval, status, stripe_subscription_id,
                    stripe_customer_id, cancel_at_period_end, current_period_end)
            await conn.execute("DELETE FROM subscription_items WHERE subscription_id = $1", rec["id"])
            for item in items:
                await conn.execute(
                    """INSERT INTO subscription_items (subscription_id, plan_id, stripe_subscription_item_id)
                       VALUES ($1, $2, $3)""", rec["id"], item["plan_id"], item["stripe_subscription_item_id"])
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
    sql = "SELECT * FROM subscriptions WHERE account_id = $1"
    if live_only:
        sql += " AND status = ANY($2::text[])"
    sql += " ORDER BY created_at DESC"
    args = [uuid.UUID(account_id)] + ([list(LIVE_STATUSES)] if live_only else [])
    subs = [dict(r) for r in await database.pool.fetch(sql, *args)]
    for s in subs:
        rows = await database.pool.fetch(
            "SELECT plan_id, stripe_subscription_item_id FROM subscription_items WHERE subscription_id = $1", s["id"])
        s["items"] = [dict(r) for r in rows]
    return subs


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
        "UPDATE subscriptions SET status = $2, cancel_at_period_end = $3, updated_at = now() WHERE id = $1",
        uuid.UUID(subscription_id), status, cancel_at_period_end,
    )


async def record_consulting_purchase(*, account_id: str, hours: float, rate_per_hour_usd: float,
                                     amount_usd: float, service_id: str | None = None,
                                     stripe_checkout_session_id: str | None = None) -> None:
    """
    Save prepaid consulting hours (once per Stripe Checkout — the webhook may repeat).

    A repeat with the same `stripe_checkout_session_id` is ignored, so Stripe
    re-sending a webhook never doubles the hours. Rows are saved as "paid".

    Args:
        account_id: str, the client account that bought the hours.
        hours: float, number of hours bought.
        rate_per_hour_usd: float, hourly rate at purchase time.
        amount_usd: float, total paid in US dollars.
        service_id: str | None, the service the hours are for, if any.
        stripe_checkout_session_id: str | None, Stripe Checkout id (None in test mode).

    Returns:
        None. Inserts a consulting_purchases row (unless it is a repeat).

    Example:
        >>> await record_consulting_purchase(account_id=aid, hours=2, rate_per_hour_usd=80.0,
        ...                                  amount_usd=160.0)
    """
    row = {"account_id": account_id, "service_id": service_id, "hours": hours, "rate_per_hour_usd": rate_per_hour_usd,
           "amount_usd": amount_usd, "stripe_checkout_session_id": stripe_checkout_session_id,
           "status": "paid", "created_at": _now()}
    if database.pool is None:
        if stripe_checkout_session_id and any(c["stripe_checkout_session_id"] == stripe_checkout_session_id
                                              for c in _consulting):
            return
        _consulting.append(row)
        return
    await database.pool.execute(
        """INSERT INTO consulting_purchases (account_id, service_id, hours, rate_per_hour_usd, amount_usd,
                                             stripe_checkout_session_id)
           VALUES ($1, $2, $3, $4, $5, $6) ON CONFLICT (stripe_checkout_session_id) DO NOTHING""",
        uuid.UUID(account_id), service_id, hours, rate_per_hour_usd, amount_usd, stripe_checkout_session_id,
    )


async def consulting_hours(account_id: str) -> float:
    """
    Return the total prepaid consulting hours bought by this account.

    Only purchases with status "paid" count.

    Args:
        account_id: str, the client account id.

    Returns:
        float: total hours (0.0 if none).

    Example:
        >>> await consulting_hours(aid)
        2.0
    """
    if database.pool is None:
        return float(sum(c["hours"] for c in _consulting if c["account_id"] == account_id and c["status"] == "paid"))
    value = await database.pool.fetchval(
        "SELECT coalesce(sum(hours), 0) FROM consulting_purchases WHERE account_id = $1 AND status = 'paid'",
        uuid.UUID(account_id),
    )
    return float(value)


async def save_plan(plan: dict) -> None:
    """
    Upsert one subscription_plans row (scripts/sync_stripe.py).

    Unlike insert_missing_plans, this overwrites an existing row, including its
    Stripe product and price ids, and marks it active.

    Args:
        plan: dict, with "id", "name", "description", "service_ids",
            "monthly_price_usd", "annual_price_usd" and optionally
            "stripe_product_id", "stripe_monthly_price_id", "stripe_annual_price_id",
            "sort_order" (default 0), "kind" (default "tool"), "is_base", "requires".

    Returns:
        None.

    Example:
        >>> await save_plan({"id": "ai", "name": "AI", "description": "...", "service_ids": ["ai"],
        ...                  "monthly_price_usd": 29.0, "annual_price_usd": 290.0,
        ...                  "stripe_product_id": "prod_..."})
    """
    if database.pool is None:
        _plans[plan["id"]] = dict(plan)
        return
    await database.pool.execute(
        """INSERT INTO subscription_plans (id, name, description, service_ids, monthly_price_usd, annual_price_usd,
                                           stripe_product_id, stripe_monthly_price_id, stripe_annual_price_id,
                                           active, sort_order, kind, is_base, requires, updated_at)
           VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, true, $10, $11, $12, $13, now())
           ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description,
               kind = EXCLUDED.kind, is_base = EXCLUDED.is_base, requires = EXCLUDED.requires,
               service_ids = EXCLUDED.service_ids, monthly_price_usd = EXCLUDED.monthly_price_usd,
               annual_price_usd = EXCLUDED.annual_price_usd, stripe_product_id = EXCLUDED.stripe_product_id,
               stripe_monthly_price_id = EXCLUDED.stripe_monthly_price_id,
               stripe_annual_price_id = EXCLUDED.stripe_annual_price_id, active = true,
               sort_order = EXCLUDED.sort_order, updated_at = now()""",
        plan["id"], plan["name"], plan["description"], list(plan["service_ids"]), plan["monthly_price_usd"],
        plan["annual_price_usd"], plan.get("stripe_product_id"), plan.get("stripe_monthly_price_id"),
        plan.get("stripe_annual_price_id"), plan.get("sort_order", 0), plan.get("kind", "tool"),
        bool(plan.get("is_base")), list(plan.get("requires", [])),
    )


async def insert_missing_plans(plans: list[dict]) -> None:
    """
    Add plan rows that don't exist yet; existing rows (and their Stripe ids) are left alone.

    Args:
        plans: list[dict], one dict per plan with "id", "name", "description",
            "service_ids", "monthly_price_usd", "annual_price_usd", "sort_order",
            and optionally "kind", "is_base", "requires" (see billing.ensure_plans).

    Returns:
        None.

    Example:
        >>> await insert_missing_plans([{"id": "ai", "name": "AI", "description": "...",
        ...                              "service_ids": ["ai"], "monthly_price_usd": 29.0,
        ...                              "annual_price_usd": 290.0, "sort_order": 6}])
    """
    if database.pool is None:
        for plan in plans:
            _plans.setdefault(plan["id"], dict(plan))
        return
    for plan in plans:
        await database.pool.execute(
            """INSERT INTO subscription_plans (id, name, description, service_ids, monthly_price_usd,
                                               annual_price_usd, sort_order, kind, is_base, requires)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10) ON CONFLICT (id) DO NOTHING""",
            plan["id"], plan["name"], plan["description"], plan["service_ids"], plan["monthly_price_usd"],
            plan["annual_price_usd"], plan["sort_order"], plan.get("kind", "tool"), bool(plan.get("is_base")),
            list(plan.get("requires", [])),
        )


# ── Consultations (table consultations; see api/services/consultations.py) ──

_CONSULTATION_FIELDS = ("account_id", "attendee_email", "attendee_name", "kind", "status", "source",
                        "calcom_booking_uid", "starts_at", "ends_at", "confirmed_by", "confirmed_at", "note")
_UUID_FIELDS = {"account_id", "confirmed_by"}   # stored as UUID in Postgres


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
            f"UPDATE consultations SET {sets}, updated_at = now() WHERE id = $1 RETURNING *",
            uuid.UUID(str(row["id"])), *(_db_value(k, v) for k, v in fields.items()),
        )
    else:
        cols = ", ".join(fields)
        marks = ", ".join(f"${i + 1}" for i in range(len(fields)))
        rec = await database.pool.fetchrow(
            f"INSERT INTO consultations ({cols}) VALUES ({marks}) RETURNING *",
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
    row = await database.pool.fetchrow("SELECT * FROM consultations WHERE id = $1", key)
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
    row = await database.pool.fetchrow("SELECT * FROM consultations WHERE calcom_booking_uid = $1", uid)
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
        """SELECT * FROM consultations
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
        """SELECT * FROM storage_consents WHERE account_id = $1 AND withdrawn_at IS NULL
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
        """INSERT INTO storage_consents (account_id, terms_version, consented_by)
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
        "UPDATE storage_consents SET withdrawn_at = now() WHERE account_id = $1 AND withdrawn_at IS NULL",
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
    allowed = ("account_id", "source", "source_name", "event_type", "rows_in", "rows_loaded", "rows_skipped",
               "plan_fingerprint", "status", "error", "started_by", "finished_at")
    fields = {k: v for k, v in row.items() if k in allowed}
    uuid_fields = {"account_id", "started_by"}
    if database.pool is None:
        if row.get("id"):
            existing = next(r for r in _load_runs if r["id"] == row["id"])
            existing.update(fields)
            return existing
        new = {"source": "file_upload", "rows_in": 0, "rows_loaded": 0, "rows_skipped": 0, "error": None,
               "plan_fingerprint": None, "started_by": None, "finished_at": None,
               **fields, "id": len(_load_runs) + 1, "started_at": _now()}
        _load_runs.append(new)
        return new
    values = [uuid.UUID(str(v)) if k in uuid_fields and v else v for k, v in fields.items()]
    if row.get("id"):
        sets = ", ".join(f"{k} = ${i + 2}" for i, k in enumerate(fields))
        rec = await database.pool.fetchrow(f"UPDATE load_runs SET {sets} WHERE id = $1 RETURNING *",
                                           int(row["id"]), *values)
    else:
        cols = ", ".join(fields)
        marks = ", ".join(f"${i + 1}" for i in range(len(fields)))
        rec = await database.pool.fetchrow(f"INSERT INTO load_runs ({cols}) VALUES ({marks}) RETURNING *", *values)
    return dict(rec)


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
        "SELECT * FROM load_runs WHERE account_id = $1 ORDER BY started_at DESC LIMIT $2",
        uuid.UUID(account_id), limit,
    )
    return [dict(r) for r in rows]

# ── Data connections (table data_connections) ───────────────────────────────

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
        "SELECT * FROM data_connections WHERE account_id = $1 ORDER BY connector", uuid.UUID(account_id))
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
        """INSERT INTO data_connections (account_id, consultant_id, connector, refresh_frequency, next_sync_at)
           VALUES ($1, $2, $3, $4, $5)
           ON CONFLICT (account_id, connector) DO UPDATE
               SET refresh_frequency = EXCLUDED.refresh_frequency,
                   next_sync_at = EXCLUDED.next_sync_at, updated_at = now()
           RETURNING *""",
        uuid.UUID(account_id), uuid.UUID(consultant_id) if consultant_id else None, connector,
        refresh_frequency, next_sync_at,
    )
    return dict(row)
