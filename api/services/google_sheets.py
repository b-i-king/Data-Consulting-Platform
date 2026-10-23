"""
google_sheets — read a client's Google Sheet, live and read-only (nothing stored).

Many small businesses keep their data in ONE Google Sheet they add to every
day. Connecting Google (Settings -> Integrations -> "Connect Google Sheets",
an OAuth sign-in like QuickBooks: api/services/oauth.py, provider
"google_sheets") lets them open that sheet in the workspace any time and always
get the latest rows — no exporting, no re-uploading.

How it works:
  * The client pastes the sheet's link (or its ID). We list its tabs
    (`tabs`) and read one (`read_tab_csv`) with the Sheets API, using the
    client's own sign-in and the READ-ONLY permission
    "spreadsheets.readonly" — we can't change their sheet.
  * The tab comes back as CSV and opens like an uploaded file (cleaning,
    dashboards, analysis). Nothing is stored; opening it again reads it again.
  * At most MAX_ROWS rows and MAX_COLUMNS columns per tab, like other live reads.

Google requires a free review of the app ("sensitive" scope) before the public
can use it; until then up to 100 test users added in Google Cloud can (see
docs/CONNECTORS.md, "Google Sheets").
"""

from __future__ import annotations

import csv
import io
import re
from urllib.parse import quote

from fastapi import HTTPException, status

from api.services import oauth

API = "https://sheets.googleapis.com/v4/spreadsheets"
USERINFO = "https://openidconnect.googleapis.com/v1/userinfo"
MAX_ROWS = 20_000          # rows per tab, like other live reads (business_tools.LIVE_MAX_ROWS)
MAX_COLUMNS = 200
_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]{20,})|^([A-Za-z0-9_-]{20,})$")


def sheet_id(link: str) -> str:
    """
    The spreadsheet ID from its link (or the ID itself).

    Example:
        >>> sheet_id("https://docs.google.com/spreadsheets/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/edit#gid=0")
        '1AbCdEfGhIjKlMnOpQrStUvWxYz012345'

    Raises:
        HTTPException: 400 when it isn't a Google Sheets link.
    """
    m = _ID.search((link or "").strip())
    if not m:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Paste the link to your Google Sheet (it looks like docs.google.com/spreadsheets/d/...).")
    return m.group(1) or m.group(2)


async def _get(token: str, url: str, params: dict | None = None) -> dict:
    """One read from Google, with plain-English errors."""
    async with oauth.http_client() as http:
        res = await http.get(url, params=params, headers={"Authorization": f"Bearer {token}"})
    if res.status_code in (403, 404):
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="We couldn't open that sheet. Check the link, and that the Google account you "
                                   "connected can view it.")
    if res.status_code == 401:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Your Google sign-in expired. Please connect again.")
    if res.status_code == 429:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, detail="Google asked us to slow down. Try again in a minute.")
    if res.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Google Sheets didn't answer. Please try again.")
    return res.json()


async def account_email(token: str) -> str:
    """The connected Google account's email (shown as the connection's name)."""
    return (await _get(token, USERINFO)).get("email") or "Google account"


async def tabs(token: str, link: str) -> dict:
    """
    The sheet's title and its tabs.

    Returns:
        dict: {"id", "title", "tabs": [{"title", "rows", "columns"}]} (rows / columns = the tab's size).
    """
    sid = sheet_id(link)
    body = await _get(token, f"{API}/{sid}", {"fields": "properties.title,sheets.properties(title,gridProperties)"})
    return {"id": sid, "title": body.get("properties", {}).get("title", "Google Sheet"),
            "tabs": [{"title": s["properties"]["title"],
                      "rows": s["properties"].get("gridProperties", {}).get("rowCount", 0),
                      "columns": s["properties"].get("gridProperties", {}).get("columnCount", 0)}
                     for s in body.get("sheets", [])]}


async def read_tab_csv(token: str, link: str, tab: str) -> dict:
    """
    One tab as CSV text (first row = column names), read live.

    Returns:
        dict: {"csv", "rows", "truncated", "name"} — like the other live reads (connections router).

    Raises:
        HTTPException: 404 the sheet / tab can't be opened; 422 the tab is empty.
    """
    sid = sheet_id(link)
    # 'Tab name'!A1:… — quotes let tab names have spaces; the range caps what Google sends.
    rng = f"'{tab.replace(chr(39), chr(39) * 2)}'!A1:{_col_letter(MAX_COLUMNS)}{MAX_ROWS + 1}"
    body = await _get(token, f"{API}/{sid}/values/{quote(rng, safe='')}", {"majorDimension": "ROWS"})
    values = body.get("values", [])
    if len(values) < 2:
        raise HTTPException(422, detail=f"The tab '{tab}' has no rows under its header.")
    width = max(len(r) for r in values)
    out = io.StringIO()
    writer = csv.writer(out)
    for row in values:
        writer.writerow(row + [""] * (width - len(row)))     # Google leaves off empty cells at the end of a row
    rows = len(values) - 1
    return {"csv": out.getvalue(), "rows": rows, "truncated": rows >= MAX_ROWS, "name": tab}


def _col_letter(n: int) -> str:
    """Column number -> letters (1 -> A, 27 -> AA, 200 -> GR)."""
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s
