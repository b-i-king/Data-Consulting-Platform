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
    # "developer" (upload, connect, load, edit dashboards) or "member" (view
    # only) — api/services/teams.py.
    role: Literal["developer", "member"] = "developer"

    # Format rules live in api/services/validation.py (letters only, capital
    # first letter, valid email stored in lowercase).
    @field_validator("first_name")
    @classmethod
    def _first(cls, v: str) -> str:
        """
        Validate and tidy the first name (Pydantic field validator).

        Runs automatically when the JSON body is parsed. Delegates to
        `validation.clean_person_name`: spaces are collapsed, and only letters
        (any alphabet), single spaces, hyphens, apostrophes and periods are
        allowed, starting with a capital letter.

        Args:
            v: str, the first name as sent by the browser.

        Returns:
            str: the tidied first name.

        Raises:
            ValueError: with a message safe to show the person (e.g. "First name must
                start with a capital letter..."); FastAPI turns it into a 422 reply.

        Example:
            >>> TeamMemberIn(first_name="  Ana ", last_name="O'Brien-Smith", email="a@example.com").first_name
            'Ana'
        """
        return validation.clean_person_name(v, "First name")

    @field_validator("last_name")
    @classmethod
    def _last(cls, v: str) -> str:
        """
        Validate and tidy the last name (Pydantic field validator).

        Runs automatically when the JSON body is parsed. Delegates to
        `validation.clean_person_name`: spaces are collapsed, and only letters
        (any alphabet), single spaces, hyphens, apostrophes and periods are
        allowed, starting with a capital letter.

        Args:
            v: str, the last name as sent by the browser.

        Returns:
            str: the tidied last name.

        Raises:
            ValueError: with a message safe to show the person (e.g. "Last name must
                start with a capital letter..."); FastAPI turns it into a 422 reply.

        Example:
            >>> TeamMemberIn(first_name="  Ana ", last_name="O'Brien-Smith", email="a@example.com").last_name
            "O'Brien-Smith"
        """
        return validation.clean_person_name(v, "Last name")

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str) -> str:
        """
        Validate the email format and store it in lowercase (Pydantic field validator).

        `mode="before"` means this runs on the raw value BEFORE Pydantic's own
        EmailStr check, so spaces are trimmed and the address lowercased first
        (Ana@Example.com and ana@example.com are the same account).

        Args:
            v: str, the email address as sent by the browser.

        Returns:
            str: the normalized, lowercase email address.

        Raises:
            ValueError: "Email address is required." or "Enter a valid email
                address, like name@example.com (...)"; FastAPI replies 422.

        Example:
            >>> TeamMemberIn(first_name="Ana", last_name="Lee", email="  Ana@Example.COM ").email
            'ana@example.com'
        """
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
    # "I'd like a consultant to do this for me": the consultant picked by name
    # (a dim.consultants id; empty = we pick, see consultants.pick) and how they
    # help — "one_time" analysis or "monthly" ongoing help.
    wants_consultant: bool = False
    consultant_id: str | None = Field(default=None, max_length=64)
    consultant_plan: Literal["one_time", "monthly"] | None = None
    # Who is signing up (the clean choice at the bottom of the form):
    #   "client"     the default
    #   "consultant" a REQUEST to be a consultant (waits for an admin)
    #   "admin"      only works for an ADMIN_EMAILS address (refused otherwise)
    signup_as: Literal["client", "consultant", "admin"] = "client"
    # Older name for signup_as="consultant" (still accepted).
    consultant_request: bool = False
    # The Terms of Service + Privacy Policy box. POST /api/accounts/signup
    # refuses a sign-up without it; a consultant adding a client leaves it out.
    accept_terms: bool = False
    # The new password. Required by POST /api/accounts/signup (checked there,
    # with a friendly message); left out when a consultant adds a client — the
    # client then chooses one on their first sign-in. See api/services/auth.py.
    password: str | None = Field(default=None, max_length=128)

    @field_validator("first_name")
    @classmethod
    def _first(cls, v: str) -> str:
        """
        Validate and tidy the account holder's first name (Pydantic field validator).

        Same rules as `TeamMemberIn._first`, via `validation.clean_person_name`:
        letters from any alphabet plus single spaces, hyphens, apostrophes and
        periods, starting with a capital letter.

        Args:
            v: str, the first name as sent by the browser.

        Returns:
            str: the tidied first name (extra spaces collapsed).

        Raises:
            ValueError: e.g. "First name must start with a capital letter...";
                FastAPI turns it into a 422 reply.

        Example:
            >>> SignupIn(account_type="individual", first_name=" Ana ", last_name="Lee",
            ...          email="a@example.com").first_name
            'Ana'
        """
        return validation.clean_person_name(v, "First name")

    @field_validator("last_name")
    @classmethod
    def _last(cls, v: str) -> str:
        """
        Validate and tidy the account holder's last name (Pydantic field validator).

        Same rules as `_first`, via `validation.clean_person_name`.

        Args:
            v: str, the last name as sent by the browser.

        Returns:
            str: the tidied last name.

        Raises:
            ValueError: e.g. "Last name can only use letters..."; FastAPI replies 422.

        Example:
            >>> SignupIn(account_type="individual", first_name="Ana", last_name="O'Brien-Smith",
            ...          email="a@example.com").last_name
            "O'Brien-Smith"
        """
        return validation.clean_person_name(v, "Last name")

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str) -> str:
        """
        Validate the sign-up email format and store it in lowercase (Pydantic field validator).

        `mode="before"` runs this on the raw value before Pydantic's EmailStr
        check. Lowercasing matters because each address may belong to only one
        account, and Ana@Example.com and ana@example.com are the same address.

        Args:
            v: str, the email address as sent by the browser.

        Returns:
            str: the normalized, lowercase email address.

        Raises:
            ValueError: "Email address is required." or "Enter a valid email
                address, like name@example.com (...)"; FastAPI replies 422.

        Example:
            >>> SignupIn(account_type="individual", first_name="Ana", last_name="Lee",
            ...          email=" Ana@Example.COM ").email
            'ana@example.com'
        """
        return validation.clean_email(v)

    @field_validator("industry_other")
    @classmethod
    def _industry_other(cls, v: str | None) -> str | None:
        """
        Tidy the free-text "Other" industry; blank becomes None (Pydantic field validator).

        Trims the ends and collapses runs of spaces to one. An empty or
        all-spaces answer is turned into None so nothing is saved.

        Args:
            v: str | None, what the person typed under "Other", or None.

        Returns:
            str | None: the tidied text, or None when nothing meaningful was typed.

        Example:
            >>> base = dict(account_type="business", first_name="Ana", last_name="Lee",
            ...             email="a@example.com", company_name="Rise Bakery")
            >>> SignupIn(**base, industry_other="  Home   bakery ").industry_other
            'Home bakery'
            >>> SignupIn(**base, industry_other="   ").industry_other is None
            True
        """
        v = " ".join((v or "").split())     # trim and collapse spaces
        return v or None

    @field_validator("company_name")
    @classmethod
    def _company(cls, v: str | None) -> str | None:
        """
        Validate and tidy the business name; blank becomes None (Pydantic field validator).

        A missing or all-spaces name becomes None (whether it is REQUIRED is
        checked later in `_business_rules`). Otherwise
        `validation.clean_company_name` applies: 2–200 characters, letters,
        digits, spaces and common company punctuation (& ' . , - ( ) / + ! # @ : ;),
        starting with a letter or digit.

        Args:
            v: str | None, the business name as sent by the browser, or None.

        Returns:
            str | None: the tidied business name, or None when left empty.

        Raises:
            ValueError: e.g. "Business name must be at least 2 characters.";
                FastAPI replies 422.

        Example:
            >>> SignupIn(account_type="business", first_name="Ana", last_name="Lee",
            ...          email="a@example.com", company_name="  Ben &  Jerry's, LLC ").company_name
            "Ben & Jerry's, LLC"
        """
        return validation.clean_company_name(v) if v and v.strip() else None

    @field_validator("location_id")
    @classmethod
    def _location(cls, v: int | None) -> int | None:
        """
        Check that the chosen location id is a real city in our list (Pydantic field validator).

        Only the GeoNames city id is trusted from the browser; the city, state
        and country names are looked up on the server (api/services/locations.py).

        Args:
            v: int | None, the GeoNames city id picked in the form, or None.

        Returns:
            int | None: the same id when it exists, or None when no location was given.

        Raises:
            ValueError: "Choose your city from the list." for an unknown id;
                FastAPI replies 422.

        Example:
            >>> SignupIn(account_type="individual", first_name="Ana", last_name="Lee",
            ...          email="a@example.com").location_id is None
            True
        """
        if v is not None and locations.get(v) is None:
            raise ValueError("Choose your city from the list.")
        return v

    @model_validator(mode="after")
    def _business_rules(self) -> "SignupIn":
        """
        Cross-field checks that need the whole form (Pydantic model validator).

        `mode="after"` runs once every single field has passed its own checks.
        Rules, in order:
        - Individual accounts may not have team members, an industry (NAICS or
          "Other") or a company size.
        - "Other" industry and a NAICS sector are mutually exclusive.
        - company_size must be one of the LinkedIn codes "A"–"I".
        - Business accounts must have a company name.
        - The industry answers must be consistent (`intake.validate_industry`:
          supersector -> sector -> subsector, each requiring the one before).
        - The same email may not appear twice (owner plus team members).

        Returns:
            SignupIn: the validated model itself, unchanged.

        Raises:
            ValueError: with the first rule broken, e.g. "Business name is
                required for a business account."; FastAPI replies 422.

        Example:
            >>> SignupIn(account_type="individual", first_name="Ana", last_name="Lee",
            ...          email="a@example.com", company_size="C")
            Traceback (most recent call last):
            ...
            pydantic_core.ValidationError: ... Company size applies to business accounts only. ...
        """
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
    password: str = Field(min_length=1, max_length=128)
    # The "Account | Consultant" choice at the top of the sign-in form. True =
    # "I'm signing in as a consultant": refused (after the password checks out)
    # when this account isn't an approved consultant.
    as_consultant: bool = False

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str) -> str:
        """
        Validate the sign-in email format and lowercase it (Pydantic field validator).

        Lowercasing makes sign-in match the address stored at sign-up, however
        the person capitalizes it today.

        Args:
            v: str, the email address as sent by the browser.

        Returns:
            str: the normalized, lowercase email address.

        Raises:
            ValueError: "Email address is required." or "Enter a valid email
                address, like name@example.com (...)"; FastAPI replies 422.

        Example:
            >>> SigninIn(email="  Ana@Example.COM ").email
            'ana@example.com'
        """
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
    is_admin: bool = False                # approves consultants (Admin panel)
    # "I'd like to work as a consultant": None, "pending", "approved" or "denied".
    consultant_request: str | None = None
    wants_consultant: bool = False
    consultant_plan: str | None = None    # "one_time" or "monthly" consultant help
    consultant_name: str | None = None    # the assigned consultant's first name
    mfa_enabled: bool = False             # two-step sign-in is on
    mfa_required: bool = False            # admins / consultants must turn it on (MFA_REQUIRED_FOR_STAFF)
    is_healthcare: bool = False           # HIPAA approved: data access is audited (api/services/audit.py)
    hipaa_status: str | None = None       # None | "pending" | "approved" | "denied" (api/services/hipaa.py)


