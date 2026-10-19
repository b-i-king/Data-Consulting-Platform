"""
Consultants: who they are, how full they are, which clients they manage, and
who may act for whom.

WHAT THIS FILE DOES
-------------------
The platform serves two kinds of clients:
  * DO IT YOURSELF — they sign up and use the tools themselves.
  * DONE FOR YOU   — they tick "I'd like a consultant to do this for me", pick
                     a consultant by name (or let us pick), and choose a
                     one-time analysis or monthly help. The consultant signs in
                     and does the work in their workspace.

WHO IS A CONSULTANT? (no access codes)
--------------------------------------
  * ADMINS are the ADMIN_EMAILS addresses (normally just the owner). An admin
    is always a consultant; the first admin is the DEFAULT consultant.
  * Anyone else goes through SCREENING (below) and waits. An admin APPROVES
    or DENIES in the Admin panel. Approval creates their dim.consultants row
    at once, so their next page load shows the consultant workspace. Nobody
    can make themselves a consultant.

  is_admin_email(email)       is this address in ADMIN_EMAILS?
  sync_roles(account)         at sign-up / sign-in: admins get their flags and
                              consultant row; approved consultants get their row
  application_status(acc)     may this person apply now? (and their last result)
  apply(account, answers)     submit the screening application
  invite(email, admin)        an admin adds an email to the invite list
  decide(account_id, ok, me)  an admin approves or denies (a reason + waiting period)
  default_consultant()        who "do it for me" clients go to when they pick nobody
  pick(consultant_id)         CAPACITY RULE: the consultant a new client gets
  directory()                 every consultant with how full they are (the picker)
  set_capacity(consultant, n) a consultant's own setting, 1 to MAX_CAPACITY
  acting_context(token, id)   THE ACCESS RULE (see below)
  active_consultant(id)       the consultant row, only while they're ACTIVE
  deactivate(id, admin)       an admin switches a consultant off; their clients
                              move to someone with room (or wait)
  reactivate(id, admin)       an admin switches them back on
  team()                      every consultant, active or not (Admin panel)

SCREENING (dim.consultant_invites, dim.consultant_applications)
---------------------------------------------------------------
  1. INVITE ONLY to start (CONSULTANT_APPLICATIONS = "invite", the default):
     an admin adds the person's email in Admin -> Consultant invites. With
     "open", anyone with an account may apply.
  2. ACCOUNT FIRST: the application is sent from a signed-in account
     (Settings -> Account -> "Work as a consultant"), never at sign-up.
  3. THE APPLICATION: the services they can deliver (of the seven), their
     hands-on data experience (EXPERIENCE_RANGES), tools, their LinkedIn
     profile (REQUIRED — how we confirm a real, verifiable person), an optional
     portfolio / work sample, a project they delivered, and the CODE_OF_CONDUCT.
  4. REVIEW (the admin; Admin panel checklist): verify who they are, check
     experience and skills against the services, read the work sample, hold a
     short screening call, then approve — or deny with one of DENIAL_REASONS
     plus PERSONAL FEEDBACK (required, MIN_FEEDBACK_CHARS). They see both, with
     HUMAN_REVIEW_NOTE: a real person decided, and it's a judgment call.
  5. WAITING PERIOD: a denial sets reapply_after = today + 3, 6 or 12 months
     (MIN_REAPPLY_MONTHS = 3 at least). Applying again before then is refused.
  Each application is its own row, so the Admin panel shows repeat applicants.
  6. ONBOARDING after approval (Settings -> Consultant; `onboarding`):
     two-step sign-in, the independent-contractor agreement (CONTRACTOR_TERMS:
     they get paid directly and pay their own taxes), the free and paid Cal.com
     booking links (the paid one connected to their own Stripe / PayPal), the
     Cal.com webhook, and their capacity. Until the required steps are done the
     workspace shows "Finish your onboarding" and their clients see the
     platform's default booking links.

DEACTIVATED CONSULTANTS
-----------------------
dim.consultants.active = false (set by an admin; deactivated_at / _by / reason
are stamped). They keep their account and sign in as a client: no consultant
workspace, can't be picked, can't open any client. Reactivating stamps
reactivated_at / _by. Admins and the default consultant can't be deactivated.

CAPACITY
--------
Each consultant takes `capacity` clients at once (default 3, at most
MAX_CAPACITY). The picker shows a fill bar: green while under half full,
yellow from half, red when full. A full consultant can't be picked; a client
who picks nobody goes to the default consultant if they have room, else to
whoever has the most room, else waits (assigned when someone has room).

THE ACCESS RULE ("row-level security" in the API)
-------------------------------------------------
Every signed-in request names who it's for:
  * no `X-Acting-As` header -> the caller's own account.
  * `X-Acting-As: <client account id>` -> allowed ONLY when the caller is a
    consultant AND that client (or their business's team owner) is assigned to
    them (accounts.consultant_id) — or the caller is an ADMIN (any client,
    just in case). Anyone else gets 403 — a client can never act as another
    client, and a consultant can never open someone else's client.
The database repeats the safe default (RLS on, no public access); see db/schema.sql.
Before real clients, add two-factor sign-in for consultants (Supabase MFA) —
docs/ROADMAP.md.
"""

