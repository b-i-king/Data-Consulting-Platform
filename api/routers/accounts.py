"""
Account endpoints.

    POST /api/accounts/signup         create an individual or business account (email + password)
    POST /api/accounts/signin         check email + password, get a new token
    GET  /api/accounts/me             who am I? (requires token)
    GET  /api/accounts/services       services on my plan + the full catalog (requires token)
    POST /api/accounts/services       record interest in a project with a consultant (doesn't unlock tools)
    GET  /api/accounts/team           list my team (business accounts)
    POST /api/accounts/team/members   add a colleague (business owners only; 5 people at most)
    PUT  /api/accounts/team/members/{id}   the owner changes a colleague's role
    DELETE /api/accounts/team/members/{id} the owner removes a colleague
    DELETE /api/accounts/me           delete my account and EVERYTHING in it (type your email to confirm)
    POST /api/accounts/forgot-password   "Forgot password?": email a reset link (same reply for any email)
    POST /api/accounts/reset-password    set a new password from the emailed link
    POST /api/accounts/reset-link        signed in (Account page): email MYSELF a reset link
    PUT  /api/accounts/business-context  "About your business" — what dashboards are built around
    GET  /api/accounts/consultants    consultants and how full they are, one page at a time (public: the picker)
    POST /api/accounts/consultant     a signed-in client picks a consultant (one-time or monthly)
    GET  /api/accounts/consultant-application   may I apply to be a consultant? (and my last result)
    POST /api/accounts/consultant-request   the consultant screening application (an admin decides)

Guests only need an account at the DOWNLOAD step. Signup asks for:
  * individual: first name, last name, email
  * business:   first name, last name, email, company name (+ optional team members)
  * required:   the Terms of Service + Privacy Policy box (accept_terms)
  * optional:   "I'd like a consultant to do this for me" — a consultant
                picked by name (capacity permitting) and one-time or monthly help
  * NOT here:    applying to be a consultant — that's done from the account,
                 after signing up (consultant-request below)
(Subscriptions are bought after signup: api/routers/billing.py.)

CONSULTANTS sign up and sign in here like everyone else — no access code. An
ADMIN_EMAILS address is an admin and a consultant from the start; anyone
else becomes a consultant only when they're invited, apply from their account
(the screening application) and an admin approves it (api/routers/admin.py). Their token says role "consultant". The services
endpoints accept `X-Acting-As: <client id>` from a consultant to work on an
assigned client (api/services/consultants.py).

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

from fastapi import APIRouter, Depends, HTTPException, Request, status

from api.core.security import create_access_token, get_acting_context, get_current_account
from api.models.accounts import (
    AccountOut,
    AddServiceIn,
    AuthOut,
    BusinessContextIn,
    ChooseConsultantIn,
    ConsultantApplicationIn,
    DeleteAccountIn,
    ForgotPasswordIn,
    HipaaRequestIn,
    MfaCodeIn,
    MfaSigninIn,
    ResetPasswordIn,
    SigninIn,
    SignupIn,
    TeamMemberIn,
    TeamRoleIn,
)
from api.services import (account_store, audit, auth, billing, consulting_billing, hipaa, consultants, consultations,
                          dashboards, entitlements, locations, login_guard, mfa, offers, pricing, sessions, teams,
                          warehouse_store)
from api.services.pricing import catalog, subscription_catalog

# Signing up "as a consultant" is refused: an account comes first, then the application.
CONSULTANT_LATER = ("Create your account first, then apply from Settings → Account → "
                    "\"Work as a consultant\" (by invitation).")

router = APIRouter(prefix="/accounts")

# The Terms of Service / Privacy Policy version ticked at sign-up (saved on the
# account). When web/app/terms or web/app/privacy changes, bump this too.
TERMS_VERSION = "2026-10-19"   # web/app/terms (added: paying a consultant through the platform)

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
        is_admin=bool(acc.get("is_admin")),
        consultant_request=acc.get("consultant_request"),
        wants_consultant=bool(acc.get("wants_consultant")),
        consultant_plan=acc.get("consultant_plan"),
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
    # A deactivated consultant (an admin switched them off) is shown as a client.
    role = "consultant" if await consultants.active_consultant(str(acc["id"])) else "client"
    holder_id = await entitlements.plan_holder_id(acc)
    holder = acc if holder_id == str(acc["id"]) else await account_store.get_account(holder_id)
    consultant = await account_store.get_consultant(str(holder["consultant_id"])) if holder and holder.get("consultant_id") else None
    out = _account_out(acc, role, consultant["display_name"] if consultant else None)
    staff = role == "consultant" or entitlements.is_admin(acc)
    return out.model_copy(update={"mfa_enabled": mfa.is_on(acc), "mfa_required": mfa.required(acc, staff),
                                  "is_healthcare": bool((holder or acc).get("is_healthcare")),
                                  "hipaa_status": (holder or acc).get("hipaa_status")})


async def _auth_response(acc: dict, request: Request | None = None, *, with_mfa: bool = False) -> AuthOut:
    """
    Build the reply for a successful sign-up or sign-in: a new token plus the account.

    The token's role comes from the looked-up account (so a consultant gets a
    "consultant" token). A SESSION is opened for this device first
    (sessions.start: the browser's X-Device-Id header; at most 3 devices — the
    least recently used is signed out and named in `signed_out_devices`).

    Args:
        acc: dict, the account row that just signed up or signed in.
        request: Request | None, for the device id and the "Chrome on Windows" label.
        with_mfa: bool, the sign-in included the two-step code.

    Returns:
        AuthOut: {"access_token": "<jwt>", "token_type": "bearer", "account": {...}, "signed_out_devices": [...]}.

    Example:
        return await _auth_response(owner, request)   # end of signup / signin
    """
    out = await _full_account_out(acc)
    headers = request.headers if request is not None else {}
    sid, bumped = await sessions.start(str(acc["id"]), headers.get("x-device-id"), headers.get("user-agent"))
    await audit.record_sign_in(acc)
    token = create_access_token(str(acc["id"]), acc["email"], acc["account_type"], out.role, sid=sid, mfa=with_mfa)
    return AuthOut(access_token=token, account=out, signed_out_devices=bumped)


@router.post("/signup", response_model=AuthOut, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupIn, request: Request):
    """
    POST /api/accounts/signup — create an individual or business account and sign it in.

    The Terms box must be ticked. `signup_as` is the choice at the bottom of
    the form: "client", or "admin" (refused unless the email is in
    ADMIN_EMAILS). "consultant" is refused: consultants apply from their
    account after signing up (POST /api/accounts/consultant-request). An
    ADMIN_EMAILS address becomes an admin and consultant at once either way. "I'd like a consultant" assigns
    the consultant they picked, or one with room (consultants.pick).

    Args:
        body: SignupIn, the validated sign-up form (see api/models/accounts.py).

    Returns:
        AuthOut: the new login token and the account (HTTP 201).

    Raises:
        HTTPException: 400 without accept_terms, for unknown service ids, or
            for signup_as "consultant"; 403 for signup_as "admin" with an email that isn't an admin;
            409 when an email already has an account or the picked consultant
            is full; 404 for an unknown consultant.
            (Malformed fields are rejected with 422 before this runs.)

    Example:
        POST /api/accounts/signup
        {"account_type": "individual", "first_name": "Ana", "last_name": "Lee",
         "email": "ana@example.com", "password": "...", "accept_terms": true}
        -> 201 {"access_token": "eyJ...", "token_type": "bearer",
                "account": {"id": "...", "role": "client", ...}}
    """
    if not body.accept_terms:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Please accept the Terms of Service and Privacy Policy to create an account.")
    if body.signup_as == "consultant" or body.consultant_request:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=CONSULTANT_LATER)
    admin = consultants.is_admin_email(body.email)
    if body.signup_as == "admin" and not admin:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail="That email isn't on the admin list. Choose Client, or Request to be a consultant.")
    if body.signup_as != "client":
        # Consultants and admins don't hire a consultant for themselves.
        body = body.model_copy(update={"wants_consultant": False, "consultant_id": None, "consultant_plan": None})
    password = auth.check_password_rules(body.password)  # 422 if missing or too short
    await _check_new_signup(body)                          # 400/409 BEFORE the password is registered
    if body.wants_consultant and not admin:
        await consultants.pick(body.consultant_id)        # 404/409 (full) BEFORE the password is registered
    creds = await auth.register_password(body.email, password)
    owner = await create_client_account(body, consultant_id=None if admin else "pick", credentials=creds,
                                        terms_version=TERMS_VERSION, is_admin=admin)
    owner = await consultants.sync_roles(owner)
    owner = await hipaa.request_at_signup(owner)   # NAICS 62: an admin confirms before health data is stored
    if creds.needs_confirmation:
        return await _confirm_email_response(owner)
    return await _auth_response(owner, request)


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
    if body.team_members:   # the owner + colleagues must fit in one team (TEAM_MAX_PEOPLE)
        await teams.check_room(None, adding=len(body.team_members), already=1)
    for email in [body.email, *(m.email for m in body.team_members)]:
        if await account_store.get_account_by_email(email):
            who = "You already have" if email == body.email else f"{email} already has"
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"{who} an account. Each email address can only be used once"
                       + (" — please sign in instead." if email == body.email else "."),
            )


async def create_client_account(body: SignupIn, consultant_id: str | None,
                                credentials: auth.Credentials | None = None,
                                terms_version: str | None = None, is_admin: bool = False) -> dict:
    """
    Create the account (+ team members, + services on the plan). Shared by
    signup and by a consultant adding a client (api/routers/consultants.py).
    `consultant_id`: "pick" = if they asked for a consultant, the one they
    chose or one with room (consultants.pick, the capacity rule); a
    consultant's id = assign to them; None = no consultant.

    Order of work (all checks happen before anything is written, so a failure
    never leaves a half-created sign-up):
    1. Every service id must be in the catalog.
    2. No email (owner or team member) may already have an account.
    3. The location id is looked up; "wants a consultant" is True when ticked
       or when a specific consultant is assigning the client to themselves;
       the consultant is picked (capacity rule) before anything is written.
    4. The owner account is created, then each team member as "invited".
    5. Picked services are recorded as interest (they don't unlock tools).

    Args:
        body: SignupIn, the validated sign-up form.
        consultant_id: str | None, "default", a consultant's id, or None (see above).
        credentials: auth.Credentials | None, the password registered at sign-up;
            None when a consultant adds a client (they choose one at first sign-in).
        terms_version: str | None, the Terms version ticked (sign-up only).
        is_admin: bool, True for an ADMIN_EMAILS address.

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
    wants = body.wants_consultant or (consultant_id not in (None, "pick"))
    if consultant_id == "pick":
        consultant_id = await consultants.pick(body.consultant_id) if body.wants_consultant else None
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
            consultant_plan=(body.consultant_plan or "one_time") if wants else None,
            terms_version=terms_version,
            consultant_request=body.consultant_request,
            is_admin=is_admin,
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
async def signin(body: SigninIn, request: Request):
    """
    POST /api/accounts/signin — check the email + password and issue a new token.

    Roles are brought up to date on each sign-in (consultants.sync_roles): an
    ADMIN_EMAILS address gets its admin flags, and every consultant's row is
    (re)registered, so the default consultant picks up clients waiting for one
    (as many as they have room for). The last-login time is updated.

    FIRST SIGN-IN FOR AN ACCOUNT MADE FOR SOMEONE: a colleague added to a team,
    or a client a consultant added, has no password yet. Their first sign-in
    SETS the password typed (and marks the account active). ⚠️ Until invite
    emails exist, whoever signs in first with that email chooses the password —
    sign in for them promptly, or tell them to (docs/ROADMAP.md).

    Args:
        body: SigninIn, the email and password.

    Returns:
        AuthOut: a fresh login token and the account (or, for a first sign-in
        that Supabase must confirm by email, needs_confirmation and no token).

    WRONG PASSWORDS (api/services/login_guard.py): each one says how many tries
    are left; after LOGIN_MAX_ATTEMPTS (3) in a row sign-in is locked for
    LOGIN_LOCK_MINUTES (15) — a password reset unlocks it at once.

    Raises:
        HTTPException: 401 "Wrong email or password. 2 attempts left ..."; 429
            while sign-in is locked; 403 for as_consultant
            when the account isn't an approved consultant; 403 while Supabase waits for the
            email to be confirmed; 422 for a too-short first password.

    Example:
        POST /api/accounts/signin
        {"email": "ana@example.com", "password": "correct horse"}
        -> 200 {"access_token": "eyJ...", "token_type": "bearer", "account": {...}}
    """
    acc = await account_store.get_account_by_email(body.email)
    await login_guard.ensure_unlocked(acc, body.email)   # 429 while locked: the password isn't even checked
    if acc is None:
        # Counted and worded like a wrong password, so the form doesn't reveal who has an account.
        await login_guard.failed(None, body.email, HTTPException(status.HTTP_401_UNAUTHORIZED,
                                                                 detail=auth.WRONG_PASSWORD))
    if auth.has_password(acc):
        try:
            await auth.check_password(acc, body.password)
        except HTTPException as wrong:
            if wrong.status_code != status.HTTP_401_UNAUTHORIZED:
                raise                                     # e.g. email not confirmed: not a wrong password
            await login_guard.failed(acc, body.email, wrong)
    else:
        # First sign-in for an account made for this person: the password typed becomes theirs.
        creds = await auth.register_password(acc["email"], auth.check_password_rules(body.password))
        await account_store.set_credentials(str(acc["id"]), auth_user_id=creds.auth_user_id,
                                            password_hash=creds.password_hash)
        acc = await account_store.get_account(str(acc["id"]))
        if creds.needs_confirmation:
            return await _confirm_email_response(acc)
    await account_store.touch_last_login(str(acc["id"]))
    acc = await consultants.sync_roles(acc)
    if body.as_consultant and not await consultants.active_consultant(str(acc["id"])):
        waiting = acc.get("consultant_request") == "pending"
        paused = await account_store.get_consultant_by_account(str(acc["id"])) is not None
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=(
            consultants.DEACTIVATED + " Sign in as Account for now." if paused else
            "Your consultant request is still waiting for approval. Sign in as Account for now."
            if waiting else
            "This account isn't a consultant. Sign in as Account, or ask to be a consultant in Settings."))
    if mfa.is_on(acc):
        # Step two: the code from their authenticator app (POST /signin/mfa).
        return AuthOut(mfa_required=True, mfa_ticket=mfa.ticket(str(acc["id"])),
                       message="Enter the 6-digit code from your authenticator app.")
    return await _auth_response(acc, request)