class AuthOut(BaseModel):
    """Returned after signup/sign-in: the token plus who it belongs to.

    When Supabase's "Confirm email" setting is on, a new sign-up gets NO token
    yet: `needs_confirmation` is True and `message` says to open the emailed
    link, then sign in."""

    access_token: str | None = None
    token_type: str = "bearer"
    account: AccountOut | None = None
    needs_confirmation: bool = False
    message: str | None = None
    # Two-step sign-in: the password was right; send `mfa_ticket` + the code to
    # POST /api/accounts/signin/mfa (no token yet, no account details).
    mfa_required: bool = False
    mfa_ticket: str | None = None
    # Devices signed out to make room (3 at most): ["Chrome on Windows"].
    signed_out_devices: list[str] = Field(default_factory=list)


class HipaaRequestIn(BaseModel):
    """Settings -> Health information (HIPAA): who the healthcare business is, for an admin to confirm."""

    organization: str = Field(min_length=2, max_length=200)   # legal name of the practice / organization
    npi: str | None = Field(default=None, max_length=20)       # 10-digit National Provider Identifier
    note: str | None = Field(default=None, max_length=1000)    # what health data they'd store


class MfaCodeIn(BaseModel):
    """A 6-digit code from the authenticator app, or a backup code like "7f3k-9q2m"."""

    code: str = Field(min_length=6, max_length=20)


