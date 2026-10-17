"""
Integrations (connected data sources) and how often they refresh.

Two KINDS, shown as two sections in Settings -> Integrations:

  * BUSINESS TOOLS (QuickBooks, Xero) are connected with a "Connect" sign-in
    (api/services/oauth.py). Connected, they give a free LIVE quick look and
    can be opened in the workspace — read when asked, never stored
    (api/services/business_tools.py). Storing them on a schedule (history,
    trends, combining sources) is the Data Engineering sync, coming next.
  * CLOUD STORAGE (a client's own Supabase / PostgreSQL database first) is
    READ WHERE IT LIVES, never copied: the client already manages it. It
    feeds analysis, dashboards, ML and AI; storing is switched off for it.
    The reading itself is api/services/cloud_sources.py.

Business tools (see docs/CONNECTORS.md):
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

from api.services import oauth
from api.services.business_tools import daily_budget
from api.services.pricing import DEFAULT_REFRESH, REFRESH_OPTIONS_BY_ID

# The integrations. "sign_in": connected with a "Connect" button (OAuth) —
# offered only once the platform's keys for it are set (oauth.configured), so
# until then it shows as coming soon. "live": has the free quick look.
# "stores_data": has a refresh schedule for the (coming) stored sync.
CONNECTORS: dict[str, dict] = {
    "quickbooks": {"name": "QuickBooks Online", "kind": "business_tool", "available": False, "stores_data": True,
                   "sign_in": True, "live": True,
                   "about": "Income, expenses, what customers owe you, top customers — live from QuickBooks, nothing "
                            "stored. Storing it on your schedule (for history and trends) comes with Data Engineering."},
    "xero": {"name": "Xero", "kind": "business_tool", "available": False, "stores_data": True,
             "sign_in": True, "live": True,
             "about": "Income, expenses, what customers owe you, top customers — live from Xero, nothing stored. "
                      "Storing it on your schedule (for history and trends) comes with Data Engineering."},
    "wave": {"name": "Wave", "kind": "business_tool", "available": False, "stores_data": True,
             "sign_in": True, "live": True,
             "about": "Sales, what customers owe you, top customers — live from Wave invoices, nothing stored "
                      "(Wave's API doesn't share expenses). Needs Wave Pro. Storing it on your schedule comes with "
                      "Data Engineering."},
    "supabase": {"name": "Supabase / PostgreSQL database", "kind": "cloud_storage", "available": True, "stores_data": False,
                 "sign_in": False, "live": False,
                 "about": "Your own Supabase database — or any PostgreSQL, such as Amazon RDS / Aurora PostgreSQL, "
                          "Google Cloud SQL, Azure Database for PostgreSQL or Neon — read where it lives. Nothing is copied."},
}
KINDS = {"cloud_storage": "Cloud storage", "business_tool": "Business tools"}

# What's on the way, shown under each section of Settings -> Integrations so
# clients know their tool is planned. NOT connectors: nothing can be set up
# for them yet, and they never get a dim.data_connections row. "when":
#   "soon"  = next in line (the most-asked tools, with well-documented APIs)
#   "later" = planned after those
# Cloud storage stays read-where-it-lives; business tools are copied into
# managed storage like QuickBooks (docs/CONNECTORS.md). Keep the names in step
# with the questionnaire's options (api/services/intake.py DATA_LOCATIONS).
# When one is built, move it into CONNECTORS and delete it here.
COMING_SOON: list[dict] = [
    {"name": "Snowflake", "kind": "cloud_storage", "when": "soon"},
    {"name": "Google BigQuery", "kind": "cloud_storage", "when": "soon"},
    {"name": "Amazon Redshift", "kind": "cloud_storage", "when": "soon"},
    {"name": "MySQL (incl. Amazon RDS / Aurora MySQL)", "kind": "cloud_storage", "when": "soon"},
    {"name": "Microsoft SQL Server / Azure SQL", "kind": "cloud_storage", "when": "soon"},
    {"name": "Databricks", "kind": "cloud_storage", "when": "later"},
    {"name": "Microsoft Fabric / Azure Synapse", "kind": "cloud_storage", "when": "later"},
    {"name": "Amazon S3 (files)", "kind": "cloud_storage", "when": "later"},
    {"name": "Google Drive / Sheets", "kind": "cloud_storage", "when": "later"},
    {"name": "Square", "kind": "business_tool", "when": "soon"},
    {"name": "Shopify", "kind": "business_tool", "when": "soon"},
    {"name": "Stripe", "kind": "business_tool", "when": "soon"},
    {"name": "Gusto", "kind": "business_tool", "when": "later"},
    {"name": "FreshBooks", "kind": "business_tool", "when": "later"},
    {"name": "Clover", "kind": "business_tool", "when": "later"},
    {"name": "Toast", "kind": "business_tool", "when": "later"},
    {"name": "HubSpot", "kind": "business_tool", "when": "later"},
    {"name": "Salesforce", "kind": "business_tool", "when": "later"},
]

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
    if connector not in CONNECTORS or not CONNECTORS[connector]["stores_data"]:
        raise ValueError(f"Unknown connector: {connector}")   # cloud sources have no schedule
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
        list[dict]: [{"connector", "name", "kind", "about", "available", "stores_data",
        "sign_in" (a "Connect" button is offered), "live" (has the quick look),
        "calls_today" (API calls made to it today, UTC), "calls_budget" (the most we
        make to it in a day — business_tools.daily_budget; None when unlimited),
        "refresh_frequency", "status", "display_name", "last_synced_at", "next_sync_at"}, ...]
        (datetimes as ISO strings or None). Saved logins are never included.

    Example:
        >>> connector_list([])[0]["refresh_frequency"], connector_list([])[0]["status"]
        ('daily', 'not_set_up')
    """
    saved = {c["connector"]: c for c in connections}
    today = datetime.now(timezone.utc).date()
    out = []
    for cid, meta in CONNECTORS.items():
        c = saved.get(cid, {})
        iso = lambda v: v.isoformat() if isinstance(v, datetime) else v   # noqa: E731
        # Sign-in tools are available once the platform's keys are set; Supabase can
        # ALSO offer "Sign in with Supabase" (optional keys) next to pasting a connection string.
        keys = oauth.configured(cid)
        out.append({"connector": cid, "name": meta["name"], "kind": meta["kind"], "about": meta["about"],
                    "available": meta["available"] or keys, "stores_data": meta["stores_data"],
                    "sign_in": keys, "live": meta["live"],
                    "calls_today": c.get("api_calls_count", 0) if c.get("api_calls_day") == today else 0,
                    "calls_budget": daily_budget(cid),
                    "refresh_frequency": c.get("refresh_frequency", DEFAULT_REFRESH),
                    "status": c.get("status", "not_set_up"), "display_name": c.get("display_name"),
                    "last_synced_at": iso(c.get("last_synced_at")), "next_sync_at": iso(c.get("next_sync_at"))})
    return out
