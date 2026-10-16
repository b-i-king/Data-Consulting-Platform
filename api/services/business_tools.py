"""
Business tools, read LIVE: QuickBooks Online and Xero — never stored.

WHY THIS EXISTS
---------------
The "quick look" middle option: a client connects QuickBooks or Xero (sign-in
in api/services/oauth.py) and, WITHOUT anything being copied into our
database, gets

  * a QUICK LOOK — income, expenses, net income, what customers owe them,
    what they owe, month by month, and their top customers — for any dates;
  * their records (invoices, payments, expenses...) opened in the workspace
    like a file, for dashboards, ML and AI.

Everything is fetched when they ask, used for that one request, and let go
(the same rule as cloud databases). Storing it — for history, trends over
time and combining with other sources — is the Data Engineering sync
(docs/CONNECTORS.md), built separately.

WHERE THE NUMBERS COME FROM
---------------------------
  QuickBooks: its own reports — Profit and Loss (by month), Sales by Customer,
              A/R and A/P Aging. The same numbers the client sees in QuickBooks.
  Xero:       Xero keeps its Profit & Loss report for App Partners, so we add
              up sales invoices + money received (income) and bills + money
              spent (expenses), excluding tax, drafts, voided items and
              transfers. Close to Xero's accrual P&L, but an ESTIMATE — the page
              says so.

THE TOOLS' LIMITS (free plans — going over gets the app blocked)
----------------------------------------------------------------
  * Connections: the router refuses a connection past XERO_MAX_CONNECTIONS
    (5) / QUICKBOOKS_MAX_CONNECTIONS (15); extra Xero organizations shared in
    one sign-in are disconnected at once.
  * Calls: every call to a client's company goes through a CallMeter (below):
    a daily budget per company, QuickBooks' monthly budget for the whole app,
    and a per-minute pace.

KEEPING COSTS DOWN
------------------
  * Empty record types are found with one cheap call each (QuickBooks
    COUNT(*), Xero a one-item page) and hidden; opening one is refused.
  * Xero invoices are fetched with summaryOnly=true (no line items).
  * At most LIVE_MAX_ROWS records per open, and no more than the upload
    size limit as CSV (the workspace opens it like an upload).
"""

from __future__ import annotations

import asyncio
import re
import time
from collections import Counter, defaultdict, deque
from datetime import date, datetime, timedelta, timezone

import pandas as pd
from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import oauth
from api.services.cloud_sources import csv_within
from api.services.unified import quickbooks_response_to_unified, unified_to_wide

LIVE_MAX_ROWS = 20_000      # records per open (provider APIs are paged and rate limited)
QUICK_LOOK_DAYS = 365       # the free quick look covers the last 12 months; longer history = the stored sync
QB_PAGE = 1000              # QuickBooks' largest page
XERO_PAGE = 1000            # Xero's largest page (pageSize)
QB_MINOR_VERSION = "75"     # QuickBooks API version (older ones are retired)
TOP_CUSTOMERS = 10

# What can be opened in the workspace, per tool. QuickBooks ids are entity
# names (flattened by api/services/unified.py into one row per line, with the
# platform's unified column names); Xero ids map to an endpoint + filter.
DATASETS: dict[str, list[dict]] = {
    "quickbooks": [
        {"id": "Invoice", "name": "Invoices (what customers were billed)"},
        {"id": "SalesReceipt", "name": "Sales receipts (paid on the spot)"},
        {"id": "Payment", "name": "Customer payments"},
        {"id": "Purchase", "name": "Expenses (checks, card and cash purchases)"},
        {"id": "Bill", "name": "Bills from vendors"},
    ],
    "xero": [
        {"id": "sales_invoices", "name": "Sales invoices", "path": "Invoices", "key": "Invoices",
         "where": 'Type=="ACCREC"', "summary": True},
        {"id": "bills", "name": "Bills from suppliers", "path": "Invoices", "key": "Invoices",
         "where": 'Type=="ACCPAY"', "summary": True},
        {"id": "bank_transactions", "name": "Bank transactions (money in and out)", "path": "BankTransactions",
         "key": "BankTransactions", "where": None, "summary": False},
        {"id": "payments", "name": "Payments on invoices and bills", "path": "Payments", "key": "Payments",
         "where": None, "summary": False},
        {"id": "contacts", "name": "Customers and suppliers", "path": "Contacts", "key": "Contacts",
         "where": None, "summary": True},
    ],
}