class MfaSigninIn(BaseModel):
    """Step two of signing in: the ticket from step one and the code."""

    ticket: str = Field(min_length=10, max_length=2000)
    code: str = Field(min_length=6, max_length=20)


class QuoteIn(BaseModel):
    service_ids: list[str] = Field(default_factory=list)
    runtime_seconds: float | None = Field(default=None, ge=0)


class AddServiceIn(BaseModel):
    """Add one service to the plan (from the workspace's left pane)."""

    service_id: str


class CheckoutIn(BaseModel):
    """Subscribe to (or change) a package: the tools picked, billed monthly or yearly.
    ["all_tools"] = the bundle. Data Cleaning is always added; AI needs Data Engineering."""

    plan_ids: list[str] = Field(min_length=1, max_length=10)
    interval: Literal["month", "year"]


class PriceIn(BaseModel):
    """Price a package before subscribing (the selector's running total)."""

    plan_ids: list[str] = Field(default_factory=list, max_length=10)
    interval: Literal["month", "year"] = "month"


class ConsultationUpdateIn(BaseModel):
    """A consultant marks a client's consultation (api/routers/consultations.py).

    With `consultation_id`: change that consultation's status. Without it: add a
    consultation by hand (for calls booked before the Cal.com webhook was set up)."""

    status: Literal["completed", "no_show", "canceled", "booked"]
    consultation_id: str | None = Field(default=None, max_length=64)
    kind: Literal["free", "paid"] = "free"
    note: str | None = Field(default=None, max_length=500)




