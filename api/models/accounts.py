"""
Request/response shapes for account endpoints (Pydantic models).

Pydantic checks incoming JSON automatically: if a required field is missing
or an email is malformed, FastAPI replies with a 422 error before our code
even runs. The models also document the API at http://localhost:8000/docs.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, EmailStr, Field, model_validator


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

    @model_validator(mode="after")
    def _business_rules(self) -> "SignupIn":
        # Only businesses have teams.
        if self.account_type == "individual" and self.team_members:
            raise ValueError("Team members can only be added to a business account.")
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


class AuthOut(BaseModel):
    """Returned after signup/sign-in: the token plus who it belongs to."""

    access_token: str
    token_type: str = "bearer"
    account: AccountOut


class QuoteIn(BaseModel):
    service_ids: list[str] = Field(default_factory=list)
    runtime_seconds: float | None = Field(default=None, ge=0)
