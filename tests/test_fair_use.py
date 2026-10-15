"""
Fair use: the download limit, free repeats of the exact same data, and the
one-way data fingerprint (api/services/fair_use.py), through the service and
through POST /api/files/download.
"""

import io
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from api.core.config import get_settings
from api.main import app
from api.services import account_store, fair_use

client = TestClient(app)
NOW = datetime(2026, 10, 15, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def limit_of_three(monkeypatch):
    """
    Use a small download limit (3 per 24 hours) and a clean in-memory store for each test.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.

    Returns:
        Iterator[None]: yields while the test runs; settings are reset afterwards.
    """
    monkeypatch.setenv("DAILY_DOWNLOAD_LIMIT", "3")
    monkeypatch.setenv("DOWNLOAD_WINDOW_HOURS", "24")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    get_settings.cache_clear()
    account_store._usage.clear()
    account_store._accounts.clear()
    yield
    get_settings.cache_clear()


def _rows(*hours_ago_and_fp):
    """
    Build fake usage records for check_download.

    Args:
        *hours_ago_and_fp: (hours before NOW, fingerprint) pairs.

    Returns:
        list[dict]: [{"created_at": datetime, "data_fingerprint": str}, ...].

    Example:
        >>> _rows((1, "a"), (30, "b"))[0]["data_fingerprint"]
        'a'
    """
    return [{"created_at": NOW - timedelta(hours=h), "data_fingerprint": fp} for h, fp in hours_ago_and_fp]


def test_fingerprint_is_stable_and_changes_with_the_data():
    """Check that the same table always hashes the same and any cell, name or row change gives a new hash."""
    df = pd.DataFrame({"customer": ["Ann", "Bob"], "amount": [10.0, 12.5]})
    fp = fair_use.data_fingerprint(df)
    assert len(fp) == 64 and fp == fair_use.data_fingerprint(df.copy())
    assert fp != fair_use.data_fingerprint(df.assign(amount=[10.0, 12.6]))
    assert fp != fair_use.data_fingerprint(df.rename(columns={"amount": "total"}))
    assert fp != fair_use.data_fingerprint(df.head(1))


def test_limit_counts_only_the_window_and_says_when_to_retry():
    """Check that downloads older than 24 hours don't count, and the 4th inside the window is refused."""
    ok = fair_use.check_download(_rows((1, "a"), (2, "b"), (30, "c")), "z", NOW)
    assert ok.allowed and ok.used == 2 and not ok.repeat
    full = fair_use.check_download(_rows((1, "a"), (2, "b"), (5, "c")), "z", NOW)
    assert not full.allowed
    assert full.retry_after == NOW - timedelta(hours=5) + timedelta(hours=24)   # oldest leaves the window
    assert "limit of 3 downloads" in full.message


def test_same_data_again_is_a_free_repeat():
    """Check that a fingerprint already downloaded inside the window is flagged as a repeat."""
    d = fair_use.check_download(_rows((3, "same")), "same", NOW)
    assert d.allowed and d.repeat and "free" in d.message
    assert not fair_use.check_download(_rows((25, "same")), "same", NOW).repeat   # outside the window


def test_zero_means_no_limit(monkeypatch):
    """
    Check that DAILY_DOWNLOAD_LIMIT=0 switches the limit off.

    Args:
        monkeypatch: pytest fixture for setting environment variables for one test.
    """
    monkeypatch.setenv("DAILY_DOWNLOAD_LIMIT", "0")
    get_settings.cache_clear()
    assert fair_use.check_download(_rows(*[(1, str(i)) for i in range(50)]), "z", NOW).allowed


def test_download_endpoint_repeat_is_free_and_limit_returns_429():
    """Check through the API: a repeat download costs $0.00, and going over the limit returns 429."""
    res = client.post("/api/accounts/signup", json={"password": "Correct-horse-9", "account_type": "individual", "first_name": "Pat",
                                                    "last_name": "Lee", "email": "fair@example.com"})
    h = {"Authorization": f"Bearer {res.json()['access_token']}"}

    def download(csv: bytes):
        return client.post("/api/files/download", headers=h, files=[("files", ("s.csv", io.BytesIO(csv), "text/csv"))])

    first = download(b"customer,amount\nAnn,10\nBob,12\n")
    assert first.status_code == 200 and first.headers["X-Amount-USD"] != "0.00"
    assert first.headers["X-Repeat-Download"] == "0"
    again = download(b"customer,amount\nAnn,10\nBob,12\n")
    assert again.headers["X-Amount-USD"] == "0.00" and again.headers["X-Repeat-Download"] == "1"
    assert download(b"customer,amount\nCy,7\n").status_code == 200        # 3rd of 3
    over = download(b"customer,amount\nDee,9\n")
    assert over.status_code == 429 and "limit of 3 downloads" in over.json()["detail"]
    # Only fingerprints are kept — never the data.
    assert all(len(u["data_fingerprint"]) == 64 for u in account_store._usage)
