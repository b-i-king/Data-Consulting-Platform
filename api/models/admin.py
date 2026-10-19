"""
Request shapes for the Admin panel's endpoints (api/routers/admin.py).

Pydantic checks the JSON before our code runs: a missing or wrong-typed field
gets a 422 reply automatically.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator

from api.services import validation


class PlanPriceIn(BaseModel):
    """Admin -> Prices: a plan's new monthly and annual price, in US dollars."""

    monthly_usd: float = Field(ge=0)
    annual_usd: float = Field(ge=0)
    # Free-trial days on a first package (0 = none; None = keep what's saved).
    trial_days: int | None = Field(default=None, ge=0, le=90)


class SeasonalSaleIn(BaseModel):
    """Admin -> Seasonal sales: one yearly window and its discount off the first payment."""

    name: str = Field(min_length=1, max_length=60)
    starts_on: str = Field(pattern=r"^\d{2}-\d{2}$")    # "MM-DD"
    ends_on: str = Field(pattern=r"^\d{2}-\d{2}$")      # "MM-DD", included
    percent_off: int = Field(ge=1, le=90)
    active: bool


class TesterIn(BaseModel):
    """Admin -> Testers: switch an account's tester flag on or off."""

    is_tester: bool


class HealthcareIn(BaseModel):
    """Admin -> Testers list: mark a plan holder as a healthcare client (their data access is audited)."""

    is_healthcare: bool


class HipaaDecisionIn(BaseModel):
    """Admin -> HIPAA requests: an optional note to the business (why it was approved or not)."""

    note: str | None = Field(default=None, max_length=500)


class DeactivateIn(BaseModel):
    """Admin -> Consultants: an optional short note on why a consultant is switched off."""

    reason: str | None = Field(default=None, max_length=300)


class ConsultantDecisionIn(BaseModel):
    """
    Admin -> Consultant applications: approve or deny.

    A denial needs `denial_reason` (one of consultants.DENIAL_REASONS) AND
    personal feedback in `note` (at least consultants.MIN_FEEDBACK_CHARS) —
    the person sees both — and sets the waiting period (`wait_months`: 3, 6 or
    12) before they may apply again. When approving, `note` is optional.
    """

    denial_reason: Literal["identity_unverified", "not_enough_experience", "skills_mismatch", "work_sample",
                           "conduct", "incomplete", "not_adding"] | None = None
    note: str | None = Field(default=None, max_length=1500)
    wait_months: Literal[3, 6, 12] = 3


class ConsultantInviteIn(BaseModel):
    """Admin -> Consultant invites: an email that may apply, with a private note for admins."""

    email: EmailStr
    note: str | None = Field(default=None, max_length=200)

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str) -> str:
        """
        Check the address and lowercase it (invites match the sign-up email, any case).

        Args:
            v: str, the address as typed.

        Returns:
            str: the cleaned, lowercase address.

        Raises:
            ValueError: for a malformed address (FastAPI replies 422).
        """
        return validation.clean_email(v)


class ConsultingPricesIn(BaseModel):
    """Admin -> Prices -> "Consultant help": send only what changes (dim.consulting_prices)."""

    setup_usd: float | None = Field(default=None, ge=0, le=100000)
    help_rate_usd: float | None = Field(default=None, ge=0, le=1000)
    core_hours: float | None = Field(default=None, ge=0, le=60)
    service_hours: float | None = Field(default=None, ge=0, le=60)
    max_hours: float | None = Field(default=None, gt=0, le=60)
    platform_fee_percent: float | None = Field(default=None, ge=0, le=100)
