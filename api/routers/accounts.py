"""
Account endpoints.

    POST /api/accounts/signup         create an individual or business account (email + password)
    POST /api/accounts/signin         check email + password, get a new token
    GET  /api/accounts/me             who am I? (requires token)
    GET  /api/accounts/services       services on my plan + the full catalog (requires token)
    POST /api/accounts/services       record interest in a project with a consultant (doesn't unlock tools)
    GET  /api/accounts/team           list my team (business accounts)
    POST /api/accounts/team/members   add a colleague (business owners only)

Guests only need an account at the DOWNLOAD step. Signup asks for:
  * individual: first name, last name, email
  * business:   first name, last name, email, company name (+ optional team members)
  * optional:   "I'd like a consultant to do this for me"
(Subscriptions are bought after signup: api/routers/billing.py.)

CONSULTANTS sign up and sign in here too: their email must be in
CONSULTANT_EMAILS and they must send CONSULTANT_ACCESS_CODE. Their token says
role "consultant". The services endpoints accept `X-Acting-As: <client id>`
from a consultant to work on an assigned client (api/services/consultants.py).

PASSWORDS
---------
Sign-up and sign-in use an email + password (api/services/auth.py): Supabase
Auth checks the password when SUPABASE_URL / SUPABASE_KEY are set, otherwise
the API keeps a one-way hash (local development). After the password checks
out, the API issues its own login token, as before.
Still to do before launch (docs/ROADMAP.md): "forgot password", and invite
emails for colleagues / consultant-added clients (today their first sign-in
sets their password).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.security import create_access_token, get_acting_context, get_current_account
from api.models.accounts import (
    AccountOut,
    AddServiceIn,
    AuthOut,
    SigninIn,
    SignupIn,
    TeamMemberIn,
)
from api.services import account_store, auth, billing, consultants, consultations, entitlements, locations, pricing
from api.services.pricing import catalog, subscription_catalog

router = APIRouter(prefix="/accounts")

# A valid sign-in token whose account no longer exists. Without a database
# (DATABASE_URL empty, e.g. a free test deploy) accounts live in memory and
# vanish whenever the server restarts or sleeps, so say that plainly. 401 tells
# the website to sign the person out.
ACCOUNT_GONE = ("We couldn't find your account. If this is a test server without a database, it may "
                "have restarted and forgotten accounts — please create your account again.")


def _account_out(acc: dict, role: str = "client", consultant_name: str | None = None) -> AccountOut:
    """
    Convert a stored account (dict with UUIDs/dates) to the API shape.

    The store returns database rows (UUID objects, optional keys). This turns
    ids into strings, fills safe defaults for missing optional fields
    (status "active", wants_consultant False) and renames storage columns to
    API names (location_geoname_id -> location_id, location_label -> location).
    It does no lookups, so the caller supplies the role and consultant name.

    Args:
        acc: dict, one account row from api/services/account_store.py.
        role: str, "client" (default) or "consultant".
        consultant_name: str | None, the assigned consultant's display name, if any.

    Returns:
        AccountOut: the account as sent to the browser (see web/lib/types.ts).

    Example:
        >>> out = _account_out({"id": "a1", "account_type": "individual", "first_name": "Ana",
        ...                     "last_name": "Lee", "email": "ana@example.com"})
        >>> out.id, out.status, out.role
        ('a1', 'active', 'client')
    """
    return AccountOut(
        id=str(acc["id"]),
        account_type=acc["account_type"],
        first_name=acc["first_name"],
        last_name=acc["last_name"],
        email=acc["email"],
        company_name=acc.get("company_name"),
        team_id=str(acc["team_id"]) if acc.get("team_id") else None,
        team_role=acc.get("team_role"),
        status=acc.get("status", "active"),
        industry_supersector=acc.get("industry_supersector"),
        naics_sector=acc.get("naics_sector"),
        naics_subsector=acc.get("naics_subsector"),
        company_size=acc.get("company_size"),
        location_id=acc.get("location_geoname_id"),
        location=acc.get("location_label"),
        industry_other=acc.get("industry_other"),
        role=role,
        wants_consultant=bool(acc.get("wants_consultant")),
        consultant_name=consultant_name,
    )


async def _full_account_out(acc: dict) -> AccountOut:
    """
    _account_out plus the role and the assigned consultant's name (looked up).

    The role is "consultant" when the account has a row in the consultants
    table, otherwise "client". The consultant is stored on the PLAN HOLDER
    (an individual, or a business's team owner), so for a team member the
    owner's consultant is shown.

    Args:
        acc: dict, one account row from the account store.

    Returns:
        AccountOut: the account with `role` and `consultant_name` filled in.

    Example:
        out = await _full_account_out(await account_store.get_account(token["sub"]))
        out.consultant_name   # e.g. "Dana", or None when no consultant is assigned
    """
    role = "consultant" if await account_store.get_consultant_by_account(str(acc["id"])) else "client"
    holder_id = await entitlements.plan_holder_id(acc)
    holder = acc if holder_id == str(acc["id"]) else await account_store.get_account(holder_id)
    consultant = await account_store.get_consultant(str(holder["consultant_id"])) if holder and holder.get("consultant_id") else None
    return _account_out(acc, role, consultant["display_name"] if consultant else None)


async def _auth_response(acc: dict) -> AuthOut:
    """
    Build the reply for a successful sign-up or sign-in: a new token plus the account.

    The token's role comes from the looked-up account (so a consultant gets a
    "consultant" token).

    Args:
        acc: dict, the account row that just signed up or signed in.

    Returns:
        AuthOut: {"access_token": "<jwt>", "token_type": "bearer", "account": {...}}.

    Example:
        return await _auth_response(owner)   # end of signup / signin
    """
    out = await _full_account_out(acc)
    token = create_access_token(str(acc["id"]), acc["email"], acc["account_type"], out.role)
    return AuthOut(access_token=token, account=out)


@router.post("/signup", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn):
    """
    POST /api/accounts/signup — create an individual or business account and sign it in.

    Consultants (emails in CONSULTANT_EMAILS) must also send the right
    `access_code`; their consultant row is created after the account. Other
    sign-ups go through `create_client_account` with "default", so ticking
    "I'd like a consultant" assigns the default consultant.

    Args:
        body: SignupIn, the validated sign-up form (see api/models/accounts.py).

    Returns:
        AuthOut: the new login token and the account (HTTP 201).

    Raises:
        HTTPException: 403 for a consultant email without the right access code;
            400 for unknown service ids; 409 when an email already has an account.
            (Malformed fields are rejected with 422 before this runs.)

    Example:
        POST /api/accounts/signup
        {"account_type": "individual", "first_name": "Ana", "last_name": "Lee",
         "email": "ana@example.com"}
        -> 201 {"access_token": "eyJ...", "token_type": "bearer",
                "account": {"id": "...", "role": "client", ...}}
    """
    is_consultant = consultants.is_consultant_email(body.email)
    if is_consultant:
        consultants.check_access_code(body.access_code)   # 403 without the right code
    password = auth.check_password_rules(body.password)  # 422 if missing or too short
    await _check_new_signup(body)                          # 400/409 BEFORE the password is registered
    creds = await auth.register_password(body.email, password)
    owner = await create_client_account(body, consultant_id=None if is_consultant else "default", credentials=creds)
    if is_consultant:
        await consultants.register(owner)
    if creds.needs_confirmation:
        return await _confirm_email_response(owner)
    return await _auth_response(owner)


async def _confirm_email_response(acc: dict) -> AuthOut:
    """
    Reply for a sign-up that must confirm its email first (no token yet).

    Happens only in Supabase mode with Supabase's "Confirm email" setting on.

    Args:
        acc: dict, the account row just created.

    Returns:
        AuthOut: needs_confirmation True, no access token, and a message.
    """
    return AuthOut(account=await _full_account_out(acc), needs_confirmation=True,
                   message=f"Almost done: we emailed a confirmation link to {acc['email']}. "
                           "Open it, then come back and sign in.")


async def _check_new_signup(body: SignupIn) -> None:
    """
    Refuse a sign-up before anything is written: unknown services or taken emails.

    Args:
        body: SignupIn, the validated sign-up form.

    Returns:
        None: returns quietly when the sign-up can go ahead.

    Raises:
        HTTPException: 400 "Unknown service id(s): ..."; 409 when the owner's or a
            colleague's email already has an account (one email = one account).
    """
    unknown = [s for s in body.service_ids if s not in {svc["id"] for svc in catalog()}]
    if unknown:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unknown service id(s): {', '.join(unknown)}")
    for email in [body.email, *(m.email for m in body.team_members)]:
        if await account_store.get_account_by_email(email):
            who = "You already have" if email == body.email else f"{email} already has"
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"{who} an account. Each email address can only be used once"
                       + (" — please sign in instead." if email == body.email else "."),
            )


async def create_client_account(body: SignupIn, consultant_id: str | None,
                                credentials: auth.Credentials | None = None) -> dict:
    """
    Create the account (+ team members, + services on the plan). Shared by
    signup and by a consultant adding a client (api/routers/consultants.py).
    `consultant_id`: "default" = the default consultant if they asked for one;
    a consultant's id = assign to them; None = no consultant.

    Order of work (all checks happen before anything is written, so a failure
    never leaves a half-created sign-up):
    1. Every service id must be in the catalog.
    2. No email (owner or team member) may already have an account.
    3. The location id is looked up; "wants a consultant" is True when ticked
       or when a specific consultant is assigning the client to themselves.
    4. The owner account is created, then each team member as "invited".
    5. Picked services are recorded as interest (they don't unlock tools).

    Args:
        body: SignupIn, the validated sign-up form.
        consultant_id: str | None, "default", a consultant's id, or None (see above).
        credentials: auth.Credentials | None, the password registered at sign-up;
            None when a consultant adds a client (they choose one at first sign-in).

    Returns:
        dict: the newly created owner account row.

    Raises:
        HTTPException: 400 "Unknown service id(s): ..."; 409 when an email is
            already used (checked up front, and again if the store reports a
            clash); 409 from `_add_member` if a colleague's email is taken.

    Example:
        owner = await create_client_account(body, consultant_id="default")
        owner["email"]   # -> "ana@example.com"
    """
    # Check EVERY email before creating anything, so a taken address never
    # leaves a half-created signup behind (one email = one account).
    await _check_new_signup(body)
    place = locations.get(body.location_id) if body.location_id else None
    wants = body.wants_consultant or (consultant_id not in (None, "default"))
    if consultant_id == "default":
        default = await consultants.default_consultant() if body.wants_consultant else None
        consultant_id = str(default["id"]) if default else None
    try:
        owner = await account_store.create_account(
            account_type=body.account_type,
            first_name=body.first_name.strip(),
            last_name=body.last_name.strip(),
            email=body.email,
            company_name=(body.company_name or "").strip() or None,
            industry_supersector=body.industry_supersector,
            naics_sector=body.naics_sector,
            naics_subsector=body.naics_subsector,
            company_size=body.company_size,
            location=place,
            industry_other=body.industry_other,
            wants_consultant=wants,
            consultant_id=consultant_id,
            auth_user_id=credentials.auth_user_id if credentials else None,
            password_hash=credentials.password_hash if credentials else None,
        )
    except account_store.AccountExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc))

    # Business signups can invite colleagues right away. They are created as
    # "invited" accounts attached to the owner's team.
    for member in body.team_members:
        await _add_member(owner, member)

    # Projects picked on "Your needs": recorded as interest for the consultant
    # (tools unlock only with a package — api/services/entitlements.py).
    if body.service_ids:
        await entitlements.add_services(owner, body.service_ids, source="signup")
    return owner


@router.post("/signin", response_model=AuthOut)
async def signin(body: SigninIn):
    """
    POST /api/accounts/signin — check the email + password and issue a new token.

    Consultant emails must also send the access code; their consultant row is
    (re)registered on each sign-in, so the default consultant picks up any
    clients waiting for one. The last-login time is updated.

    FIRST SIGN-IN FOR AN ACCOUNT MADE FOR SOMEONE: a colleague added to a team,
    or a client a consultant added, has no password yet. Their first sign-in
    SETS the password typed (and marks the account active). ⚠️ Until invite
    emails exist, whoever signs in first with that email chooses the password —
    sign in for them promptly, or tell them to (docs/ROADMAP.md).

    Args:
        body: SigninIn, the email, password (and, for consultants, the access code).

    Returns:
        AuthOut: a fresh login token and the account (or, for a first sign-in
        that Supabase must confirm by email, needs_confirmation and no token).

    Raises:
        HTTPException: 403 for a consultant email without the right code;
            401 "Wrong email or password."; 403 while Supabase waits for the
            email to be confirmed; 422 for a too-short first password.

    Example:
        POST /api/accounts/signin
        {"email": "ana@example.com", "password": "correct horse"}
        -> 200 {"access_token": "eyJ...", "token_type": "bearer", "account": {...}}
    """
    if consultants.is_consultant_email(body.email):
        consultants.check_access_code(body.access_code)   # 403 without the right code
    acc = await account_store.get_account_by_email(body.email)
    if acc is None:
        # Same message as a wrong password, so the form doesn't reveal who has an account.
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            detail=f"{auth.WRONG_PASSWORD} New here? Create an account instead.")
    if auth.has_password(acc):
        await auth.check_password(acc, body.password)
    else:
        # First sign-in for an account made for this person: the password typed becomes theirs.
        creds = await auth.register_password(acc["email"], auth.check_password_rules(body.password))
        await account_store.set_credentials(str(acc["id"]), auth_user_id=creds.auth_user_id,
                                            password_hash=creds.password_hash)
        acc = await account_store.get_account(str(acc["id"]))
        if creds.needs_confirmation:
            return await _confirm_email_response(acc)
    await account_store.touch_last_login(str(acc["id"]))
    if consultants.is_consultant_email(body.email):
        await consultants.register(acc)
    return await _auth_response(acc)


@router.get("/me", response_model=AccountOut)
async def me(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/accounts/me — who am I? Returns the signed-in account.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        AccountOut: the caller's account, with role and consultant name.

    Raises:
        HTTPException: 401 when no/invalid token, or when the account is gone
            (ACCOUNT_GONE — e.g. an in-memory test server restarted).

    Example:
        GET /api/accounts/me
        Authorization: Bearer eyJ...
        -> 200 {"id": "...", "first_name": "Ana", "role": "client", ...}
    """
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    return await _full_account_out(acc)


@router.get("/team")
async def my_team(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/accounts/team — list the caller's business team.

    Any team member (owner or colleague) may view the team.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: {"team_id": str, "name": str | None, "members": [AccountOut, ...]}.

    Raises:
        HTTPException: 401 without a valid token; 404 "This account is not part
            of a team." for individuals or unknown accounts.

    Example:
        GET /api/accounts/team
        Authorization: Bearer eyJ...
        -> 200 {"team_id": "...", "name": "Rise Bakery", "members": [{...}, {...}]}
    """
    acc = await account_store.get_account(token["sub"])
    if acc is None or not acc.get("team_id"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="This account is not part of a team.")
    team_id = str(acc["team_id"])
    team = await account_store.get_team(team_id)
    members = await account_store.list_team_members(team_id)
    return {
        "team_id": team_id,
        "name": team["name"] if team else None,
        "members": [_account_out(m) for m in members],
    }


@router.post("/team/members", response_model=AccountOut, status_code=status.HTTP_201_CREATED)
async def add_team_member(body: TeamMemberIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/accounts/team/members — add a colleague to the caller's team (owners only).

    Args:
        body: TeamMemberIn, the colleague's first name, last name and email.
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        AccountOut: the new "invited" colleague account (HTTP 201).

    Raises:
        HTTPException: 401 without a valid token; 403 "Only the team owner can
            add members."; 409 when the colleague's email already has an account.

    Example:
        POST /api/accounts/team/members
        Authorization: Bearer eyJ...
        {"first_name": "Bo", "last_name": "Diaz", "email": "bo@example.com"}
        -> 201 {"id": "...", "team_role": "member", "status": "invited", ...}
    """
    owner = await account_store.get_account(token["sub"])
    if owner is None or owner.get("team_role") != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can add members.")
    return _account_out(await _add_member(owner, body))


async def _add_member(owner: dict, member: TeamMemberIn) -> dict:
    """
    Create an 'invited' business account on the owner's team.

    The colleague inherits the owner's company name, industry and company
    size, gets team_role "member" and status "invited".

    Args:
        owner: dict, the team owner's account row (must have a team_id).
        member: TeamMemberIn, the colleague's validated name and email.

    Returns:
        dict: the new colleague's account row.

    Raises:
        HTTPException: 409 "<email> already has an account. Team transfers are
            not supported yet."

    Example:
        bo = await _add_member(owner, TeamMemberIn(first_name="Bo", last_name="Diaz",
                                                   email="bo@example.com"))
        bo["status"]   # -> "invited"
    """
    try:
        return await account_store.create_account(
            account_type="business",
            first_name=member.first_name.strip(),
            last_name=member.last_name.strip(),
            email=member.email,
            company_name=owner.get("company_name"),
            team_id=str(owner["team_id"]),
            team_role="member",
            status="invited",
            # Colleagues share the business's industry.
            industry_supersector=owner.get("industry_supersector"),
            naics_sector=owner.get("naics_sector"),
            naics_subsector=owner.get("naics_subsector"),
            company_size=owner.get("company_size"),
        )
    except account_store.AccountExistsError:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"{member.email} already has an account. Team transfers are not supported yet.",
        )


async def _services_out(ctx: dict) -> dict:
    """
    Build the workspace's "what's on my plan" reply (shared by GET and POST /services).

    Gathers, for the account the request is FOR (which may be a consultant's
    client): active services, whether this person may change the plan (only
    individuals and team owners), the full service catalog flagged active or
    not, consulting interests, live subscriptions, plans on sale, whether
    Stripe is on, included downloads, whether AI may be added (stored data
    exists) and the consulting rate / free-call status.

    Args:
        ctx: dict, the acting context from `get_acting_context`
            ({"account", "consultant", "acting"}).

    Returns:
        dict: keys "active", "can_add", "services", "interests", "account",
        "acting", "subscriptions", "plans", "billing", "ai_ready", "downloads_included",
        "consulting" (mirrored in web/lib/types.ts).

    Example:
        data = await _services_out(ctx)
        data["can_add"], data["billing"]   # -> (True, {"stripe": False})
    """
    acc = ctx["account"]
    # A consultant (own workspace or a client's) also sees services not launched
    # yet (AI) as a preview, so they can test all seven.
    active = await entitlements.active_services(acc, preview_all=ctx["consultant"] is not None)
    holder_id = await entitlements.plan_holder_id(acc)
    subs = await account_store.list_subscriptions(holder_id)
    return {
        "active": active,
        # True when this person may add services (individuals and team owners).
        "can_add": holder_id == str(acc["id"]),
        "services": [{**s, "active": s["id"] in active} for s in catalog()],
        # Projects they'd like a consultant's help with (interest only).
        "interests": await entitlements.interests(acc),
        # Whose workspace this is (a consultant may be working on a client's).
        "account": (await _full_account_out(acc)).model_dump(),
        "acting": ctx["acting"],
        # Live subscriptions (kept in sync by Stripe) and the plans on sale.
        "subscriptions": [_subscription_out(s) for s in subs],
        "plans": subscription_catalog(),
        "billing": {"stripe": billing.stripe_enabled()},   # False = test mode, nothing is charged
        # AI can be added only once data is stored (fact.account_data) — billing.ai_ready.
        "ai_ready": await billing.ai_ready(await entitlements.plan_holder(acc)),
        "downloads_included": await entitlements.downloads_included(acc),
        "consulting": {
            "rate_per_hour_usd": pricing.consulting_rate(),
            "free_minutes": pricing.FREE_CONSULTATION_MINUTES,
            # Free call used up? Which booking link to show? (api/services/consultations.py)
            **(await consultations.summary(acc)),
        },
    }


def _subscription_out(s: dict) -> dict:
    """
    One subscription: its tools, interval, status and what it costs per period.

    Items whose plan id is no longer on sale are ignored. The price is
    recomputed from today's price list (`pricing.selection_price`), and the
    period end is sent as an ISO date ("YYYY-MM-DD").

    Args:
        s: dict, a subscription row with its "items" list, "billing_interval",
            "status" and optional "current_period_end" (datetime).

    Returns:
        dict: {"id", "plan_ids", "names", "interval", "status", "total_usd",
        "cancel_at_period_end", "current_period_end"}.

    Example:
        _subscription_out(sub)
        # -> {"id": "...", "plan_ids": ["data_cleaning"], "interval": "month",
        #     "status": "active", "total_usd": ..., "current_period_end": "2026-11-15", ...}
    """
    end = s.get("current_period_end")
    plan_ids = [i["plan_id"] for i in s["items"] if i["plan_id"] in pricing.SUBSCRIPTIONS_BY_ID]
    price = pricing.selection_price(plan_ids, s["billing_interval"]) if plan_ids else {"total_usd": 0}
    return {
        "id": str(s["id"]),
        "plan_ids": plan_ids,
        "names": [pricing.SUBSCRIPTIONS_BY_ID[p].name for p in plan_ids],
        "interval": s["billing_interval"], "status": s["status"],
        "total_usd": price["total_usd"],
        "cancel_at_period_end": bool(s.get("cancel_at_period_end")),
        "current_period_end": end.date().isoformat() if end else None,
    }


@router.get("/services")
async def my_services(ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    GET /api/accounts/services — services on my plan plus the full catalog.

    A consultant may send `X-Acting-As: <client id>` to see an assigned client's plan.

    Args:
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: the plan summary built by `_services_out`.

    Raises:
        HTTPException: 401/403/404 from `get_acting_context`.

    Example:
        GET /api/accounts/services
        Authorization: Bearer eyJ...
        -> 200 {"active": ["data_cleaning"], "can_add": true, "services": [...], ...}
    """
    return await _services_out(ctx)


@router.post("/services")
async def add_service(body: AddServiceIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/accounts/services — record interest in a project with a consultant.

    This does NOT unlock tools (only a paid package does; see
    api/services/entitlements.py). Only the plan holder (individual or team
    owner) — or their consultant acting for them — may add.

    Args:
        body: AddServiceIn, the service id to add.
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: the updated plan summary built by `_services_out`.

    Raises:
        HTTPException: 403 when the caller isn't the plan holder; 400 for an
            unknown service id; plus 401/403/404 from `get_acting_context`.

    Example:
        POST /api/accounts/services
        Authorization: Bearer eyJ...
        {"service_id": "data_engineering"}
        -> 200 {"active": [...], "interests": ["data_engineering"], ...}
    """
    acc = ctx["account"]
    try:
        await entitlements.add_services(acc, [body.service_id])
    except entitlements.NotPlanOwnerError as exc:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return await _services_out(ctx)