def _num(value) -> float:
    """A report cell ("1,234.50", "", None) as a number (blank = 0)."""
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


async def _get_json(url: str, token: str, params: dict | None = None, headers: dict | None = None,
                    meter: "CallMeter | None" = None) -> dict:
    """GET a provider API address with the access token; friendly errors. Calls to a company are metered."""
    if meter:
        await meter.before()
    async with oauth.http_client() as http:
        res = await http.get(url, params=params, headers={"Authorization": f"Bearer {token}",
                                                          "Accept": "application/json", **(headers or {})})
    if meter:
        meter.after(res.headers)
    if res.status_code == 401:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The connection was removed or expired. Please connect again.")
    if res.status_code == 429:
        if meter:
            raise meter.too_many(res.headers)
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                            detail="That service asked us to slow down. Please try again in a minute.")
    if res.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"The service answered with an error ({res.status_code}).")
    return res.json()


# ############################################################################
# The tools' limits: a call meter per connected company
# ############################################################################
# Both tools count our API calls, and going over gets the app BLOCKED (not
# billed) — so every call to a client's company goes through a CallMeter:
#
#   XERO (free Starter plan)          QUICKBOOKS (free Builder tier)
#   1,000 calls / organization / day  500,000 "CorePlus" calls / MONTH for the
#   60 / minute / organization        whole app (every read we make is one);
#                                     past it, ALL clients are blocked until
#                                     next month and QuickBooks shows the app
#                                     "Offline". 500 / minute / company.
#
# What the meter does, per request:
#   * DAILY BUDGET per company: Xero 90% of XERO_DAILY_CALL_LIMIT (900 — the
#     last 10% is a safety margin for anything we didn't count); QuickBooks
#     QUICKBOOKS_DAILY_CALL_LIMIT (1,000 — our own fair-share cap, so one client
#     can't use up the month for everyone). Saved on the client's
#     dim.data_connections row (api_calls_day / api_calls_count); starts over
#     each UTC day.
#   * MONTHLY BUDGET for the whole app (QuickBooks): 90% of
#     QUICKBOOKS_MONTHLY_CALL_LIMIT (450,000), counted in dim.api_usage.
#   * Xero's own count wins: its X-DayLimit-Remaining header, when it says
#     fewer calls are left than we think.
#   * A MINUTE GUARD per company (Xero 55 of 60, QuickBooks 450 of 500): a
#     request that would go over waits a few seconds, or is refused.
#   * A 429 (too many) from the tool is explained in plain words; Xero's
#     daily one stops calls for the rest of the day.

LIMITS: dict[str, dict] = {
    "xero": {"name": "Xero", "day_share": 0.9, "minute": 55},
    "quickbooks": {"name": "QuickBooks", "day_share": 1.0, "minute": 450},
}
MONTH_SHARE = 0.9              # use at most 90% of an app-wide monthly allowance
MINUTE_MAX_WAIT_S = 10         # wait at most this long for the minute window, else refuse
_minute_calls: dict[tuple[str, str], deque] = defaultdict(deque)   # (tool, company) -> recent call times (this server)


def daily_budget(provider: str) -> int | None:
    """
    The most calls we make to ONE company in a day, or None (no limit).

    Example:
        >>> daily_budget("xero"), daily_budget("quickbooks")     # defaults
        (900, 1000)
    """
    s = get_settings()
    limit = {"xero": s.xero_daily_call_limit, "quickbooks": s.quickbooks_daily_call_limit}.get(provider)
    if provider not in LIMITS or not limit:
        return None
    return max(1, int(limit * LIMITS[provider]["day_share"]))


def monthly_budget(provider: str) -> int | None:
    """The most calls the whole app makes to this tool in a month (QuickBooks: 450,000), or None."""
    limit = get_settings().quickbooks_monthly_call_limit if provider == "quickbooks" else 0
    return max(1, int(limit * MONTH_SHARE)) if limit else None