@router.post("/signin/mfa", response_model=AuthOut)
async def signin_mfa(body: MfaSigninIn, request: Request):
    """
    POST /api/accounts/signin/mfa — step two of signing in: the authenticator code (or a backup code).

    Wrong codes count toward the same lockout as wrong passwords (login_guard).

    Args:
        body: MfaSigninIn, {"ticket": "<from /signin>", "code": "492039"}.
        request: Request, for the device session.

    Returns:
        AuthOut: the login token (with "mfa": true inside) and the account.

    Raises:
        HTTPException: 401 for an expired ticket or a wrong code (tries left); 429 when locked.

    Example:
        POST /api/accounts/signin/mfa  {"ticket": "eyJ...", "code": "492039"}
        -> 200 {"access_token": "eyJ...", "account": {...}}
    """
    acc = await account_store.get_account(mfa.read_ticket(body.ticket))
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Please sign in again.")
    await login_guard.ensure_unlocked(acc, acc["email"])
    if not await mfa.check(acc, body.code.strip()):
        await login_guard.failed(acc, acc["email"], HTTPException(status.HTTP_401_UNAUTHORIZED, detail=mfa.WRONG_CODE))
    await account_store.touch_last_login(str(acc["id"]))   # also clears the wrong-try count
    return await _auth_response(acc, request, with_mfa=True)


