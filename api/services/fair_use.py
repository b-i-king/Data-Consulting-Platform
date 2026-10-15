"""
Fair-use rules: stop the system being gamed without storing anyone's data.

WHAT IT CHECKS
--------------
1. DOWNLOAD LIMIT. A client account can make at most DAILY_DOWNLOAD_LIMIT
   cleaned-data downloads in a rolling DOWNLOAD_WINDOW_HOURS (default 20 per
   24 hours). Splitting one job into dozens of tiny downloads a day is
   blocked with a friendly "try again after ..." message. A consultant
   downloading for a client counts against that client.

2. REPEAT DOWNLOADS OF THE SAME DATA. Every download gets a FINGERPRINT: a
   SHA-256 hash of the cleaned table. A hash is a one-way 64-character code —
   the data can't be rebuilt from it — so saving it in usage_events keeps the
   "we never store client data" promise. If the same account downloads data
   with the same fingerprint again inside the window, it is a repeat: it is
   NOT charged a second time (they already paid for that exact result), but it
   still counts toward the download limit.

Why not per-day rounding? Each download is already rounded UP to a whole
minute (pricing.runtime_cost), so splitting a job into many small downloads
costs MORE, never less. The limit protects the server; the fingerprint keeps
billing fair.

Duplicate data going INTO managed storage (Data Engineering) is handled where
data is stored: api/services/unified.py (plan_upsert) and the unique key on
fact.account_data.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from api.core.config import get_settings
from api.services import account_store


def data_fingerprint(df: pd.DataFrame) -> str:
    """
    Compute a one-way fingerprint (SHA-256 hex) of a cleaned table.

    The hash covers the column names and every value in row order, so the same
    cleaned result always gives the same fingerprint and any change (one cell,
    one renamed column, one extra row) gives a different one. pandas' own
    row hasher is used, so it is fast even for large tables.

    Args:
        df: pd.DataFrame, the cleaned table about to be downloaded.

    Returns:
        str: 64 lowercase hex characters.

    Example:
        >>> a = pd.DataFrame({"x": [1, 2]})
        >>> data_fingerprint(a) == data_fingerprint(a.copy())
        True
        >>> data_fingerprint(a) == data_fingerprint(pd.DataFrame({"x": [1, 3]}))
        False
    """
    h = hashlib.sha256()
    h.update("\x1f".join(map(str, df.columns)).encode("utf-8"))   # column names, in order
    h.update(str(df.shape).encode("utf-8"))
    if len(df):
        # One 64-bit hash per row (values only; index ignored), then hash those.
        row_hashes = pd.util.hash_pandas_object(df.astype(str), index=False).to_numpy()
        h.update(row_hashes.tobytes())
    return h.hexdigest()


@dataclass
class DownloadDecision:
    """
    The fair-use verdict for one download.

    Attributes:
        allowed: False when the account is over its download limit.
        repeat: True when the same data was already downloaded in the window
            (so it is not charged again).
        used: downloads already made in the window (before this one).
        limit: the limit in force (0 = unlimited).
        retry_after: when the oldest download in the window expires (only set
            when not allowed).
        message: a plain-English explanation for the client ("" when allowed
            and not a repeat).
        fingerprint: the data_fingerprint of the table checked (set by
            evaluate_download, saved on the usage record).
    """

    allowed: bool
    repeat: bool
    used: int
    limit: int
    retry_after: datetime | None = None
    message: str = ""
    fingerprint: str = ""


def check_download(recent: list[dict], fingerprint: str, now: datetime | None = None) -> DownloadDecision:
    """
    Decide whether a download may go ahead and whether it should be charged.

    Rules, in order:
    - only downloads newer than now - DOWNLOAD_WINDOW_HOURS count;
    - if DAILY_DOWNLOAD_LIMIT > 0 and that many already happened -> not allowed,
      with the time the oldest one leaves the window;
    - otherwise allowed, and `repeat` is True when one of them has the same
      fingerprint (charge $0).

    Args:
        recent: list[dict], the account's recent usage records, each with
            "created_at" (timezone-aware datetime) and "data_fingerprint"
            (str or None). Older rows are ignored, so passing extra is fine.
        fingerprint: str, data_fingerprint of the table about to be downloaded.
        now: datetime | None, the current time (UTC); tests pass a fixed value.

    Returns:
        DownloadDecision: allowed / repeat / counts / message.

    Example:
        >>> now = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)
        >>> past = [{"created_at": now - timedelta(hours=1), "data_fingerprint": "abc"}]
        >>> d = check_download(past, "abc", now)
        >>> d.allowed, d.repeat, d.used
        (True, True, 1)
    """
    settings = get_settings()
    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=max(settings.download_window_hours, 1))
    in_window = sorted((r for r in recent if r["created_at"] >= window_start), key=lambda r: r["created_at"])
    limit = max(settings.daily_download_limit, 0)
    used = len(in_window)

    if limit and used >= limit:
        # The window frees up one slot when its oldest download turns 24 h old.
        retry_after = in_window[used - limit]["created_at"] + timedelta(hours=settings.download_window_hours)
        return DownloadDecision(
            allowed=False, repeat=False, used=used, limit=limit, retry_after=retry_after,
            message=(f"You've reached the limit of {limit} downloads in {settings.download_window_hours} hours. "
                     f"You can download again after {retry_after:%m/%d/%Y %I:%M %p} UTC."),
        )

    repeat = any(r.get("data_fingerprint") == fingerprint for r in in_window)
    return DownloadDecision(
        allowed=True, repeat=repeat, used=used, limit=limit,
        message="You already downloaded this exact data recently, so this copy is free." if repeat else "",
    )


async def evaluate_download(account_id: str, df: pd.DataFrame, now: datetime | None = None) -> DownloadDecision:
    """
    Fingerprint a table and check it against the account's recent downloads.

    Reads the account's usage records from the last DOWNLOAD_WINDOW_HOURS
    (account_store.recent_usage) and applies check_download. The fingerprint is
    put on the decision so the caller can save it with the usage record.

    Args:
        account_id: str, the client account being billed (for a consultant
            acting on a client, the CLIENT's id).
        df: pd.DataFrame, the cleaned table about to be downloaded.
        now: datetime | None, current UTC time; tests pass a fixed value.

    Returns:
        DownloadDecision: as check_download, with `fingerprint` filled in.

    Example:
        decision = await evaluate_download(str(account["id"]), group.cleaned)
        if not decision.allowed:
            raise HTTPException(429, detail=decision.message)
    """
    now = now or datetime.now(timezone.utc)
    fingerprint = data_fingerprint(df)
    window_start = now - timedelta(hours=max(get_settings().download_window_hours, 1))
    recent = await account_store.recent_usage(account_id, window_start)
    decision = check_download(recent, fingerprint, now)
    decision.fingerprint = fingerprint
    return decision
