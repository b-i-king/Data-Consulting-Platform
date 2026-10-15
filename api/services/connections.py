"""
Connected data sources (connectors) and how often they refresh.

A connector (QuickBooks first; more later — see docs/CONNECTORS.md) copies a
client's data into managed storage, so it needs the Data Engineering tool.
Each pull costs API calls and compute, so the client picks HOW OFTEN their
data updates, and the faster choices are paid add-ons:

    manual   only when they click "Refresh now"            included
    weekly   once a week                                    included
    daily    every night (the DEFAULT)                      included
    hourly   every hour                                     add-on
    live     about every 15 minutes                         add-on

The ids, labels and prices live in api/services/pricing.py (REFRESH_OPTIONS).

ANTI-GAMING: "Refresh now" is allowed only after a gap that matches the
chosen frequency (e.g. 4 hours on "daily"), so nobody gets hourly data for
the price of daily by clicking the button all day.

This module is pure rules (no database, no HTTP). Storage is
api/services/account_store.py (table data_connections); endpoints are
api/routers/connections.py.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from api.services.pricing import DEFAULT_REFRESH, REFRESH_OPTIONS_BY_ID

# Connectors a client can set a schedule for. "available" False = shown as
# coming soon: the schedule can be chosen now and starts once it's connected.
CONNECTORS: dict[str, dict] = {
    "quickbooks": {"name": "QuickBooks Online", "available": False},
}

# The tool a client needs before any connector can store data for them.
REQUIRED_SERVICE = "data_engineering"


def can_connect(active_services: list[str]) -> bool:
    """
    Tell whether a client may set up connectors (they need Data Engineering).

    Args:
        active_services: list[str], the service ids the client can use
            (entitlements.active_services).

    Returns:
        bool: True when "data_engineering" is among them.

    Example:
        >>> can_connect(["data_cleaning", "data_engineering"]), can_connect(["data_cleaning"])
        (True, False)
    """
    return REQUIRED_SERVICE in active_services


def validate(connector: str, frequency: str) -> None:
    """
    Check a connector id and a refresh frequency id, raising on anything unknown.

    Args:
        connector: str, e.g. "quickbooks" (a key of CONNECTORS).
        frequency: str, e.g. "daily" (an id in pricing.REFRESH_OPTIONS).

    Returns:
        None. Returns quietly when both are valid.

    Raises:
        ValueError: "Unknown connector: ..." or "Unknown refresh frequency: ...".

    Example:
        >>> validate("quickbooks", "daily")      # fine
        >>> validate("quickbooks", "every_second")
        Traceback (most recent call last):
        ValueError: Unknown refresh frequency: every_second
    """
    if connector not in CONNECTORS:
        raise ValueError(f"Unknown connector: {connector}")
    if frequency not in REFRESH_OPTIONS_BY_ID:
        raise ValueError(f"Unknown refresh frequency: {frequency}")


def next_sync_at(frequency: str, last_synced_at: datetime | None, now: datetime | None = None) -> datetime | None:
    """
    Work out when the scheduler should pull this connection next.

    - "manual" never runs on a schedule -> None.
    - Never synced yet -> right away (`now`).
    - Otherwise last sync + the frequency's interval, but never in the past
      (a connection that missed its slot runs at `now`, not in a burst).

    Args:
        frequency: str, a refresh option id ("manual", "weekly", "daily", "hourly", "live").
        last_synced_at: datetime | None, last successful pull (timezone-aware), or None.
        now: datetime | None, current UTC time; tests pass a fixed value.

    Returns:
        datetime | None: the next pull time (UTC), or None for "manual".

    Example:
        >>> t = datetime(2026, 10, 15, 2, 0, tzinfo=timezone.utc)
        >>> next_sync_at("daily", t, now=t + timedelta(hours=1))
        datetime.datetime(2026, 10, 16, 2, 0, tzinfo=datetime.timezone.utc)
    """
    now = now or datetime.now(timezone.utc)
    interval = REFRESH_OPTIONS_BY_ID[frequency].interval_minutes
    if interval is None:
        return None
    if last_synced_at is None:
        return now
    return max(last_synced_at + timedelta(minutes=interval), now)


def refresh_now_allowed(frequency: str, last_synced_at: datetime | None,
                        now: datetime | None = None) -> tuple[bool, datetime | None]:
    """
    Decide whether the client's "Refresh now" button may run a pull right now.

    Allowed when the connection has never synced, or when at least the option's
    refresh_now_gap_minutes have passed since the last pull (manual 1 hour,
    weekly 24 hours, daily 4 hours, hourly 15 minutes, live 5 minutes).

    Args:
        frequency: str, the connection's refresh option id.
        last_synced_at: datetime | None, last successful pull (timezone-aware), or None.
        now: datetime | None, current UTC time; tests pass a fixed value.

    Returns:
        tuple[bool, datetime | None]: (allowed, earliest time it will be allowed;
        None when allowed now).

    Example:
        >>> t = datetime(2026, 10, 15, 9, 0, tzinfo=timezone.utc)
        >>> refresh_now_allowed("daily", t, now=t + timedelta(hours=1))
        (False, datetime.datetime(2026, 10, 15, 13, 0, tzinfo=datetime.timezone.utc))
    """
    now = now or datetime.now(timezone.utc)
    if last_synced_at is None:
        return True, None
    ready_at = last_synced_at + timedelta(minutes=REFRESH_OPTIONS_BY_ID[frequency].refresh_now_gap_minutes)
    return (True, None) if now >= ready_at else (False, ready_at)


def addon_total(connections: list[dict]) -> float:
    """
    Add up the monthly add-on price of a client's connections (billable options only).

    Options that are included (manual, weekly, daily) cost $0; hourly and live
    count only once their option is billable (connectors launched).

    Args:
        connections: list[dict], data_connections rows with "refresh_frequency".

    Returns:
        float: US dollars per month, rounded to cents.

    Example:
        >>> addon_total([{"refresh_frequency": "daily"}, {"refresh_frequency": "hourly"}])   # not billable yet
        0.0
    """
    total = 0.0
    for c in connections:
        opt = REFRESH_OPTIONS_BY_ID.get(c.get("refresh_frequency", DEFAULT_REFRESH))
        if opt and opt.billable:
            total += opt.monthly_usd
    return round(total, 2)


def connector_list(connections: list[dict]) -> list[dict]:
    """
    Merge the supported connectors with the client's saved settings, for the web page.

    Every connector in CONNECTORS appears once; when the client hasn't chosen a
    schedule yet it shows the default ("daily") with status "not_set_up".

    Args:
        connections: list[dict], the client's data_connections rows.

    Returns:
        list[dict]: [{"connector", "name", "available", "refresh_frequency", "status",
        "last_synced_at", "next_sync_at"}, ...] (datetimes as ISO strings or None).

    Example:
        >>> connector_list([])[0]["refresh_frequency"], connector_list([])[0]["status"]
        ('daily', 'not_set_up')
    """
    saved = {c["connector"]: c for c in connections}
    out = []
    for cid, meta in CONNECTORS.items():
        c = saved.get(cid, {})
        iso = lambda v: v.isoformat() if isinstance(v, datetime) else v   # noqa: E731
        out.append({"connector": cid, "name": meta["name"], "available": meta["available"],
                    "refresh_frequency": c.get("refresh_frequency", DEFAULT_REFRESH),
                    "status": c.get("status", "not_set_up"),
                    "last_synced_at": iso(c.get("last_synced_at")), "next_sync_at": iso(c.get("next_sync_at"))})
    return out
