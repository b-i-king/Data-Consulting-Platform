"""
Request shapes for the Admin panel's endpoints (api/routers/admin.py).

Pydantic checks the JSON before our code runs: a missing or wrong-typed field
gets a 422 reply automatically.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


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