from __future__ import annotations

import calendar
from datetime import date

from fastapi import HTTPException, status

from api.core.config import get_settings
from api.services import account_store, mfa, pricing
from api.services.entitlements import is_admin, plan_holder_id

MAX_CAPACITY = 10   # the most clients one consultant can take (same CHECK in db/schema.sql)
ACCOUNT_GONE = ("We couldn't find your account. If this is a test server without a database, it may "
                "have restarted and forgotten accounts — please create your account again.")


def is_admin_email(email: str) -> bool:
    """
    Tell whether an email address is listed in ADMIN_EMAILS.

    The comparison ignores letter case (the settings list is already lower-cased).

    Args:
        email: str, the address someone is signing in or signing up with.

    Returns:
        bool: True if the address belongs to an admin.

    Example:
        >>> is_admin_email("Pat@Example.com")   # ADMIN_EMAILS="pat@example.com"
        True
    """
    return email.lower() in get_settings().admin_email_list


async def register(account: dict) -> dict:
    """
    Make sure a consultant's row exists (the dim.consultants table).

    A consultant is the DEFAULT when their email is the first ADMIN_EMAILS
    address (refreshed on every call). The default consultant also picks up
    clients who asked for a consultant before anyone had room — but only as
    many as their capacity leaves free.

    Args:
        account: dict, the consultant's accounts row (needs "id", "first_name",
            "email").

    Returns:
        dict: the consultants row (with "id", "is_default", "capacity", ...).

    Example:
        >>> row = await register(pat_account)   # Pat is first in ADMIN_EMAILS
        >>> row["is_default"]
        True
    """
    admins = get_settings().admin_email_list
    consultant = await account_store.ensure_consultant(account, is_default=bool(admins) and account["email"] == admins[0])
    if consultant["is_default"]:
        free = consultant.get("capacity", account_store.DEFAULT_CAPACITY) - await account_store.count_clients(str(consultant["id"]))
        await account_store.assign_waiting_clients(str(consultant["id"]), limit=free)
    return consultant


async def sync_roles(account: dict) -> dict:
    """
    Bring an account's roles up to date at sign-up and sign-in.

      * an ADMIN_EMAILS address -> is_admin and is_consultant set, consultant row made
      * an approved consultant  -> consultant row made (if it was ever missing)

    Args:
        account: dict, the accounts row that just signed up or signed in.

    Returns:
        dict: the account row, re-read when anything changed.

    Example:
        >>> (await sync_roles(my_account))["is_admin"]   # my email is in ADMIN_EMAILS
        True
    """
    if is_admin_email(account["email"]) and not account.get("is_admin"):
        await account_store.set_admin(str(account["id"]))
        account = await account_store.get_account(str(account["id"])) or account
    if account.get("is_admin") or account.get("is_consultant"):
        await register(account)
    return account


# ── Screening: invite list, application, decision, waiting period ────────────

MIN_REAPPLY_MONTHS = 3            # a denied person waits at least this long to apply again
MIN_FEEDBACK_CHARS = 20           # a denial's personal feedback: at least a real sentence

# Hands-on data experience, as the application's choices (same ids as the CHECK in db/schema.sql).
EXPERIENCE_RANGES: dict[str, str] = {
    "lt_1": "Less than 1 year",
    "1_3": "1–3 years",
    "3_5": "3–5 years",
    "5_10": "5–10 years",
    "10_plus": "10+ years",
}

# Shown with every denial, above the reason and the personal feedback.
HUMAN_REVIEW_NOTE = ("A real person on our team read your application. This decision is a judgment call — it's "
                     "subjective, not a score, and it isn't a measure of your worth or your potential. We've "
                     "shared our honest reasons so you know what we saw, and we'd be glad to see a new "
                     "application when the waiting period ends.")
REAPPLY_CHOICES = (3, 6, 12)      # the waiting periods an admin can choose when denying

