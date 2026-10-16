"""
Intake + pricing endpoints (no sign-in required).

    GET  /api/intake/options   everything the guest questionnaire needs to render
    GET  /api/services         the service catalog with starting prices
    POST /api/quote            price a selection of services
    GET  /api/locations?q=atl  city search for the signup form's location picker

The frontend loads `/api/intake/options` once when the wizard opens, so the
questions and answer choices are controlled from the backend. A future iOS /
Android app can reuse the exact same endpoint.
"""

from fastapi import APIRouter, HTTPException, status

from api.core.config import get_settings
from api.models.accounts import QuoteIn
from api.services import consultants, intake, locations, pricing
from api.services.file_reader import EXCLUDED_FORMATS, SUPPORTED_EXTENSIONS, formats_for_ui

router = APIRouter()


@router.get("/intake/options")
async def intake_options():
    """
    GET /api/intake/options — everything the guest questionnaire needs to render.

    Loaded once when the wizard opens, so questions and answer choices are
    controlled from the backend (and reusable by a future mobile app).

    Returns:
        dict: keys "account_types", "data_locations", "services",
        "supported_extensions", "file_formats", "excluded_formats", "limits"
        (max_upload_mb, max_files_per_upload), "pricing" (per-minute rate,
        billing increment), "booking_url", "subscriptions",
        "consulting_rate_per_hour_usd", "consultant" ({"name"} or None until the
        default consultant has signed in once), "industries" (NAICS tree) and
        "company_sizes".

    Example:
        GET /api/intake/options
        -> 200 {"account_types": [...], "limits": {"max_upload_mb": 50, "max_files_per_upload": 20},
                "consultant": null, ...}
    """
    settings = get_settings()
    return {
        "account_types": intake.ACCOUNT_TYPES,
        "data_locations": intake.DATA_LOCATIONS,
        "services": pricing.catalog(),
        "supported_extensions": list(SUPPORTED_EXTENSIONS),
        # Grouped list for the upload screen; CSV and Excel are marked recommended.
        "file_formats": formats_for_ui(),
        "excluded_formats": EXCLUDED_FORMATS,
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_files_per_upload": settings.max_files_per_upload,
        },
        "pricing": {
            "price_per_runtime_minute_usd": settings.price_per_runtime_minute_usd,
            "billing_increment_seconds": settings.billing_increment_seconds,
        },
        "booking_url": settings.booking_url,
        # Plans, each with a monthly and a yearly price (cancel anytime).
        "subscriptions": pricing.subscription_catalog(),
        # Consulting after the free 30-minute first consultation.
        "consulting_rate_per_hour_usd": pricing.consulting_rate(),
        # The default consultant's first name, for "I'd like Brandon to do this
        # for me" at signup (None until a consultant has signed in once).
        "consultant": {"name": c["display_name"]} if (c := await consultants.default_consultant()) else None,
        # Every consultant and how full they are, for the "pick your consultant"
        # fill bars (first names and counts only — api/services/consultants.py).
        "consultants": await consultants.directory(),
        # Business funnel: goods/services -> NAICS sector -> subsector.
        "industries": intake.naics_tree(),
        # Business size bands (LinkedIn company size codes A–I).
        "company_sizes": intake.COMPANY_SIZES,
    }


@router.get("/locations")
async def search_locations(q: str = "", limit: int = 8):
    """
    GET /api/locations?q=atl — city search for the signup form's location picker.

    Case- and accent-insensitive; needs at least 2 letters of city name.
    Text after a comma narrows by state or country ("paris, fr"). `limit` is
    clamped to 1–20.

    Args:
        q: str, what the person has typed so far (default "").
        limit: int, the most matches to return (default 8, clamped to 1–20).

    Returns:
        list[dict]: matching cities (GeoNames id plus city/state/country label);
        an empty list for fewer than 2 letters.

    Example:
        GET /api/locations?q=atlanta&limit=3
        -> 200 [{"id": 4180439, "label": "Atlanta, Georgia, United States", ...}, ...]
    """
    # Typeahead: at least 2 letters; "paris, fr" narrows by state/country.
    return locations.search(q, max(1, min(limit, 20)))


@router.get("/services")
async def list_services():
    """
    GET /api/services — the service catalog with starting prices.

    Returns:
        list[dict]: one entry per service (id, name, starting price, ...), from
        `pricing.catalog()`.

    Example:
        GET /api/services
        -> 200 [{"id": "data_cleaning", ...}, {"id": "data_strategy_discovery", ...}, ...]
    """
    return pricing.catalog()


@router.post("/quote")
async def quote(body: QuoteIn):
    """
    POST /api/quote — price a selection of services.

    Data Cleaning is always included, even if not ticked. An optional measured
    runtime adds the runtime-based charge.

    Args:
        body: QuoteIn, the picked service ids and optional runtime_seconds (>= 0).

    Returns:
        dict: the quote from `pricing.build_quote`.

    Raises:
        HTTPException: 400 "Unknown service id(s): ..." (ValueError from pricing).

    Example:
        POST /api/quote
        {"service_ids": ["data_visualization"], "runtime_seconds": 42}
        -> 200 {...quote lines and totals...}
    """
    try:
        return pricing.build_quote(body.service_ids, body.runtime_seconds)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
