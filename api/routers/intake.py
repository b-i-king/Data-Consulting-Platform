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
        # Business funnel: goods/services -> NAICS sector -> subsector.
        "industries": intake.naics_tree(),
        # Business size bands (LinkedIn company size codes A–I).
        "company_sizes": intake.COMPANY_SIZES,
    }


@router.get("/locations")
async def search_locations(q: str = "", limit: int = 8):
    # Typeahead: at least 2 letters; "paris, fr" narrows by state/country.
    return locations.search(q, max(1, min(limit, 20)))


@router.get("/services")
async def list_services():
    return pricing.catalog()


@router.post("/quote")
async def quote(body: QuoteIn):
    try:
        return pricing.build_quote(body.service_ids, body.runtime_seconds)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