# Why an application can be turned down: a FIXED list (the person sees the
# text, so it stays fair and consistent). Same ids as the CHECK in db/schema.sql.
DENIAL_REASONS: dict[str, str] = {
    "identity_unverified": "We couldn't verify your identity or professional profile.",
    "not_enough_experience": "You don't have enough hands-on data experience for client work yet.",
    "skills_mismatch": "Your skills don't match the services our clients need right now.",
    "work_sample": "Your work sample or project description didn't show the skills the work needs.",
    "conduct": "We had concerns about data handling, confidentiality or professionalism.",
    "incomplete": "The application was incomplete or we couldn't review it.",
    "not_adding": "We aren't adding consultants for these services right now.",
}

# What every consultant agrees to before applying (the application form shows it).
CODE_OF_CONDUCT: tuple[str, ...] = (
    "Keep every client's data confidential, and use it only for the work they asked for.",
    "Work on client data only inside this platform — never copy or store it anywhere else.",
    "Be honest about your skills; take only work you can deliver well.",
    "Keep client work and payments on the platform (no taking clients off it).",
    "Two-step sign-in stays on for your account.",
)

# Why someone can't apply right now (GET /api/accounts/consultant-application).
_BLOCKED = {
    "already": "You're already a consultant.",
    "pending": "Your application is waiting for review.",
    "not_invited": ("Consultant applications are by invitation for now. If you've been invited, "
                    "sign in with the email address the invitation was sent to."),
}


def add_months(day: date, months: int) -> date:
    """
    The same day `months` later (the last day of the month when it doesn't exist).

    Args:
        day: date, the starting day.
        months: int, how many months to add.

    Returns:
        date: e.g. 11/30/2026 + 3 months -> 02/28/2027.

    Example:
        >>> add_months(date(2026, 10, 18), 3)
        datetime.date(2027, 1, 18)
    """
    total = day.month - 1 + months
    year, month = day.year + total // 12, total % 12 + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _us_date(day: date) -> str:
    """A date as US text: 01/18/2027."""
    return day.strftime("%m/%d/%Y")


async def application_status(account: dict) -> dict:
    """
    Whether this person may apply now, and what happened to their last application.

    The checks, in order: already a consultant (or admin) -> an application
    waiting -> denied and still inside the waiting period -> (invite-only mode)
    not on the invite list. Otherwise they may apply.

    Args:
        account: dict, the signed-in person's own accounts row.

    Returns:
        dict: {"can_apply": bool, "blocked": None | "already" | "pending" |
        "waiting" | "not_invited", "message": str | None, "mode": "invite" | "open",
        "invited": bool, "reapply_after": "YYYY-MM-DD" | None,
        "latest": {status, submitted_at, decided_at, denial_reason,
        denial_reason_text, decision_note, human_review_note} | None,
        "code_of_conduct": [...], "experience_ranges": [{id, label}, ...]}.

    Example:
        >>> (await application_status(kim))["blocked"]
        'waiting'
    """
    mode = get_settings().consultant_applications
    invited = await account_store.get_invite(account["email"]) is not None
    latest = await account_store.latest_application(str(account["id"]))
    blocked, message, reapply_after = None, None, None
    if account.get("is_consultant") or account.get("is_admin"):
        blocked = "already"
    elif account.get("consultant_request") == "pending":
        blocked = "pending"
    elif latest and latest["status"] == "denied" and latest.get("reapply_after") and latest["reapply_after"] > date.today():
        blocked, reapply_after = "waiting", latest["reapply_after"]
        message = f"You can apply again on {_us_date(reapply_after)}."
    elif mode == "invite" and not invited:
        blocked = "not_invited"
    message = message or _BLOCKED.get(blocked or "")
    return {
        "can_apply": blocked is None,
        "blocked": blocked,
        "message": message,
        "mode": mode,
        "invited": invited,
        "reapply_after": reapply_after.isoformat() if reapply_after else None,
        "latest": {
            "status": latest["status"],
            "submitted_at": latest["submitted_at"].isoformat(),
            "decided_at": latest["decided_at"].isoformat() if latest.get("decided_at") else None,
            "denial_reason": latest.get("denial_reason"),
            "denial_reason_text": DENIAL_REASONS.get(latest.get("denial_reason") or ""),
            "decision_note": latest.get("decision_note"),
            "human_review_note": HUMAN_REVIEW_NOTE if latest["status"] == "denied" else None,
        } if latest else None,
        "code_of_conduct": list(CODE_OF_CONDUCT),
        "experience_ranges": [{"id": k, "label": v} for k, v in EXPERIENCE_RANGES.items()],
    }