class CallMeter:
    """
    Counts and limits API calls to one connected company for one request.

    Args:
        provider: str, "xero" or "quickbooks".
        company_id: str, the Xero organization (tenant) or QuickBooks company (realm).
        day: date | None, the UTC day the saved daily count belongs to.
        used: int, calls already made to this company that day (dim.data_connections).
        month_used: int, calls the whole app made to this tool this month (dim.api_usage).

    Example:
        >>> CallMeter("xero", "tenant-1", None, 0).budget
        900
    """

    def __init__(self, provider: str, company_id: str, day: date | None, used: int, month_used: int = 0):
        self.provider, self.company_id = provider, company_id
        self.name = LIMITS[provider]["name"]
        today = datetime.now(timezone.utc).date()
        self.day = today
        self.used = int(used or 0) if day == today else 0   # a new day starts at 0
        self.month = today.replace(day=1)
        self.month_used = int(month_used or 0)
        self.request_calls = 0                               # made in THIS request (added to the month)
        self.changed = False
        self.budget = daily_budget(provider) or 10 ** 9
        self.month_budget = monthly_budget(provider)

    def _used_up(self) -> HTTPException:
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                             detail=f"Today's {self.name} allowance for this company is used up ({self.budget:,} calls). "
                                    "It starts over at midnight UTC (8:00 PM Eastern). Uploading an export works meanwhile.")

    async def before(self) -> None:
        """Call before every request to the company: refuse past a budget; pace per minute."""
        today = datetime.now(timezone.utc).date()
        if today != self.day:
            self.day, self.used, self.changed = today, 0, True
        if self.used >= self.budget:
            raise self._used_up()
        if self.month_budget is not None and self.month_used + self.request_calls >= self.month_budget:
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                detail=f"The {self.name} connection has reached this month's allowance and is paused "
                                       "until the 1st. Uploading an export from it works meanwhile.")
        window = _minute_calls[(self.provider, self.company_id)]
        now = time.monotonic()
        while window and now - window[0] >= 60:
            window.popleft()
        if len(window) >= LIMITS[self.provider]["minute"]:
            wait = 60 - (now - window[0])
            if wait > MINUTE_MAX_WAIT_S:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                                    detail=f"{self.name} limits how fast we can ask. Please try again in a minute.")
            await asyncio.sleep(wait)
            window.popleft()
        window.append(time.monotonic())
        self.used += 1
        self.request_calls += 1
        self.changed = True

    def after(self, headers) -> None:
        """Believe Xero's own count: X-DayLimit-Remaining = calls left today (QuickBooks doesn't send one)."""
        remaining = headers.get("X-DayLimit-Remaining")
        if self.provider == "xero" and remaining is not None and str(remaining).isdigit():
            xero_used = get_settings().xero_daily_call_limit - int(remaining)
            if xero_used > self.used:
                self.used, self.changed = xero_used, True

    def too_many(self, headers) -> HTTPException:
        """The tool answered 429: say which limit; stop for the day if it was Xero's daily one."""
        if self.provider == "xero" and (headers.get("X-Rate-Limit-Problem") or "").lower() == "day":
            self.used, self.changed = max(self.used, self.budget), True
            return self._used_up()
        return HTTPException(status.HTTP_429_TOO_MANY_REQUESTS,
                             detail=f"{self.name} asked us to slow down. Please try again in a minute.")


async def meter_for(connector: str, row: dict, creds: dict) -> CallMeter:
    """
    The call meter for one connected company, with today's count (its
    dim.data_connections row) and this month's app-wide count (dim.api_usage).
    """
    from api.services import account_store

    company = creds["extra"]["tenant_id"] if connector == "xero" else creds["extra"]["realm_id"]
    month = datetime.now(timezone.utc).date().replace(day=1)
    return CallMeter(connector, company, row.get("api_calls_day"), row.get("api_calls_count") or 0,
                     await account_store.get_api_usage(connector, month))


