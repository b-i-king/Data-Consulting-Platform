"""
Intake + pricing endpoints (no sign-in required).

    GET  /api/intake/options   everything the guest questionnaire needs to render
    GET  /api/services         the service catalog with starting prices
    POST /api/quote            price a selection of services

The frontend loads `/api/intake/options` once when the wizard opens, so the
questions and answer choices are controlled from the backend. A future iOS /
Android app can reuse the exact same endpoint.
"""

from fastapi import APIRouter, HTTPException, status

from api.core.config import get_settings
from api.models.accounts import QuoteIn
from api.services import intake, pricing
from api.services.file_reader import SUPPORTED_EXTENSIONS

router = APIRouter()


@router.get("/intake/options")
async def intake_options():
    settings = get_settings()
    return {
        "account_types": intake.ACCOUNT_TYPES,
        "data_locations": intake.DATA_LOCATIONS,
        "services": pricing.catalog(),
        "supported_extensions": list(SUPPORTED_EXTENSIONS),
        "limits": {
            "max_upload_mb": settings.max_upload_mb,
            "max_files_per_upload": settings.max_files_per_upload,
        },
        "pricing": {
            "price_per_runtime_minute_usd": settings.price_per_runtime_minute_usd,
            "billing_increment_seconds": settings.billing_increment_seconds,
        },
        "booking_url": settings.booking_url,
    }


@router.get("/services")
async def list_services():
    return pricing.catalog()


@router.post("/quote")
async def quote(body: QuoteIn):
    try:
        return pricing.build_quote(body.service_ids, body.runtime_seconds)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