async def apply(account: dict, answers: dict) -> dict:
    """
    Submit a screening application ("I'd like to work as a consultant").

    Args:
        account: dict, the signed-in person's own accounts row (never a client's).
        answers: dict, the validated form (api/models/accounts.py ConsultantApplicationIn).

    Returns:
        dict: the account row after applying (consultant_request 'pending').

    Raises:
        HTTPException: 409 when already a consultant, already waiting, or inside
            the waiting period after a denial; 403 when applications are
            invite-only and this email isn't invited.

    Example:
        >>> (await apply(kim, answers))["consultant_request"]
        'pending'
    """
    check = await application_status(account)
    if not check["can_apply"]:
        code = status.HTTP_403_FORBIDDEN if check["blocked"] == "not_invited" else status.HTTP_409_CONFLICT
        raise HTTPException(code, detail=check["message"])
    await account_store.create_application(str(account["id"]), answers)
    return await account_store.get_account(str(account["id"])) or account


async def invite(email: str, admin: dict, note: str | None = None) -> dict:
    """
    An admin puts an email on the invite list (so that person may apply).

    Args:
        email: str, the person's email address.
        admin: dict, the admin's accounts row.
        note: str | None, a private note for admins.

    Returns:
        dict: the consultant_invites row.

    Example:
        >>> (await invite("kim@example.com", me))["email"]
        'kim@example.com'
    """
    return await account_store.add_invite(email, str(admin["id"]), (note or "").strip() or None)


# ── Onboarding (after approval) ──────────────────────────────────────────────

CONTRACTOR_TERMS_VERSION = "2026-10-19"   # change the date when CONTRACTOR_TERMS' wording changes

# The independent-contractor agreement every consultant accepts while onboarding.
CONTRACTOR_TERMS: tuple[str, ...] = (
    "You work as an independent contractor, not an employee. You choose your own hours and how you do the work.",
    "Hourly sessions: clients pay you directly through your own Cal.com paid booking, connected to the "
    "payment tool you choose (Stripe, PayPal, ...).",
    "Setup and ongoing help: clients pay through the platform, and Stripe sends your share to your connected "
    "Stripe payout account automatically. The platform keeps a service fee (shown in your onboarding); the rest "
    "is yours.",
    "You're responsible for your own taxes — federal, state and local income tax and self-employment tax. "
    "Nothing is withheld for you. Stripe may send you a tax form (1099) for payouts made through the platform.",
    "You handle any business license or insurance your city or state requires.",
    "You follow the consultant code of conduct you agreed to when you applied.",
)


def onboarding(consultant: dict, account: dict, mfa_on: bool) -> dict:
    """
    A newly approved consultant's onboarding checklist (Settings -> Consultant).

    Required steps: two-step sign-in, the independent-contractor agreement, a
    connected Stripe PAYOUT account (consulting_billing: how clients' setup and
    ongoing-help payments reach them), the free and paid Cal.com booking links,
    and the Cal.com webhook. Capacity is optional (3 clients unless they change
    it). Admins skip the contractor and payout steps (it's their platform).

    Args:
        consultant: dict, their consultants row.
        account: dict, their accounts row (admins skip the contractor step: it's their platform).
        mfa_on: bool, two-step sign-in is turned on (api/services/mfa.is_on).

    Returns:
        dict: {"steps": [{"id", "label", "done", "required"}, ...], "done": bool
        (every required step done), "contractor_terms": [...],
        "contractor_terms_version", "contractor_agreed_at"}.

    Example:
        >>> onboarding(new_consultant, account, mfa_on=True)["done"]
        False
    """
    owner = bool(account.get("is_admin"))
    agreed = consultant.get("contractor_agreed_at")
    steps = [
        {"id": "two_step", "label": "Turn on two-step sign-in (Settings → Account)", "done": mfa_on, "required": True},
        {"id": "contractor", "label": "Agree to the independent-contractor terms (your pay and your taxes)",
         "done": owner or (bool(agreed) and consultant.get("contractor_terms_version") == CONTRACTOR_TERMS_VERSION),
         "required": not owner},
        {"id": "payouts", "label": "Connect your Stripe payout account (how clients' setup and ongoing-help "
                                    f"payments reach you; the platform keeps {pricing.CONSULTING['platform_fee_percent']:g}%)",
         "done": owner or bool(consultant.get("payouts_ready_at")), "required": not owner},
        {"id": "free_link", "label": "Add your free 30-minute Cal.com booking link",
         "done": bool(consultant.get("free_booking_url")), "required": True},
        {"id": "paid_link", "label": "Add your paid session link (connected to your own Stripe or PayPal in Cal.com)",
         "done": bool(consultant.get("paid_booking_url")), "required": True},
        {"id": "webhook", "label": "Connect your Cal.com webhook (so bookings show up here)",
         "done": bool(consultant.get("calcom_webhook_secret")), "required": True},
        {"id": "capacity", "label": "Choose how many clients you take at once (optional — 3 unless you change it)",
         "done": True, "required": False},
    ]
    return {
        "steps": steps,
        "done": all(st["done"] for st in steps if st["required"]),
        "contractor_terms": list(CONTRACTOR_TERMS),
        "contractor_terms_version": CONTRACTOR_TERMS_VERSION,
        "contractor_agreed_at": agreed.isoformat() if agreed else None,
    }