async def save_meter(row: dict, meter: CallMeter) -> None:
    """Save today's count on the connection and add this request's calls to the month (also after a failure)."""
    from api.services import account_store

    if meter.changed:
        await account_store.save_api_usage(str(row["account_id"]), row["connector"], meter.day, meter.used)
    await account_store.add_api_usage(row["connector"], meter.month, meter.request_calls)


# ############################################################################
# QuickBooks Online
# ############################################################################

def qb_base() -> str:
    """The QuickBooks API address: the sandbox (development keys) or the real one."""
    if get_settings().quickbooks_environment.strip().lower() == "production":
        return "https://quickbooks.api.intuit.com"
    return "https://sandbox-quickbooks.api.intuit.com"


async def _qb(token: str, realm: str, path: str, params: dict | None = None, meter: CallMeter | None = None) -> dict:
    """GET /v3/company/<realm>/<path> (realm = the QuickBooks company id), metered."""
    return await _get_json(f"{qb_base()}/v3/company/{realm}/{path}", token,
                           {**(params or {}), "minorversion": QB_MINOR_VERSION}, meter=meter)


async def qb_company_name(token: str, realm: str) -> str:
    """The company's name, shown on the Integrations card."""
    body = await _qb(token, realm, f"companyinfo/{realm}")
    return (body.get("CompanyInfo") or {}).get("CompanyName") or "QuickBooks company"


async def qb_query(token: str, realm: str, sql: str, meter: CallMeter | None = None) -> dict:
    """Run a QuickBooks query ("SELECT * FROM Invoice ...") — QuickBooks' own read-only query language."""
    return (await _qb(token, realm, "query", {"query": sql}, meter)).get("QueryResponse", {})


def parse_qb_pnl(report: dict) -> dict:
    """
    Read a QuickBooks Profit and Loss report (summarized by month).

    The report's rows are sections with a `group` (Income, COGS, Expenses,
    OtherIncome, OtherExpenses, NetIncome) whose Summary row holds one total
    per column; the columns are the months, then "Total".

    Returns:
        dict: {"totals": {"income", "expenses", "net_income"},
        "monthly": [{"month": "2026-01", "income", "expenses", "net"}]}.

    Example:
        >>> parse_qb_pnl(sample)["totals"]
        {'income': 1500.0, 'expenses': 600.0, 'net_income': 900.0}
    """
    cols = (report.get("Columns") or {}).get("Column") or []

    def meta(col: dict, name: str) -> str | None:
        return next((m.get("Value") for m in col.get("MetaData") or [] if m.get("Name") == name), None)

    months = [(i, meta(c, "StartDate")[:7]) for i, c in enumerate(cols)
              if c.get("ColType") == "Money" and meta(c, "StartDate") and meta(c, "ColKey") != "total"]
    total_i = next((i for i, c in enumerate(cols) if meta(c, "ColKey") == "total"
                    or (c.get("ColTitle") or "").lower() == "total"), len(cols) - 1)
    groups: dict[str, list[float]] = {}
    for row in (report.get("Rows") or {}).get("Row") or []:
        cells = (row.get("Summary") or {}).get("ColData")
        if row.get("group") and cells:
            groups[row["group"]] = [_num(c.get("value")) for c in cells]

    def at(names: tuple[str, ...], i: int) -> float:
        return round(sum(groups[g][i] for g in names if g in groups and i < len(groups[g])), 2)

    income, expenses = ("Income", "OtherIncome"), ("COGS", "Expenses", "OtherExpenses")

    def net(i: int) -> float:
        return at(("NetIncome",), i) if "NetIncome" in groups else round(at(income, i) - at(expenses, i), 2)

    return {
        "totals": {"income": at(income, total_i), "expenses": at(expenses, total_i), "net_income": net(total_i)},
        "monthly": [{"month": m, "income": at(income, i), "expenses": at(expenses, i), "net": net(i)} for i, m in months],
    }


def _qb_data_rows(rows: list[dict]):
    """Every 'Data' row of a QuickBooks report, at any depth (sub-customers sit inside sections)."""
    for row in rows:
        if row.get("type") == "Data" and row.get("ColData"):
            yield row["ColData"]
        yield from _qb_data_rows((row.get("Rows") or {}).get("Row") or [])