# ── Two-step sign-in: set up / turn off (api/services/mfa.py) ─────────────────

async def _me_row(token: dict) -> dict:
    """The caller's own accounts row, or 401."""
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    return acc


@router.post("/mfa/setup")
async def mfa_setup(token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/accounts/mfa/setup — start two-step sign-in: a key for the authenticator app.

    Returns:
        dict: {"secret": "JBSW...", "otpauth_url": "otpauth://totp/..."} (not on until /mfa/enable).

    Raises:
        HTTPException: 409 when it's already on.
    """
    return await mfa.setup(await _me_row(token))


@router.post("/mfa/enable")
async def mfa_enable(body: MfaCodeIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/accounts/mfa/enable — finish setup with the app's code; get the backup codes (shown once).

    The reply also has a fresh token for THIS device marked "mfa", so an admin
    or consultant can carry on without signing in again.

    Returns:
        dict: {"recovery_codes": [...10 codes...], "access_token": "eyJ..."}.

    Raises:
        HTTPException: 400 before setup or for a wrong code.
    """
    acc = await _me_row(token)
    codes = await mfa.enable(acc, body.code)
    out = await _full_account_out(acc)
    fresh = create_access_token(str(acc["id"]), acc["email"], acc["account_type"], out.role,
                                sid=token.get("sid"), mfa=True)
    await sessions.sign_out_all(str(acc["id"]), "everywhere", keep=token.get("sid"))   # others sign in with the code
    return {"recovery_codes": codes, "access_token": fresh}


@router.post("/mfa/disable")
async def mfa_disable(body: MfaCodeIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/accounts/mfa/disable — turn two-step sign-in off (with a current code). Not for staff.

    Raises:
        HTTPException: 400 for admins / consultants (required) or a wrong code.
    """
    acc = await _me_row(token)
    staff = entitlements.is_admin(acc) or await consultants.active_consultant(str(acc["id"])) is not None
    await mfa.disable(acc, body.code, staff)
    return {"message": "Two-step sign-in is off."}


# ── Signed-in devices (api/services/sessions.py) ──────────────────────────────

@router.get("/sessions")
async def my_sessions(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/accounts/sessions — "Your devices": every device signed in now.

    Returns:
        dict: {"sessions": [{"id", "device", "signed_in_at", "last_used_at", "this_device"}],
        "max_devices": 3, "idle_hours": 8}.
    """
    from api.core.config import get_settings

    s = get_settings()
    return {"sessions": await sessions.list_for(token["sub"], token.get("sid")),
            "max_devices": sessions.max_devices(), "idle_hours": s.session_idle_hours}


@router.delete("/sessions/{session_id}")
async def end_session(session_id: str, token: Annotated[dict, Depends(get_current_account)]):
    """
    DELETE /api/accounts/sessions/{id} — sign out one of my devices.

    Raises:
        HTTPException: 404 when it isn't one of my live devices.
    """
    if not await sessions.sign_out(token["sub"], session_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That device isn't signed in.")
    return {"sessions": await sessions.list_for(token["sub"], token.get("sid"))}


@router.post("/sessions/sign-out-others")
async def end_other_sessions(token: Annotated[dict, Depends(get_current_account)]):
    """POST /api/accounts/sessions/sign-out-others — sign out every device but this one."""
    count = await sessions.sign_out_all(token["sub"], "everywhere", keep=token.get("sid"))
    return {"signed_out": count, "sessions": await sessions.list_for(token["sub"], token.get("sid"))}


@router.post("/signout")
async def signout(token: Annotated[dict, Depends(get_current_account)]):
    """POST /api/accounts/signout — sign out THIS device (its token stops working at once)."""
    if token.get("sid"):
        await sessions.sign_out(token["sub"], token["sid"])
    return {"signed_out": True}


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
        dict: {"team_id": str, "name": str | None, "members": [AccountOut, ...],
        "seats": {"used", "limit"}, "roles": {role: label}, "can_manage": bool (the owner)}.

    Raises:
        HTTPException: 401 without a valid token; 404 "This account is not part
            of a team." for individuals or unknown accounts.

    Example:
        GET /api/accounts/team
        Authorization: Bearer eyJ...
        -> 200 {"team_id": "...", "name": "Rise Bakery", "members": [{...}, {...}],
                "seats": {"used": 2, "limit": 5}, "roles": {...}, "can_manage": true}
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
        "seats": {"used": len(members), "limit": await teams.seat_limit(team_id)},
        "roles": teams.ROLE_LABELS,
        "can_manage": acc.get("team_role") == "owner",
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
            add members."; 409 when the colleague's email already has an account,
            or the team already has TEAM_MAX_PEOPLE people (teams.TEAM_FULL).

    Example:
        POST /api/accounts/team/members
        Authorization: Bearer eyJ...
        {"first_name": "Bo", "last_name": "Diaz", "email": "bo@example.com", "role": "member"}
        -> 201 {"id": "...", "team_role": "member", "status": "invited", ...}
    """
    owner = await account_store.get_account(token["sub"])
    if owner is None or owner.get("team_role") != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can add members.")
    await teams.check_room(str(owner["team_id"]), adding=1)
    return _account_out(await _add_member(owner, body))


@router.put("/team/members/{member_id}", response_model=AccountOut)
async def change_team_role(member_id: str, body: TeamRoleIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    PUT /api/accounts/team/members/{id} — the owner makes a colleague a Developer or a Member (view only).

    Args:
        member_id: str, the colleague's account id.
        body: TeamRoleIn, {"role": "developer" | "member"}.
        token: dict, the decoded login token.

    Returns:
        AccountOut: the colleague with their new team_role.

    Raises:
        HTTPException: 403 for anyone but the owner; 404 not on the team.

    Example:
        PUT /api/accounts/team/members/7b1e...   {"role": "member"}
        -> 200 {"team_role": "member", ...}
    """
    owner = await account_store.get_account(token["sub"])
    if owner is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    return _account_out(await teams.set_role(owner, member_id, body.role))


@router.delete("/team/members/{member_id}")
async def remove_team_member(member_id: str, token: Annotated[dict, Depends(get_current_account)]):
    """
    DELETE /api/accounts/team/members/{id} — the owner removes a colleague (their account is deleted).

    Args:
        member_id: str, the colleague's account id.
        token: dict, the decoded login token.

    Returns:
        dict: {"removed": 1, "seats": {"used", "limit"}}.

    Raises:
        HTTPException: 403 for anyone but the owner; 404 not on the team.

    Example:
        DELETE /api/accounts/team/members/7b1e...
        -> 200 {"removed": 1, "seats": {"used": 1, "limit": 5}}
    """
    owner = await account_store.get_account(token["sub"])
    if owner is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    await teams.remove(owner, member_id)
    return {"removed": 1, "seats": await teams.seats(str(owner["team_id"]))}


async def _add_member(owner: dict, member: TeamMemberIn) -> dict:
    """
    Create an 'invited' business account on the owner's team.

    The colleague inherits the owner's company name, industry and company
    size, gets the role the owner picked (team_role "developer" by default, or
    "member" = view only) and status "invited".

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
            team_role=member.role,
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
        data["can_add"], data["billing"]   # -> (True, {"stripe": False, "tester": False})
    """
    acc = ctx["account"]
    # An admin (own workspace or a client's) gets every service free, including
    # ones not launched yet (AI) as a preview. Everyone else: what's paid for.
    active = await entitlements.active_services(acc, preview_all=ctx.get("admin", False))
    holder_id = await entitlements.plan_holder_id(acc)
    holder = acc if holder_id == str(acc["id"]) else (await account_store.get_account(holder_id) or acc)
    subs = await account_store.list_subscriptions(holder_id)
    return {
        "active": active,
        # True when this person may add services (individuals and team owners).
        "can_add": holder_id == str(acc["id"]),
        # False for a view-only team MEMBER: they see everything but change nothing (teams.py).
        "can_edit": teams.can_edit(ctx),
        # "owner" | "developer" | "member" on a business team; None for individuals.
        "team_role": acc.get("team_role"),
        "services": [{**s, "active": s["id"] in active} for s in catalog()],
        # Projects they'd like a consultant's help with (interest only).
        "interests": await entitlements.interests(acc),
        # Whose workspace this is (a consultant may be working on a client's).
        "account": (await _full_account_out(acc)).model_dump(),
        "acting": ctx["acting"],
        # Live subscriptions (kept in sync by Stripe) and the plans on sale.
        "subscriptions": [_subscription_out(s) for s in subs],
        "plans": subscription_catalog(),
        # HIPAA request (hipaa.py): while it's waiting for an admin, storing data is paused.
        "hipaa": hipaa.status_out(holder),
        # A first package's offer: a free trial (if they qualify) or today's seasonal sale (offers.py).
        "offer": await offers.offer_for(holder),
        # stripe False = test mode (or a tester): packages switch on at once, nothing is charged.
        "billing": {"stripe": billing.charges_real_money(holder), "tester": bool(holder.get("is_tester"))},
        # AI can be added only once data is stored (fact.account_data) — billing.ai_ready.
        "ai_ready": await billing.ai_ready(await entitlements.plan_holder(acc)),
        "downloads_included": ctx.get("admin", False) or await entitlements.downloads_included(acc),
        # The person looking is an admin: every tool free, nothing to buy.
        "admin": ctx.get("admin", False),
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


@router.get("/consultants")
async def consultant_directory(page: int = 1, page_size: int = 8, q: str = ""):
    """
    GET /api/accounts/consultants?page=1&page_size=8&q=ki — consultants and how full they are.

    Public (guests see it on "Your needs"): first names and client counts only,
    never emails. One page at a time, so the side panel scales to any number
    of consultants; those with room come first.

    Args:
        page: int, 1-based page.
        page_size: int, 1 to 50 (default 8).
        q: str, filter by first name.

    Returns:
        dict: {"consultants": [{"id", "name", "capacity", "clients", "percent",
        "full", "is_default"}, ...], "page", "page_size", "total", "pages"}.

    Example:
        GET /api/accounts/consultants?page=1
        -> 200 {"consultants": [{"id": "...", "name": "Brandon", "capacity": 3, "clients": 1,
                                 "percent": 33, "full": false, "is_default": true}],
                "page": 1, "page_size": 8, "total": 1, "pages": 1}
    """
    return await consultants.directory_page(page, page_size, q[:60])


@router.delete("/me")
async def delete_me(body: DeleteAccountIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    DELETE /api/accounts/me — delete my account and everything in it. Cannot be undone.

    The person types their own email to confirm. A business OWNER also deletes
    their team's colleague accounts (they share the owner's plan and data); a
    colleague deletes only their own account. Everything else goes with it
    (account_store.delete_account): stored data, subscriptions (canceled in
    Stripe first, so billing stops), usage, consents, load history, connectors,
    consultations. A consultant's clients go back to waiting for a consultant.
    Only an ANONYMOUS row per account is kept (dim.account_deletions: type,
    role, age in days, paid or not, the optional reason) — never who they were.

    Args:
        body: DeleteAccountIn, {"confirm_email": "<my email>", "reason": optional pick}.
        token: dict, the decoded login token (only your OWN account — never a
            client's, even for a consultant acting for one).

    Returns:
        dict: {"deleted": n accounts}.

    Raises:
        HTTPException: 400 when the email doesn't match; 401 when the account is gone.

    Example:
        DELETE /api/accounts/me   {"confirm_email": "ana@example.com"}
        -> 200 {"deleted": 1}
    """
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    if body.confirm_email.strip().lower() != acc["email"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Type your own email address to confirm.")
    # "Had a paid package" for the anonymous deletion count: checked BEFORE billing is canceled.
    holder = await entitlements.plan_holder(acc)
    paid = bool(await account_store.list_subscriptions(str(holder["id"]))) and not holder.get("is_tester")
    # A consultant leaving: their clients' ongoing help ends now (refund the rest in Stripe).
    if (mine := await account_store.get_consultant_by_account(str(acc["id"]))) is not None:
        await consulting_billing.end_for_consultant(str(mine["id"]), "consultant_left")
    if not teams.is_colleague(acc):
        await billing.cancel_everything(acc)              # stop Stripe billing before the rows go
        await warehouse_store.delete(acc)                 # the in-memory store too (the database cascades)
    ids = await account_store.delete_account(str(acc["id"]), reason=body.reason, had_paid_plan=paid)
    return {"deleted": len(ids)}


@router.post("/consultant")
async def choose_consultant(body: ChooseConsultantIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    POST /api/accounts/consultant — a signed-in client asks for a consultant (or changes theirs).

    Used by "Next steps" when the person was already signed in. The capacity
    rule applies (consultants.pick); keeping the same consultant only changes
    one-time / monthly. Only the plan holder (individual or team owner) may choose.

    Args:
        body: ChooseConsultantIn, the consultant picked (empty = keep mine, or
            we pick) and "one_time" or "monthly".
        ctx: dict, the acting context (injected by `get_acting_context`).

    Returns:
        dict: the updated plan summary built by `_services_out`.

    Raises:
        HTTPException: 403 for a team member or a consultant's own account;
            404 / 409 from the capacity rule.

    Example:
        POST /api/accounts/consultant
        {"consultant_id": "...", "consultant_plan": "monthly"}
        -> 200 {"account": {"consultant_name": "Brandon", "consultant_plan": "monthly", ...}, ...}
    """
    acc = ctx["account"]
    if await entitlements.plan_holder_id(acc) != str(acc["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Ask your team's owner to choose a consultant.")
    if acc.get("is_consultant"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Consultants can't hire a consultant for themselves.")
    current = str(acc["consultant_id"]) if acc.get("consultant_id") else None
    keep = current is not None and body.consultant_id in (None, "", current)
    if not keep:   # 409 while ongoing help with the current consultant is running
        await consulting_billing.can_switch(acc, body.consultant_id or None)
    chosen = current if keep else await consultants.pick(body.consultant_id)
    await account_store.set_consultant_for(str(acc["id"]), chosen, body.consultant_plan)
    ctx = {**ctx, "account": await account_store.get_account(str(acc["id"]))}
    return await _services_out(ctx)


@router.get("/consultant-application")
async def consultant_application(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/accounts/consultant-application — may I apply to be a consultant?

    Settings -> Account -> "Work as a consultant" shows the form, or why it
    can't be sent yet (waiting for review, the waiting period after a denial,
    or not invited), and the last decision with its reason.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: consultants.application_status (can_apply, blocked, message,
        reapply_after, latest, code_of_conduct, ...).

    Raises:
        HTTPException: 401 when the account is gone.

    Example:
        GET /api/accounts/consultant-application
        -> 200 {"can_apply": false, "blocked": "waiting", "message": "You can apply again on 01/18/2027.", ...}
    """
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    return await consultants.application_status(acc)


@router.post("/consultant-request", response_model=AccountOut)
async def ask_to_be_consultant(body: ConsultantApplicationIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/accounts/consultant-request — the consultant screening application.

    Only from a signed-in account, and (while applications are invite-only)
    only for an invited email. Puts the application on the admin's list
    (api/routers/admin.py); nothing changes until an admin approves.

    Args:
        body: ConsultantApplicationIn, the screening answers (services, experience,
            tools, profile link, optional work sample, a project, the code of conduct).
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        AccountOut: the caller's account, consultant_request "pending".

    Raises:
        HTTPException: 401 when the account is gone; 403 when not invited;
            409 if already a consultant, already waiting, or inside the waiting
            period after a denial; 422 for missing or malformed answers.

    Example:
        POST /api/accounts/consultant-request
        {"services": ["data_cleaning"], "years_experience": 4, "tools": "SQL, pandas",
         "profile_url": "https://www.linkedin.com/in/kim", "project_summary": "...", "agree_conduct": true}
        -> 200 {"consultant_request": "pending", ...}
    """
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    return await _full_account_out(await consultants.apply(acc, body.model_dump()))


@router.post("/forgot-password")
async def forgot_password(body: ForgotPasswordIn):
    """
    POST /api/accounts/forgot-password — email a password-reset link.

    The reply is the same whether or not the email has an account, so the
    form can't be used to find out who signed up (api/services/auth.py).

    Args:
        body: ForgotPasswordIn, {"email": "..."}.

    Returns:
        dict: {"message": auth.RESET_SENT}.

    Raises:
        HTTPException: 429 when too many links were asked for.

    Example:
        POST /api/accounts/forgot-password  {"email": "ana@example.com"}
        -> 200 {"message": "If that email has an account, we've sent it a link ..."}
    """
    await auth.send_reset_link(await account_store.get_account_by_email(body.email), body.email)
    return {"message": auth.RESET_SENT}


@router.post("/reset-password")
async def reset_password(body: ResetPasswordIn):
    """
    POST /api/accounts/reset-password — choose a new password from the emailed link.

    Args:
        body: ResetPasswordIn, {"token": "<from the link>", "password": "<new>"}.

    Returns:
        dict: {"message": "...", "email": "<the account's email>"} — the page then offers sign-in.

    Raises:
        HTTPException: 400 for an expired / used link; 422 for a password that breaks the rules.
    """
    account = await auth.reset_password(body.token.strip(), body.password)
    await sessions.sign_out_all(str(account["id"]), "password_reset")   # a stolen session ends with the old password
    return {"message": "Your password is changed. Every device was signed out — sign in with your new password.",
            "email": account["email"]}


@router.post("/reset-link")
async def reset_link(token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/accounts/reset-link — signed in (Account page): email myself a reset link.

    Returns:
        dict: {"message": "We emailed a reset link to <email>."}

    Raises:
        HTTPException: 401 when the account is gone.
    """
    acc = await account_store.get_account(token["sub"])
    if acc is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    await auth.send_reset_link(acc, acc["email"])
    return {"message": f"We emailed a link to reset your password to {acc['email']}. It works for 1 hour."}


@router.get("/hipaa")
async def hipaa_status(token: Annotated[dict, Depends(get_current_account)]):
    """GET /api/accounts/hipaa — my business's HIPAA request (status, details, the admin's note)."""
    acc = await _me_row(token)
    return hipaa.status_out(await entitlements.plan_holder(acc))


@router.post("/hipaa")
async def hipaa_request(body: HipaaRequestIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/accounts/hipaa — ask to store health information (HIPAA); an admin confirms the business.

    While it waits, storing data is paused (cleaning and analyzing still work).

    Returns:
        dict: hipaa.status_out (status "pending").

    Raises:
        HTTPException: 403 for a colleague; 400 for a bad NPI; 409 when already pending / approved.
    """
    acc = await _me_row(token)
    return hipaa.status_out(await hipaa.request(acc, body.organization, body.npi, body.note))


@router.delete("/hipaa")
async def hipaa_withdraw(token: Annotated[dict, Depends(get_current_account)]):
    """DELETE /api/accounts/hipaa — take back a waiting request (storing works again, without health data)."""
    acc = await _me_row(token)
    return hipaa.status_out(await hipaa.withdraw(acc))


@router.get("/audit")
async def audit_log(ctx: Annotated[dict, Depends(get_acting_context)], format: str = "json", limit: int = 200):  # noqa: A002
    """
    GET /api/accounts/audit — a HEALTHCARE client's activity log (who touched the data, when).

    For the plan holder (the owner, or an individual) and admins working on the
    client; colleagues and other consultants get 403. ?format=csv downloads it.

    Args:
        ctx: dict, the acting context.
        format: str, "json" or "csv".
        limit: int, how many rows (newest first; up to 5,000).

    Returns:
        dict: {"enabled": bool, "entries": [{"at", "action", "actor_name", "actor_email", "actor_role", "detail"}]}
        — or a CSV file.

    Raises:
        HTTPException: 403 for anyone but the plan holder or an admin.
    """
    holder = await entitlements.plan_holder(ctx["account"])
    if not ctx.get("admin") and (ctx.get("acting") or str(holder["id"]) != str(ctx["account"]["id"])):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the account's owner can see its activity log.")
    if not holder.get("is_healthcare"):
        return {"enabled": False, "entries": []}
    entries = await audit.for_account(str(holder["id"]), limit)
    if format == "csv":
        import csv
        import io

        from fastapi.responses import StreamingResponse

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["when_utc", "action", "who", "email", "role", "detail"])
        for e in entries:
            writer.writerow([e["at"], e["action"], e["actor_name"] or "", e["actor_email"] or "", e["actor_role"],
                             "; ".join(f"{k}={v}" for k, v in e["detail"].items())])
        return StreamingResponse(io.BytesIO(out.getvalue().encode("utf-8-sig")), media_type="text/csv",
                                 headers={"Content-Disposition": 'attachment; filename="activity_log.csv"'})
    return {"enabled": True, "entries": entries}


@router.put("/business-context")
async def set_business_context(body: BusinessContextIn, ctx: Annotated[dict, Depends(get_acting_context)]):
    """
    PUT /api/accounts/business-context — "About your business" (what dashboards are built around).

    Saved on the plan holder (a business shares it); a consultant may fill it
    in for their client.

    Args:
        body: BusinessContextIn, {"about", "goals", "key_questions": [...]}.

    Returns:
        dict: {"context": the saved context}.

    Raises:
        HTTPException: 403 for a team colleague.
    """
    holder = await entitlements.plan_holder(ctx["account"])
    if not ctx["acting"] and str(holder["id"]) != str(ctx["account"]["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the team owner can change this.")
    context = dashboards.clean_context(body.about, body.goals, body.key_questions)
    await account_store.set_business_context(str(holder["id"]), context)
    return {"context": context}