async def decide(account_id: str, approved: bool, admin: dict, *, denial_reason: str | None = None,
                 note: str | None = None, wait_months: int = MIN_REAPPLY_MONTHS) -> dict:
    """
    An admin approves or denies the application that's waiting.

    Approving creates the person's dim.consultants row straight away (capacity
    3), so they can take clients the next time their workspace loads. Denying
    needs a reason from DENIAL_REASONS and personal feedback in `note` (they
    see both, with HUMAN_REVIEW_NOTE), and starts the waiting period: they may
    apply again on today + `wait_months` (3 at least).

    Args:
        account_id: str, the person who applied.
        approved: bool, True = approve, False = deny.
        admin: dict, the admin's accounts row.
        denial_reason: str | None, a DENIAL_REASONS id (required to deny).
        note: str | None, personal feedback shown to the person (required to deny,
            MIN_FEEDBACK_CHARS at least; optional when approving).
        wait_months: int, denial only: 3, 6 or 12 (REAPPLY_CHOICES).

    Returns:
        dict: the person's account row after the decision.

    Raises:
        HTTPException: 404 if the account doesn't exist; 400 if nothing is
            waiting, or a denial without a valid reason, personal feedback or
            waiting period.

    Example:
        >>> (await decide(kim_id, True, me))["is_consultant"]
        True
        >>> (await decide(lee_id, False, me, denial_reason="work_sample", wait_months=6,
        ...               note="The dashboard link was empty; share a finished one next time."))["consultant_request"]
        'denied'
    """
    person = await account_store.get_account(account_id)
    if person is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That account was not found.")
    if person.get("consultant_request") != "pending":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="This person has no application waiting.")
    reapply_after = None
    note = (note or "").strip() or None
    if not approved:
        if denial_reason not in DENIAL_REASONS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Choose why the application isn't approved.")
        if len(note or "") < MIN_FEEDBACK_CHARS:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="Write them some personal feedback (a sentence or two) so they know why.")
        if wait_months not in REAPPLY_CHOICES:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail=f"The waiting period is {', '.join(map(str, REAPPLY_CHOICES))} months.")
        reapply_after = add_months(date.today(), wait_months)
    app = await account_store.latest_application(account_id)
    if app and app["status"] == "pending":
        await account_store.decide_application(app["id"], approved=approved, admin_id=str(admin["id"]),
                                               denial_reason=None if approved else denial_reason,
                                               decision_note=note, reapply_after=reapply_after)
    else:
        # A request from before screening existed (asked at sign-up, no application row).
        await account_store.decide_consultant_request(account_id, approved, str(admin["id"]))
    person = await account_store.get_account(account_id) or person
    if approved:
        await register(person)
    return person


def load(consultant: dict) -> dict:
    """
    How full one consultant is, for the picker's fill bar (no email: clients see this).

    Args:
        consultant: dict, a consultants row with "client_count" (list_consultants).

    Returns:
        dict: {"id", "name", "capacity", "clients", "percent", "full", "is_default"}.
            percent = clients / capacity * 100 (0-100).

    Example:
        >>> load({"id": "c1", "display_name": "Kim", "capacity": 4, "client_count": 2})["percent"]
        50
    """
    capacity = consultant.get("capacity") or account_store.DEFAULT_CAPACITY
    clients = consultant.get("client_count", 0)
    return {
        "id": str(consultant["id"]),
        "name": consultant["display_name"],
        "capacity": capacity,
        "clients": clients,
        "percent": min(100, round(clients / capacity * 100)),
        "full": clients >= capacity,
        "is_default": bool(consultant.get("is_default")),
    }


async def directory() -> list[dict]:
    """
    Every active consultant with how full they are (default consultant first).

    Returns:
        list[dict]: `load(...)` for each active consultant.

    Example:
        >>> [(c["name"], c["clients"], c["capacity"]) for c in await directory()]
        [('Brandon', 1, 3), ('Kim', 3, 3)]
    """
    rows = [load(c) for c in await account_store.list_consultants()]
    return sorted(rows, key=lambda c: not c["is_default"])