def parse_qb_customer_sales(report: dict) -> list[dict]:
    """Sales by Customer Summary -> the top customers [{"name", "amount"}], biggest first."""
    out = [{"name": c[0].get("value") or "(no name)", "amount": round(_num(c[-1].get("value")), 2)}
           for c in _qb_data_rows((report.get("Rows") or {}).get("Row") or [])]
    return sorted(out, key=lambda r: -r["amount"])[:TOP_CUSTOMERS]


def parse_qb_grand_total(report: dict) -> float:
    """The TOTAL of an aging report (A/R = owed to you, A/P = you owe): the GrandTotal row's last cell."""
    for row in (report.get("Rows") or {}).get("Row") or []:
        cells = (row.get("Summary") or {}).get("ColData")
        if row.get("group") == "GrandTotal" and cells:
            return round(_num(cells[-1].get("value")), 2)
    return 0.0


async def qb_quick_look(token: str, realm: str, start: date, end: date, meter: CallMeter | None = None) -> dict:
    """
    The quick look from QuickBooks' own reports (4 API calls, nothing stored).

    Returns:
        dict: see quick_look() — totals, monthly, top_customers, currency, basis.
    """
    dates = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    pnl = await _qb(token, realm, "reports/ProfitAndLoss", {**dates, "summarize_column_by": "Month"}, meter)
    customers = await _qb(token, realm, "reports/CustomerSales", dates, meter)
    receivable = await _qb(token, realm, "reports/AgedReceivables", None, meter)
    payable = await _qb(token, realm, "reports/AgedPayables", None, meter)
    parsed = parse_qb_pnl(pnl)
    return {
        "totals": {**parsed["totals"], "owed_to_you": parse_qb_grand_total(receivable),
                   "you_owe": parse_qb_grand_total(payable)},
        "monthly": parsed["monthly"],
        "top_customers": parse_qb_customer_sales(customers),
        "currency": (pnl.get("Header") or {}).get("Currency") or "USD",
        "basis": "QuickBooks' own Profit and Loss, Sales by Customer, and A/R and A/P Aging reports.",
        "estimate": False,
    }


async def qb_dataset_frame(token: str, realm: str, entity: str, max_rows: int = LIVE_MAX_ROWS,
                           meter: CallMeter | None = None) -> tuple[pd.DataFrame, bool]:
    """
    One QuickBooks record type, page by page, as a table: one row per
    transaction line, named like the rest of the platform (unified.py).

    Returns:
        tuple[pd.DataFrame, bool]: (the table, True when there were more than max_rows records).
    """
    objects: list[dict] = []
    start = 1
    while len(objects) < max_rows:
        page = (await qb_query(token, realm, f"SELECT * FROM {entity} ORDERBY Id "
                                             f"STARTPOSITION {start} MAXRESULTS {QB_PAGE}", meter)).get(entity) or []
        objects += page
        if len(page) < QB_PAGE:
            break
        start += QB_PAGE
    truncated = len(objects) > max_rows
    long = quickbooks_response_to_unified({"QueryResponse": {entity: objects[:max_rows]}})
    return unified_to_wide(long), truncated


# ############################################################################
# Xero
# ############################################################################

XERO_API = "https://api.xero.com/api.xro/2.0"


async def xero_tenant(token: str) -> dict:
    """
    Which Xero organization the client connected (right after sign-in).

    Returns:
        dict: {"tenant_id", "name", "connection_id"} for the first organization.

    Raises:
        HTTPException: 409 when no organization was shared with us.
    """
    conns = await _get_json("https://api.xero.com/connections", token)
    orgs = [c for c in conns or [] if c.get("tenantType", "ORGANISATION") == "ORGANISATION"]
    if not orgs:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="No Xero organization was shared. Please connect again and pick one.")
    # Each shared organization uses one of the app's connection slots (5 on
    # Starter); we only read the first, so the others are disconnected at once.
    for extra in orgs[1:]:
        await xero_remove_connection(token, extra.get("id"))
    return {"tenant_id": orgs[0]["tenantId"], "name": orgs[0].get("tenantName") or "Xero organization",
            "connection_id": orgs[0].get("id")}