class DeleteStoredIn(BaseModel):
    """The "delete my stored data" request (POST /api/warehouse/delete). The word DELETE must be sent
    on purpose, so a stray click or request can't wipe a client's data."""

    confirm: str = Field(max_length=20)

class RefreshIn(BaseModel):
    """How often a connector should pull new data (an id from pricing.REFRESH_OPTIONS)."""

    refresh_frequency: Literal["manual", "weekly", "daily", "hourly", "live"]


class ConsultantSettingsIn(BaseModel):
    """
    A consultant's own settings; send only what changes.

      capacity          how many clients at once (1 to 10, consultants.MAX_CAPACITY)
      free_booking_url  their free 30-minute Cal.com link ("" clears it)
      paid_booking_url  their paid session link ("" clears it)
    Links must be https:// addresses.
    """

    capacity: int | None = Field(default=None, ge=1, le=10)
    free_booking_url: str | None = Field(default=None, max_length=500)
    paid_booking_url: str | None = Field(default=None, max_length=500)

    @field_validator("free_booking_url", "paid_booking_url")
    @classmethod
    def _https(cls, v: str | None) -> str | None:
        """
        Keep booking links safe to show clients: https only, no spaces.

        Args:
            v: str | None, the link as typed ("" = clear it).

        Returns:
            str | None: the trimmed link, "" to clear, or None for "no change".

        Raises:
            ValueError: "Use a full https:// link, like https://cal.com/you/30min."

        Example:
            >>> ConsultantSettingsIn(free_booking_url=" https://cal.com/kim/30min ").free_booking_url
            'https://cal.com/kim/30min'
        """
        if v is None:
            return None
        v = v.strip()
        if v and (not v.lower().startswith("https://") or " " in v or len(v) < 12):
            raise ValueError("Use a full https:// link, like https://cal.com/you/30min.")
        return v


class CloudConnectIn(BaseModel):
    """Connect a client's own Supabase / PostgreSQL database (read-only)."""

    connection_string: str = Field(min_length=12, max_length=1000)


class WaveTokenIn(BaseModel):
    """Admins only: a Wave FULL ACCESS TOKEN for their own business (no Wave Pro needed)."""

    token: str = Field(min_length=10, max_length=500)


class SyncIn(BaseModel):
    """Start the stored sync. consent = "store my data" ticked (needed once, like any storing)."""

    consent: bool = False


class EntityDecisionIn(BaseModel):
    """An answer to "Is this the same customer?" (api/services/entities.py decide)."""

    link_id: int
    same: bool


class SupabaseProjectIn(BaseModel):
    """After "Sign in with Supabase": the project to read (its ref, e.g. "abcdefghijklmnop")."""

    ref: str = Field(min_length=1, max_length=64)


class ForgotPasswordIn(BaseModel):
    """ "Forgot password?": the email to send the reset link to."""

    email: EmailStr

    @field_validator("email", mode="before")
    @classmethod
    def _email(cls, v: str) -> str:
        """Validate and lowercase the email (same rules as sign-in)."""
        return validation.clean_email(v)


class ResetPasswordIn(BaseModel):
    """The reset page: the token from the emailed link and the new password."""

    token: str = Field(min_length=10, max_length=4000)
    password: str = Field(min_length=1, max_length=128)


class BusinessContextIn(BaseModel):
    """ "About your business" (Account page): what dashboards are built around."""

    about: str = Field(default="", max_length=1000)          # what the business does, who it serves
    goals: str = Field(default="", max_length=1000)          # what they want to improve
    key_questions: list[str] = Field(default_factory=list, max_length=12)   # the questions they ask most


class DashboardTabIn(BaseModel):
    """A dashboard tab: a name and its saved plain-English questions."""

    name: str = Field(min_length=1, max_length=60)
    questions: list[str] = Field(default_factory=list, max_length=12)


class QuestionLogIn(BaseModel):
    """One question asked of the data (only the text is kept)."""

    question: str = Field(min_length=1, max_length=300)
    answered: bool = True


class TeamRoleIn(BaseModel):
    """The team owner changes a colleague's role."""

    role: Literal["developer", "member"]


class DeleteAccountIn(BaseModel):
    """ "Delete my account": the person types their own email to confirm."""

    confirm_email: str = Field(min_length=3, max_length=320)
    # Optional "Why are you leaving?" pick. Kept only as a count in the anonymous
    # dim.account_deletions row (a fixed list, so no personal text is ever stored).
    reason: Literal["too_expensive", "missing_features", "not_needed", "switching_tools", "privacy", "other"] | None = None


class ChooseConsultantIn(BaseModel):
    """A client picks (or changes) their consultant, and how they help."""

    consultant_id: str | None = Field(default=None, max_length=64)   # empty = we pick
    consultant_plan: Literal["one_time", "monthly"] = "one_time"