async def directory_page(page: int = 1, page_size: int = 8, q: str = "") -> dict:
    """
    One page of the consultant directory (the "pick your consultant" panel).

    Consultants with room come first (default consultant first among them),
    then full ones; `q` filters by first name.

    Args:
        page: int, 1-based page number (out-of-range pages come back empty).
        page_size: int, consultants per page (1 to 50).
        q: str, part of a first name ("" = everyone).

    Returns:
        dict: {"consultants": [load(...), ...], "page", "page_size", "total", "pages"}.

    Example:
        >>> (await directory_page(1, 8))["total"]
        12
    """
    page_size = max(1, min(page_size, 50))
    everyone = [c for c in await directory() if q.strip().lower() in c["name"].lower()]
    everyone.sort(key=lambda c: (c["full"], not c["is_default"], c["name"].lower()))
    start = (max(page, 1) - 1) * page_size
    return {"consultants": everyone[start:start + page_size], "page": max(page, 1), "page_size": page_size,
            "total": len(everyone), "pages": max(1, -(-len(everyone) // page_size))}


async def default_consultant() -> dict | None:
    """
    Find the default consultant: the first ADMIN_EMAILS address's consultant row.

    The row exists only once that person has signed in at least once.

    Returns:
        dict | None: the consultants row, or None if ADMIN_EMAILS is empty or
            the first admin has not signed in yet.

    Example:
        >>> c = await default_consultant()
        >>> c["email"] if c else None
        'pat@example.com'
    """
    admins = get_settings().admin_email_list
    return await account_store.get_consultant_by_email(admins[0]) if admins else None


async def pick(consultant_id: str | None) -> str | None:
    """
    THE CAPACITY RULE: which consultant a new "do it for me" client gets.

      * a consultant chosen by name -> them, if active and not full (else 409)
      * nobody chosen -> the default consultant if they have room, else the
        active consultant with the most room, else None (the client waits and
        is assigned when the default consultant next has room)

    Args:
        consultant_id: str | None, the consultants row id the client picked.

    Returns:
        str | None: the consultants row id to assign, or None to wait.

    Raises:
        HTTPException: 404 for an unknown or inactive consultant; 409 when the
            chosen consultant is full.

    Example:
        >>> await pick(None)   # default consultant has room
        '<default consultant id>'
    """
    everyone = await directory()
    if consultant_id:
        chosen = next((c for c in everyone if c["id"] == consultant_id), None)
        if chosen is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That consultant isn't available.")
        if chosen["full"]:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail=f"{chosen['name']} is fully booked right now. Please pick someone else.")
        return chosen["id"]
    open_ = [c for c in everyone if not c["full"]]
    default = next((c for c in open_ if c["is_default"]), None)
    if default:
        return default["id"]
    open_.sort(key=lambda c: (c["clients"] / c["capacity"], c["name"]))
    return open_[0]["id"] if open_ else None


async def set_capacity(consultant: dict, capacity: int) -> dict:
    """
    A consultant sets how many clients they take at once.

    Lowering it below their current number of clients is allowed (nobody is
    dropped); they just can't be picked until they have room again.

    Args:
        consultant: dict, the consultant's own consultants row.
        capacity: int, 1 to MAX_CAPACITY.

    Returns:
        dict: `load(...)` with the new capacity.

    Raises:
        HTTPException: 400 outside 1..MAX_CAPACITY.

    Example:
        >>> (await set_capacity(kim, 5))["capacity"]
        5
    """
    if not 1 <= capacity <= MAX_CAPACITY:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail=f"Capacity must be between 1 and {MAX_CAPACITY} clients.")
    row = await account_store.set_consultant_capacity(str(consultant["id"]), capacity) or consultant
    return load({**row, "client_count": await account_store.count_clients(str(consultant["id"]))})


def client_summary(acc: dict) -> dict:
    """
    Build what the consultant's "Working on" picker shows for one client.

    The label is the company name when there is one, else the person's full name.

    Args:
        acc: dict, the client's accounts row.

    Returns:
        dict: {"id", "label", "person", "email", "account_type", "company_name",
            "location", "naics_sector", "industry_other", "consultant_plan",
            "interested_services" (what they'd like help with — service ids, for the
            picker's "Help with" filter), "last_login_at" (ISO or None, for "recently active")}.

    Example:
        >>> client_summary({"id": 7, "first_name": "Ana", "last_name": "Diaz",
        ...                 "email": "ana@bakery.com", "account_type": "business",
        ...                 "company_name": "Sunrise Bakery"})["label"]
        'Sunrise Bakery'
    """
    name = f"{acc['first_name']} {acc['last_name']}"
    return {
        "id": str(acc["id"]),
        "label": acc.get("company_name") or name,
        "person": name,
        "email": acc["email"],
        "account_type": acc["account_type"],
        "company_name": acc.get("company_name"),
        "location": acc.get("location_label"),
        "naics_sector": acc.get("naics_sector"),
        "industry_other": acc.get("industry_other"),
        "consultant_plan": acc.get("consultant_plan"),   # "one_time" or "monthly"
        # Projects picked on "Your needs" ("Projects with a consultant"): the picker filters on them.
        "interested_services": list(acc.get("interested_services") or []),
        "last_login_at": acc["last_login_at"].isoformat() if acc.get("last_login_at") else None,
    }


async def acting_context(token: dict, acting_as: str | None) -> dict:
    """
    Decide who this request is for: {"account", "consultant", "acting", "admin"}.

      account    the account whose plan, team and billing apply
      consultant the caller's consultant row (None for clients)
      acting     True when a consultant is working on a client's account
      admin      True when the CALLER is an admin (every tool free, any client)

    THE ACCESS RULE: with no `acting_as` (or the caller's own id) the request is
    for the caller. Otherwise the caller must be a consultant AND the client's plan
    holder (the client, or their business's team owner) must be assigned to that
    consultant (accounts.consultant_id).

    Args:
        token: dict, the decoded sign-in token; "sub" is the caller's account id.
        acting_as: str | None, the client account id from the `X-Acting-As` header.

    Returns:
        dict: {"account": dict, "consultant": dict | None, "acting": bool, "admin": bool}.

    Raises:
        HTTPException: 401 if the caller's account no longer exists (ACCOUNT_GONE);
            403 if the caller is not a consultant or the client is not assigned to
            them; 404 if the client account does not exist.

    Example:
        >>> ctx = await acting_context({"sub": consultant_id}, client_id)
        >>> ctx["acting"], str(ctx["account"]["id"]) == client_id
        (True, True)
    """
    me = await account_store.get_account(token["sub"])
    if me is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=ACCOUNT_GONE)
    consultant = await active_consultant(str(me["id"]))   # a deactivated consultant is a client here
    admin = is_admin(me)
    if not acting_as or acting_as == str(me["id"]):
        return {"account": me, "consultant": consultant, "acting": False, "admin": admin}
    if consultant is None:
        paused = await account_store.get_consultant_by_account(str(me["id"])) is not None
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            detail=DEACTIVATED if paused else "Only consultants can work on another account.")
    from api.services import mfa   # (mfa imports account_store only)

    mfa.require_for_staff(token, me)   # opening a client's data needs the two-step sign-in
    client = await account_store.get_account(acting_as)
    if client is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That client was not found.")
    if admin:
        return {"account": client, "consultant": consultant, "acting": True, "admin": True}
    # A business's consultant is set on its team owner; colleagues follow the owner.
    holder_id = await plan_holder_id(client)
    holder = client if holder_id == str(client["id"]) else await account_store.get_account(holder_id)
    if not holder or str(holder.get("consultant_id") or "") != str(consultant["id"]):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="That client isn't assigned to you.")
    return {"account": client, "consultant": consultant, "acting": True, "admin": False}