async def xero_remove_all(token: str) -> None:
    """Disconnect every organization this sign-in shared (used when the connection limit is reached)."""
    try:
        conns = await _get_json("https://api.xero.com/connections", token)
    except HTTPException:
        return
    for c in conns or []:
        await xero_remove_connection(token, c.get("id"))


async def xero_remove_connection(token: str, connection_id: str | None) -> None:
    """
    On disconnect: remove the organization connection at Xero, which frees one
    of the app's connection slots (Xero's Starter plan allows 5). Best effort.
    """
    if not connection_id:
        return
    try:
        async with oauth.http_client() as http:
            await http.delete(f"https://api.xero.com/connections/{connection_id}",
                              headers={"Authorization": f"Bearer {token}"})
    except Exception:  # noqa: BLE001 — never block a disconnect
        pass


async def xero_pages(token: str, tenant: str, path: str, key: str, params: dict | None = None,
                     max_rows: int = LIVE_MAX_ROWS, meter: CallMeter | None = None) -> tuple[list[dict], bool]:
    """
    Every item of a Xero list endpoint, page by page (up to max_rows).

    Uses Xero's `pagination.pageCount` when it is sent; otherwise keeps going
    until a page comes back short or empty.

    Returns:
        tuple[list[dict], bool]: (items, True when there were more than max_rows).
    """
    items: list[dict] = []
    page = 1
    while len(items) <= max_rows:
        body = await _get_json(f"{XERO_API}/{path}", token, {**(params or {}), "page": page, "pageSize": XERO_PAGE},
                               {"xero-tenant-id": tenant}, meter)
        got = body.get(key) or []
        items += got
        pages = (body.get("pagination") or {}).get("pageCount")
        if not got or (pages is not None and page >= pages) or (pages is None and len(got) < XERO_PAGE):
            break
        page += 1
    return items[:max_rows], len(items) > max_rows


def _xero_where(*parts: str | None) -> str | None:
    """Join Xero filter pieces with AND."""
    kept = [p for p in parts if p]
    return " && ".join(kept) if kept else None


def _xero_dt(d: date) -> str:
    """A date in Xero's filter syntax: DateTime(2026, 01, 31)."""
    return f"DateTime({d.year}, {d.month:02d}, {d.day:02d})"


_XERO_DATE = re.compile(r"^/Date\((-?\d+)([+-]\d{4})?\)/$")


