"""
Error monitoring (Sentry) with personal data stripped before anything leaves the server.

WHAT THIS FILE DOES
-------------------
When the API crashes on a request, you want to know (and see where in the
code) — but the report must never carry a client's data. With SENTRY_DSN set
(sentry.io -> a Python/FastAPI project -> Client Keys), init() turns on Sentry
with:

  * send_default_pii=False   no IP addresses, cookies or user details
  * no request bodies        uploads and form fields are never sent
  * scrub()                  before each report is sent: Authorization /
                             Cookie / X-Acting-As headers, query strings, and
                             every local variable's VALUE are removed; any
                             email address or phone number left in the error
                             message itself is replaced with [email] / [phone]
  * no performance tracing   (traces_sample_rate 0: cheaper, less data)

What still goes: the error type, the message (scrubbed), and the file and line
numbers where it happened. Sentry's free plan is enough to start.
Without SENTRY_DSN nothing is sent anywhere (errors stay in Render's logs).
"""

from __future__ import annotations

import logging
import re

from api.core.config import get_settings

log = logging.getLogger(__name__)

EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.-]?)?\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)")
DROP_HEADERS = {"authorization", "cookie", "x-acting-as", "x-device-id", "set-cookie"}


def scrub_text(text):
    """Replace emails and phone numbers in a piece of text ("ana@x.com" -> "[email]")."""
    if not isinstance(text, str):
        return text
    return PHONE.sub("[phone]", EMAIL.sub("[email]", text))


def scrub(event: dict, hint: dict | None = None) -> dict:
    """
    Sentry's before_send hook: strip personal data from a report.

    Args:
        event: dict, the report Sentry is about to send.
        hint: dict | None, extra info from Sentry (unused).

    Returns:
        dict: the same report, scrubbed (Sentry sends what's returned).

    Example:
        >>> scrub({"message": "No account for ana@example.com"})["message"]
        'No account for [email]'
    """
    request = event.get("request") or {}
    request.pop("data", None)                    # request bodies (uploads, forms)
    request.pop("cookies", None)
    request.pop("query_string", None)            # may hold emails, tokens
    if isinstance(request.get("headers"), dict):
        request["headers"] = {k: v for k, v in request["headers"].items() if k.lower() not in DROP_HEADERS}
    event.pop("user", None)
    if "message" in event:
        event["message"] = scrub_text(event["message"])
    if isinstance(event.get("logentry"), dict):
        event["logentry"]["message"] = scrub_text(event["logentry"].get("message"))
        event["logentry"].pop("params", None)
    for exc in (event.get("exception") or {}).get("values") or []:
        exc["value"] = scrub_text(exc.get("value"))
        for frame in (exc.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)              # local variables can hold client rows
    for crumb in (event.get("breadcrumbs") or {}).get("values") or []:
        crumb["message"] = scrub_text(crumb.get("message"))
        crumb.pop("data", None)
    return event


def init() -> bool:
    """
    Start Sentry if SENTRY_DSN is set (called once, from api/main.py).

    Returns:
        bool: True when monitoring is on.
    """
    dsn = get_settings().sentry_dsn
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:   # the package isn't installed: run without it
        log.warning("SENTRY_DSN is set but sentry-sdk isn't installed; error monitoring is off.")
        return False
    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,           # no IPs, cookies or user details
        include_local_variables=False,    # local variables can hold client rows
        max_request_body_size="never",    # never the uploads or form fields
        traces_sample_rate=0.0,           # errors only, no performance tracing
        before_send=scrub,
        before_breadcrumb=_scrub_breadcrumb,
    )
    return True


def _scrub_breadcrumb(crumb: dict, hint: dict | None = None) -> dict:
    """Sentry's before_breadcrumb hook: the same scrubbing for the trail of events before an error."""
    crumb["message"] = scrub_text(crumb.get("message"))
    crumb.pop("data", None)
    return crumb