# ── Deactivate / reactivate (admins, Admin panel -> Consultants) ─────────────

DEACTIVATED = "Your consultant access is paused by an admin. You can still use your own account."


async def active_consultant(account_id: str) -> dict | None:
    """
    The account's consultant row if they are an ACTIVE consultant, else None.

    A deactivated consultant is treated as a normal client everywhere: no
    consultant workspace, no clients, can't act for anyone.

    Args:
        account_id: str, the account id.

    Returns:
        dict | None: the consultants row (active), or None.

    Example:
        >>> await active_consultant(kim_account_id) is None   # after deactivate(...)
        True
    """
    row = await account_store.get_consultant_by_account(account_id)
    return row if row and row.get("active", True) else None


def _iso(value) -> str | None:
    """A timestamp as ISO text (None stays None)."""
    return value.isoformat() if value else None


def team_row(consultant: dict, account: dict | None = None) -> dict:
    """
    One consultant as the Admin panel lists them (with email: admins only).

    Includes the admin's CONFIRMATION of onboarding: when they agreed to the
    independent-contractor terms (and whether that's the current wording), and
    which required onboarding steps are still left.

    Args:
        consultant: dict, a consultants row with "client_count" (list_consultants(active_only=False)).
        account: dict | None, their accounts row (for two-step sign-in and admin status).

    Returns:
        dict: load(...) plus {"email", "active", "created_at", "updated_at",
        "deactivated_at", "deactivation_reason", "reactivated_at",
        "contractor_agreed_at", "contractor_terms_current", "onboarding_done",
        "onboarding_left": [step labels]} (dates as ISO text).

    Example:
        >>> team_row(kim, kim_account)["contractor_terms_current"]
        True
    """
    account = account or {}
    steps = onboarding(consultant, account, mfa.is_on(account))
    return {**load(consultant), "email": consultant["email"], "active": bool(consultant.get("active", True)),
            "created_at": _iso(consultant.get("created_at")), "updated_at": _iso(consultant.get("updated_at")),
            "deactivated_at": _iso(consultant.get("deactivated_at")),
            "deactivation_reason": consultant.get("deactivation_reason"),
            "reactivated_at": _iso(consultant.get("reactivated_at")),
            "contractor_agreed_at": _iso(consultant.get("contractor_agreed_at")),
            "contractor_terms_current": consultant.get("contractor_terms_version") == CONTRACTOR_TERMS_VERSION,
            "onboarding_done": steps["done"],
            "onboarding_left": [st["label"] for st in steps["steps"] if st["required"] and not st["done"]]}


