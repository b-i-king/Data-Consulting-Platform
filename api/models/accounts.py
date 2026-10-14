"""
Request/response shapes for account endpoints (Pydantic models).

Pydantic checks incoming JSON automatically: if a required field is missing
or an email is malformed, FastAPI replies with a 422 error before our code
even runs. The models also document the API at http://localhost:8000/docs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from api.services import locations, validation
from api.services.intake import COMPANY_SIZE_CODES, validate_industry


class TeamMemberIn(BaseModel):
    """A colleague a business owner wants to add to their team."""

    first_name: str = Field(min_length=1, max_length=200)
    last_name: str = Field(min_length=1, max_length=200)
    email: EmailStr

    # Format rules live in api/services/validation.py (letters only, capital
    # first letter, valid email stored in lowercase).
    @field_validator("first_name")
    @classmethod
    def _first(cls, v: str) -> str:
        return validation.clean_person_name(v, "First name")

    @field_validator("last_name")
    @classmethod
    def _last(cls, v: str) -> str:
        return validation.clean_person_name(v, "Last name")

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str) -> str:
        return validation.clean_email(v)


class SignupIn(BaseModel):
    """
    Signup form.
      * individual -> first name, last name, email
      * business   -> the same, plus company name and optional team members
    """

    account_type: Literal["individual", "business"]
    first_name: str = Field(min_length=1, max_length=200)
    last_name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    company_name: str | None = Field(default=None, max_length=300)
    team_members: list[TeamMemberIn] = Field(default_factory=list, max_length=25)
    # Industry (business accounts only) — see api/services/intake.py.
    #   industry_supersector: "goods_producing" or "service_providing"
    #   naics_sector:         2-digit sector code, e.g. "44-45"
    #   naics_subsector:      3-digit subsector code, e.g. "445"
    industry_supersector: str | None = None
    naics_sector: str | None = None
    naics_subsector: str | None = None
    # Company size (business accounts only): LinkedIn size code "A"–"I",
    # e.g. "C" = 11–50 employees. See intake.COMPANY_SIZES.
    company_size: str | None = None
    # Services picked in the guest questionnaire ("Your needs"), put on the
    # new account's plan. See api/services/entitlements.py.
    service_ids: list[str] = Field(default_factory=list, max_length=20)
    # Optional location, chosen from the picker (a GeoNames city id). Only the
    # id is trusted; the city/state/country are looked up on the server.
    location_id: int | None = None
    # "Other" industry: what the person typed when nothing in the list fit.
    # Saved instead of a NAICS code; leave everything empty to save nothing.
    industry_other: str | None = Field(default=None, max_length=120)
    # "I'd like a consultant to do this for me" -> assigned to the default consultant.
    wants_consultant: bool = False
    # Only consultants (CONSULTANT_EMAILS) send this; see api/services/consultants.py.
    access_code: str | None = Field(default=None, max_length=200)

    @field_validator("first_name")
    @classmethod
    def _first(cls, v: str) -> str:
        return validation.clean_person_name(v, "First name")

    @field_validator("last_name")
    @classmethod
    def _last(cls, v: str) -> str:
        return validation.clean_person_name(v, "Last name")

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str) -> str:
        return validation.clean_email(v)

    @field_validator("industry_other")
    @classmethod
    def _industry_other(cls, v: str | None) -> str | None:
        v = " ".join((v or "").split())     # trim and collapse spaces
        return v or None

    @field_validator("company_name")
    @classmethod
    def _company(cls, v: str | None) -> str | None:
        return validation.clean_company_name(v) if v and v.strip() else None

    @field_validator("location_id")
    @classmethod
    def _location(cls, v: int | None) -> int | None:
        if v is not None and locations.get(v) is None:
            raise ValueError("Choose your city from the list.")
        return v

    @model_validator(mode="after")
    def _business_rules(self) -> "SignupIn":
        # Only businesses have teams and an industry.
        if self.account_type == "individual":
            if self.team_members:
                raise ValueError("Team members can only be added to a business account.")
            if self.industry_supersector or self.naics_sector or self.naics_subsector:
                raise ValueError("Industry applies to business accounts only.")
            if self.company_size:
                raise ValueError("Company size applies to business accounts only.")
            if self.industry_other:
                raise ValueError("Industry applies to business accounts only.")
        if self.industry_other and self.naics_sector:
            raise ValueError("Pick an industry from the list, or choose Other — not both.")
        if self.company_size and self.company_size not in COMPANY_SIZE_CODES:
            raise ValueError("Unknown company size.")
        if self.account_type == "business" and not self.company_name:
            raise ValueError("Business name is required for a business account.")
        error = validate_industry(self.industry_supersector, self.naics_sector, self.naics_subsector)
        if error:
            raise ValueError(error)
        # One email = one person: no repeats within this form.
        emails = [self.email, *(m.email for m in self.team_members)]
        repeats = sorted({e for e in emails if emails.count(e) > 1})
        if repeats:
            raise ValueError(f"Each person needs their own email address — used more than once: {', '.join(repeats)}")
        return self


class SigninIn(BaseModel):
    email: EmailStr
    access_code: str | None = Field(default=None, max_length=200)   # consultants only

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str) -> str:
        return validation.clean_email(v)


class AccountOut(BaseModel):
    id: str
    account_type: str
    first_name: str
    last_name: str
    email: str
    company_name: str | None = None
    team_id: str | None = None
    team_role: str | None = None
    status: str
    industry_supersector: str | None = None
    naics_sector: str | None = None
    naics_subsector: str | None = None
    company_size: str | None = None
    location_id: int | None = None
    location: str | None = None       # "Atlanta, Georgia, United States"
    industry_other: str | None = None
    role: Literal["client", "consultant"] = "client"
    wants_consultant: bool = False
    consultant_name: str | None = None    # the assigned consultant's first name


class AuthOut(BaseModel):
    """Returned after signup/sign-in: the token plus who it belongs to."""

    access_token: str
    token_type: str = "bearer"
    account: AccountOut


class QuoteIn(BaseModel):
    service_ids: list[str] = Field(default_factory=list)
    runtime_seconds: float | None = Field(default=None, ge=0)


class AddServiceIn(BaseModel):
    """Add one service to the plan (from the workspace's left pane)."""

    service_id: str


class CheckoutIn(BaseModel):
    """Start a subscription: which plan, billed monthly or yearly."""

    plan_id: str = Field(max_length=60)
    interval: Literal["month", "year"]


class ConsultingHoursIn(BaseModel):
    """Prepay consulting hours (after the free 30-minute consultation)."""

    hours: int = Field(ge=1, le=40)
    service_id: str | None = Field(default=None, max_length=60)


class CancelPlanIn(BaseModel):
    """Test mode only: cancel a plan (with Stripe, the billing portal does this)."""

    plan_id: str = Field(max_length=60)
