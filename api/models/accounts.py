"""
Request/response shapes for account endpoints (Pydantic models).

Pydantic checks incoming JSON automatically: if a required field is missing
or an email is malformed, FastAPI replies with a 422 error before our code
even runs. The models also document the API at http://localhost:8000/docs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator

from api.services.intake import COMPANY_SIZE_CODES, validate_industry


class TeamMemberIn(BaseModel):
    """A colleague a business owner wants to add to their team."""

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr


class SignupIn(BaseModel):
    """
    Signup form.
      * individual -> first name, last name, email
      * business   -> the same, plus company name and optional team members
    """

    account_type: Literal["individual", "business"]
    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    company_name: str | None = Field(default=None, max_length=200)
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
        if self.company_size and self.company_size not in COMPANY_SIZE_CODES:
            raise ValueError("Unknown company size.")
        error = validate_industry(self.industry_supersector, self.naics_sector, self.naics_subsector)
        if error:
            raise ValueError(error)
        return self


class SigninIn(BaseModel):
    email: EmailStr


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