def xero_date(value) -> str | None:
    """
    Xero's dates ("/Date(1767225600000+0000)/" or "2026-01-01T00:00:00") as ISO text.

    Example:
        >>> xero_date("/Date(1767225600000+0000)/"), xero_date("2026-02-14T00:00:00")
        ('2026-01-01', '2026-02-14')
    """
    if not isinstance(value, str):
        return None
    m = _XERO_DATE.match(value)
    if m:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).date().isoformat()
    return value[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", value) else None


def _xero_item_date(item: dict) -> str | None:
    return xero_date(item.get("DateString")) or xero_date(item.get("Date"))


def xero_summarize(invoices: list[dict], bank: list[dict], unpaid: list[dict]) -> dict:
    """
    Add up Xero records into the quick look (pure: no API calls; easy to test).

    income   = sales invoices (ACCREC) + money received (RECEIVE)
    expenses = bills (ACCPAY) + money spent (SPEND)
    — excluding tax (SubTotal), drafts, voided/deleted items and transfers.
    owed_to_you / you_owe = AmountDue on approved, unpaid invoices / bills.

    Returns:
        dict: totals, monthly, top_customers, currency.
    """
    monthly: dict[str, dict] = defaultdict(lambda: {"income": 0.0, "expenses": 0.0})
    customers: Counter = Counter()
    currencies: Counter = Counter()
    for inv in invoices:
        d = _xero_item_date(inv)
        if inv.get("Status") not in ("AUTHORISED", "PAID") or not d:
            continue
        amount = _num(inv.get("SubTotal"))
        currencies[inv.get("CurrencyCode") or "USD"] += 1
        if inv.get("Type") == "ACCREC":
            monthly[d[:7]]["income"] += amount
            customers[(inv.get("Contact") or {}).get("Name") or "(no name)"] += amount
        elif inv.get("Type") == "ACCPAY":
            monthly[d[:7]]["expenses"] += amount
    for tx in bank:
        d = _xero_item_date(tx)
        if tx.get("Status") != "AUTHORISED" or not d:
            continue
        amount = _num(tx.get("SubTotal"))
        if tx.get("Type") == "RECEIVE":          # not RECEIVE-TRANSFER, -OVERPAYMENT, -PREPAYMENT
            monthly[d[:7]]["income"] += amount
            name = (tx.get("Contact") or {}).get("Name")
            if name:
                customers[name] += amount
        elif tx.get("Type") == "SPEND":
            monthly[d[:7]]["expenses"] += amount
    owed_to_you = sum(_num(i.get("AmountDue")) for i in unpaid if i.get("Type") == "ACCREC")
    you_owe = sum(_num(i.get("AmountDue")) for i in unpaid if i.get("Type") == "ACCPAY")
    months = [{"month": m, "income": round(v["income"], 2), "expenses": round(v["expenses"], 2),
               "net": round(v["income"] - v["expenses"], 2)} for m, v in sorted(monthly.items())]
    income = round(sum(m["income"] for m in months), 2)
    expenses = round(sum(m["expenses"] for m in months), 2)
    return {
        "totals": {"income": income, "expenses": expenses, "net_income": round(income - expenses, 2),
                   "owed_to_you": round(owed_to_you, 2), "you_owe": round(you_owe, 2)},
        "monthly": months,
        "top_customers": [{"name": n, "amount": round(a, 2)} for n, a in customers.most_common(TOP_CUSTOMERS)],
        "currency": currencies.most_common(1)[0][0] if currencies else "USD",
    }


async def xero_quick_look(token: str, tenant: str, start: date, end: date, meter: CallMeter | None = None) -> dict:
    """The quick look from Xero invoices and bank transactions (usually 3 calls, nothing stored)."""
    period = f"Date >= {_xero_dt(start)} && Date <= {_xero_dt(end)}"
    invoices, _ = await xero_pages(token, tenant, "Invoices", "Invoices", {"where": period, "summaryOnly": "true"},
                                   meter=meter)
    bank, _ = await xero_pages(token, tenant, "BankTransactions", "BankTransactions", {"where": period}, meter=meter)
    unpaid, _ = await xero_pages(token, tenant, "Invoices", "Invoices", {"Statuses": "AUTHORISED", "summaryOnly": "true"},
                                 meter=meter)
    return {**xero_summarize(invoices, bank, unpaid),
            "basis": "Added up from your Xero sales invoices, bills and bank transactions (before tax). Xero's own "
                     "Profit & Loss report can differ slightly (credit notes, journals, cash vs. accrual).",
            "estimate": True}


def xero_frame(items: list[dict]) -> pd.DataFrame:
    """Xero items -> a flat table: nested fields become columns (Contact.Name), lists are dropped, dates become ISO."""
    df = pd.json_normalize(items, sep=".")
    listy = [c for c in df.columns if df[c].map(lambda v: isinstance(v, list)).any()]
    df = df.drop(columns=listy)
    for col in df.columns:
        if df[col].dtype == object and df[col].astype(str).str.startswith("/Date(").any():
            df[col] = df[col].map(xero_date)
    return df


# ############################################################################
# One front door for the router
# ############################################################################

def quick_look_earliest(today: date | None = None) -> date:
    """The earliest date the FREE quick look covers: 12 months back. Older history needs the stored sync."""
    return (today or datetime.now(timezone.utc).date()) - timedelta(days=QUICK_LOOK_DAYS)


def _dates(start: date | None, end: date | None) -> tuple[date, date, bool]:
    """
    The period to read: default this year so far, never before the last 12
    months (free quick look) and never after today.

    Returns:
        tuple[date, date, bool]: (start, end, True when the start was moved up to 12 months ago).

    Raises:
        HTTPException: 400 when the start is after the end.
    """
    today = datetime.now(timezone.utc).date()
    earliest = quick_look_earliest(today)
    start, end = start or max(date(today.year, 1, 1), earliest), min(end or today, today)
    limited = start < earliest
    start = max(start, earliest)
    if start > end:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="The start date is after the end date.")
    return start, end, limited