async def team() -> list[dict]:
    """
    Every consultant, active or not (active first, then by name), for the Admin panel.

    Returns:
        list[dict]: team_row(...) for each consultant.
    """
    rows = [team_row(c, await account_store.get_account(str(c["account_id"])))
            for c in await account_store.list_consultants(active_only=False)]
    return sorted(rows, key=lambda c: (not c["active"], not c["is_default"], c["name"].lower()))


async def deactivate(consultant_id: str, admin: dict, reason: str | None = None) -> dict:
    """
    Switch a consultant off and move their clients to someone with room.

    They keep their own account (and can sign in as a client), but can't be
    picked, can't open any client, and lose consultant mode. Each of their
    clients is reassigned by the CAPACITY RULE (pick: the default consultant if
    they have room, else whoever has the most room) or, if everyone is full,
    waits for a consultant (assigned when someone has room).

    Args:
        consultant_id: str, the consultants row id.
        admin: dict, the admin's accounts row.
        reason: str | None, an optional short note ("On leave until March").

    Returns:
        dict: {"moved": n clients reassigned, "waiting": n clients now waiting,
        "help_ended": [...] — clients whose ongoing help (paid through the platform)
        ended now: refund their unused time in Stripe (consulting_billing.end_for_consultant)}.

    Raises:
        HTTPException: 404 unknown consultant; 400 for the default consultant or
            an admin (they can't be switched off); 409 already deactivated.

    Example:
        >>> await deactivate(kim_id, me, "Left the company")
        {'moved': 2, 'waiting': 0}
    """
    row = await account_store.get_consultant(consultant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That consultant wasn't found.")
    owner = await account_store.get_account(str(row["account_id"]))
    if row.get("is_default") or is_admin(owner):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Admins and the default consultant can't be deactivated.")
    if not row.get("active", True):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That consultant is already deactivated.")
    note = (reason or "").strip()[:300] or None
    await account_store.set_consultant_active(consultant_id, False, str(admin["id"]), note)
    from api.services import consulting_billing   # imported here: it imports this file's neighbors
    help_ended = await consulting_billing.end_for_consultant(consultant_id, "consultant_deactivated")
    moved = waiting = 0
    for client in await account_store.list_clients(consultant_id):
        new = await pick(None)   # they are inactive now, so pick() never gives them back
        await account_store.set_consultant_for(str(client["id"]), new, client.get("consultant_plan"))
        moved, waiting = (moved + 1, waiting) if new else (moved, waiting + 1)
    return {"moved": moved, "waiting": waiting, "help_ended": help_ended}


async def reactivate(consultant_id: str, admin: dict) -> dict:
    """
    Switch a consultant back on. Clients waiting for a consultant are given to them (up to their room).

    Clients moved away when they were deactivated stay with their new consultant.

    Args:
        consultant_id: str, the consultants row id.
        admin: dict, the admin's accounts row.

    Returns:
        dict: {"assigned": n waiting clients given to them}.

    Raises:
        HTTPException: 404 unknown consultant; 409 already active.

    Example:
        >>> await reactivate(kim_id, me)
        {'assigned': 1}
    """
    row = await account_store.get_consultant(consultant_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That consultant wasn't found.")
    if row.get("active", True):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="That consultant is already active.")
    await account_store.set_consultant_active(consultant_id, True, str(admin["id"]))
    room = (row.get("capacity") or account_store.DEFAULT_CAPACITY) - await account_store.count_clients(consultant_id)
    return {"assigned": await account_store.assign_waiting_clients(consultant_id, room)}