async def quick_look(provider: str, company: str, token: str, extra: dict,
                     start: date | None = None, end: date | None = None, meter: CallMeter | None = None) -> dict:
    """
    The quick look for a connected QuickBooks or Xero (live, nothing stored).

    Returns:
        dict: {"source", "company", "start", "end", "earliest" (12 months back: the free quick
        look's limit), "history_limited" (the asked-for start was earlier), "currency", "estimate", "basis",
        "totals": {"income", "expenses", "net_income", "owed_to_you", "you_owe"},
        "monthly": [{"month", "income", "expenses", "net"}], "top_customers": [{"name", "amount"}],
        "stored": False}.

    Example:
        >>> (await quick_look("quickbooks", "Bakery", tok, {"realm_id": "123"}))["totals"]["income"]
        18210.5
    """
    start, end, limited = _dates(start, end)
    if provider == "quickbooks":
        body = await qb_quick_look(token, extra["realm_id"], start, end, meter)
    elif provider == "xero":
        body = await xero_quick_look(token, extra["tenant_id"], start, end, meter)
    else:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No quick look for that integration.")
    return {"source": oauth.PROVIDERS[provider]["name"], "company": company, "start": start.isoformat(),
            "end": end.isoformat(), "stored": False, "earliest": quick_look_earliest().isoformat(),
            "history_limited": limited, **body}


async def list_datasets(provider: str, token: str, extra: dict, meter: CallMeter | None = None) -> list[dict]:
    """
    What the client can open in the workspace, and whether each has records
    (one cheap call each). Empty ones come back with has_rows False (hidden).

    Returns:
        list[dict]: [{"id", "name", "has_rows"}].
    """
    out = []
    for d in DATASETS.get(provider, []):
        if provider == "quickbooks":
            count = (await qb_query(token, extra["realm_id"], f"SELECT COUNT(*) FROM {d['id']}", meter)).get("totalCount", 0)
            has = int(count or 0) > 0
        else:
            params = {"where": d["where"]} if d["where"] else {}
            body = await _get_json(f"{XERO_API}/{d['path']}", token, {**params, "page": 1, "pageSize": 1},
                                   {"xero-tenant-id": extra["tenant_id"]}, meter)
            has = bool(body.get(d["key"]))
        out.append({"id": d["id"], "name": d["name"], "has_rows": has})
    return out


async def read_dataset_csv(provider: str, token: str, extra: dict, dataset_id: str,
                           max_bytes: int | None = None, meter: CallMeter | None = None) -> dict:
    """
    One record type as CSV for the workspace (live, never stored).

    Returns:
        dict: {"csv": bytes, "rows": int, "truncated": bool, "name": "quickbooks.Invoice"}.

    Raises:
        HTTPException: 404 unknown dataset; 422 when it has no records.
    """
    spec = next((d for d in DATASETS.get(provider, []) if d["id"] == dataset_id), None)
    if not spec:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That record type isn't available.")
    if provider == "quickbooks":
        df, more = await qb_dataset_frame(token, extra["realm_id"], dataset_id, meter=meter)
    else:
        params = {"where": spec["where"]} if spec["where"] else {}
        if spec["summary"]:
            params["summaryOnly"] = "true"
        items, more = await xero_pages(token, extra["tenant_id"], spec["path"], spec["key"], params, meter=meter)
        df = xero_frame(items)
    if df.empty:
        raise HTTPException(422, detail=f"{spec['name']} has no records yet, so there is nothing to analyze.")
    if max_bytes is None:
        max_bytes = get_settings().max_upload_bytes - 64 * 1024
    csv, rows, cut = csv_within(df, max_bytes)
    return {"csv": csv, "rows": rows, "truncated": more or cut, "name": f"{provider}.{dataset_id}"}
