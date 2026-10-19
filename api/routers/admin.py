"""
Admin endpoints (admins only — everyone else gets 403).

    GET  /api/admin/consultant-requests                 consultant applications (waiting
                                                        first, with the screening answers),
                                                        the invite list, and every
                                                        consultant and how full they are
    POST /api/admin/consultant-requests/{id}/approve    make them a consultant (their
                                                        dim.consultants row is created at once)
    POST /api/admin/consultant-requests/{id}/deny       say no: a reason (they see it) and a
                                                        waiting period (3, 6 or 12 months)
    POST   /api/admin/consultant-invites                add an email that may apply
    DELETE /api/admin/consultant-invites/{email}        take it off the list
    POST /api/admin/consultants/{id}/deactivate         switch a consultant off (their
                                                        clients move to someone with room)
    POST /api/admin/consultants/{id}/reactivate         switch them back on
    GET  /api/admin/accounts?q=                         plan holders, to pick testers
    POST /api/admin/accounts/{id}/tester                tester on / off (try paid tools free)
    POST /api/admin/accounts/{id}/healthcare            healthcare on / off (their data access is audited)
    GET  /api/admin/hipaa-requests                      businesses asking to store health information
    POST /api/admin/hipaa-requests/{id}/{approve|deny}  confirm (audit log on) or turn down
    POST /api/admin/accounts/{id}/sign-out              sign them out on every device
    POST /api/admin/accounts/{id}/reset-mfa             clear two-step sign-in (lost phone + backup codes)
    GET  /api/admin/prices                              every tool's live price
    PUT  /api/admin/prices/{plan_id}                    change a price or trial (Stripe + dim.plan_prices)
    GET  /api/admin/consulting-prices                   a consultant's help: setup, rate, hours rule, fee
    PUT  /api/admin/consulting-prices                   change them (dim.consulting_prices)
    GET  /api/admin/sales                               the yearly seasonal sales
    PUT  /api/admin/sales/{sale_id}                     change one (dates, percent, on / off)

An admin is an ADMIN_EMAILS address (normally just the owner). Admin status is
checked against the database on every call, never trusted from the token.
The website shows these in the workspace's Admin panel (components/workspace/AdminPanel.tsx).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from api.core.security import get_current_account
from api.core.config import get_settings
from api.models.admin import (ConsultantDecisionIn, ConsultantInviteIn, ConsultingPricesIn, DeactivateIn, HealthcareIn,
                              HipaaDecisionIn, PlanPriceIn, SeasonalSaleIn, TesterIn)
from api.services import account_store, billing, consultants, hipaa, mfa, offers, price_store, sessions
from api.services.entitlements import is_admin

router = APIRouter(prefix="/admin")


async def _admin(token: dict) -> dict:
    """
    The caller's account if they are an admin, or 403.

    Args:
        token: dict, the decoded login token (its "sub" is the account id).

    Returns:
        dict: the admin's accounts row.

    Raises:
        HTTPException: 403 "This is for admins only."

    Example:
        me = await _admin(token)
    """
    me = await account_store.get_account(token["sub"])
    if me is None or not (me.get("is_admin") or consultants.is_admin_email(me["email"])):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="This is for admins only.")
    mfa.require_for_staff(token, me)   # the Admin panel needs the two-step sign-in
    return me


def _iso(d):
    """A date or date-time as ISO text (None stays None)."""
    return d.isoformat() if d else None


def _application_out(app: dict | None) -> dict | None:
    """
    The screening answers and decision of one application, for the Admin panel.

    Args:
        app: dict | None, a consultant_applications row (with "times_applied").

    Returns:
        dict | None: {"services", "experience", "experience_label", "tools", "linkedin_url",
        "work_sample_url", "project_summary", "submitted_at", "times_applied",
        "denial_reason", "decision_note", "reapply_after"}, or None (a request
        from before screening existed).
    """
    if app is None:
        return None
    return {
        "services": app["services"],
        "experience": app["experience"],
        "experience_label": consultants.EXPERIENCE_RANGES.get(app["experience"], app["experience"]),
        "tools": app["tools"],
        "linkedin_url": app["linkedin_url"],
        "work_sample_url": app.get("work_sample_url"),
        "project_summary": app["project_summary"],
        "submitted_at": _iso(app.get("submitted_at")),
        "times_applied": app.get("times_applied", 1),
        "denial_reason": app.get("denial_reason"),
        "decision_note": app.get("decision_note"),
        "reapply_after": _iso(app.get("reapply_after")),
    }


def _request_out(acc: dict, app: dict | None = None) -> dict:
    """
    One consultant request as the Admin panel shows it.

    Args:
        acc: dict, the accounts row of the person who applied.
        app: dict | None, their latest application (None for an old request).

    Returns:
        dict: {"account_id", "name", "email", "company_name", "location",
        "status", "requested_at", "decided_at", "application"} (dates as ISO text).

    Example:
        >>> _request_out(kim, app)["application"]["experience_label"]
        '3–5 years'
    """
    iso = _iso
    return {
        "account_id": str(acc["id"]),
        "name": f"{acc['first_name']} {acc['last_name']}",
        "email": acc["email"],
        "company_name": acc.get("company_name"),
        "location": acc.get("location_label"),
        "status": acc["consultant_request"],
        "requested_at": iso(acc.get("consultant_requested_at")),
        "decided_at": iso(acc.get("consultant_decided_at")),
        "application": _application_out(app),
    }


async def _panel() -> dict:
    """
    Everything the Admin panel shows: requests, consultants, and account deletions.

    Returns:
        dict: {"requests": [_request_out, ...], "consultants": [consultants.load, ...]
        (active ones), "team": [consultants.team_row, ...] (everyone, with
        active / deactivated dates), "deletions": account_store.deletion_summary()}.
    """
    latest: dict[str, dict] = {}
    for app in await account_store.list_applications():   # newest first: keep each person's latest
        latest.setdefault(app["account_id"], app)
    return {
        "requests": [_request_out(a, latest.get(str(a["id"]))) for a in await account_store.list_consultant_requests()],
        # Screening: who may apply, and the admin's choices when denying.
        "applications_mode": get_settings().consultant_applications,
        "invites": [{"email": i["email"], "note": i.get("note"), "created_at": _iso(i.get("created_at")),
                     "has_account": await account_store.get_account_by_email(i["email"]) is not None}
                    for i in await account_store.list_invites()],
        "denial_reasons": [{"id": k, "text": v} for k, v in consultants.DENIAL_REASONS.items()],
        "reapply_choices": list(consultants.REAPPLY_CHOICES),
        "min_feedback_chars": consultants.MIN_FEEDBACK_CHARS,
        "human_review_note": consultants.HUMAN_REVIEW_NOTE,   # what the applicant sees with a denial
        "consultants": await consultants.directory(),
        "team": await consultants.team(),
        "deletions": await account_store.deletion_summary(),
        # Why clients cancel their package (Stripe's portal asks; the webhook saves it).
        "cancellations": await account_store.cancellation_summary(),
        "hipaa_waiting": sum(1 for r in await account_store.list_hipaa_requests() if r["hipaa_status"] == "pending"),
    }


@router.get("/consultant-requests")
async def consultant_requests(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/admin/consultant-requests — who asked to be a consultant, and every consultant's load.

    Args:
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: {"requests": [...], "consultants": [...]} (see `_panel`).

    Raises:
        HTTPException: 401 without a valid token; 403 for non-admins.

    Example:
        GET /api/admin/consultant-requests
        -> 200 {"requests": [{"account_id": "...", "name": "Kim Park", "status": "pending", ...}],
                "consultants": [{"name": "Brandon", "clients": 1, "capacity": 3, ...}]}
    """
    await _admin(token)
    return await _panel()


@router.post("/consultant-requests/{account_id}/{decision}")
async def decide_request(account_id: str, decision: str, token: Annotated[dict, Depends(get_current_account)],
                         body: ConsultantDecisionIn | None = None):
    """
    POST /api/admin/consultant-requests/{account_id}/approve (or /deny).

    Approving makes the person a consultant straight away (capacity 3, which
    they can change in their workspace). Denying keeps them a client, needs a
    reason they'll see, and starts the waiting period (3, 6 or 12 months)
    before they may apply again.

    Args:
        account_id: str, the person who applied.
        decision: str, "approve" or "deny".
        token: dict, the decoded login token (injected by `get_current_account`).
        body: ConsultantDecisionIn | None, {"denial_reason", "note", "wait_months"}
            (required to deny; optional note when approving).

    Returns:
        dict: the refreshed Admin panel (see `_panel`).

    Raises:
        HTTPException: 403 for non-admins; 404 for an unknown decision or
            account; 400 if nothing is waiting or a denial has no reason.

    Example:
        POST /api/admin/consultant-requests/7b1e.../approve
        -> 200 {"requests": [{"status": "approved", ...}], "consultants": [...]}
    """
    me = await _admin(token)
    if decision not in ("approve", "deny"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Use /approve or /deny.")
    body = body or ConsultantDecisionIn()
    await consultants.decide(account_id, decision == "approve", me, denial_reason=body.denial_reason,
                             note=body.note, wait_months=body.wait_months)
    return await _panel()


@router.post("/consultant-invites")
async def add_consultant_invite(body: ConsultantInviteIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/consultant-invites — let this email apply to be a consultant.

    Share the sign-up link with them yourself; they create an account with
    THIS email, then apply from Settings -> Account.

    Args:
        body: ConsultantInviteIn, {"email", "note"} (the note is for admins only).
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: the refreshed Admin panel (see `_panel`).

    Raises:
        HTTPException: 403 for non-admins; 422 for a malformed email.

    Example:
        POST /api/admin/consultant-invites  {"email": "kim@example.com", "note": "Referred by Ana"}
        -> 200 {"invites": [{"email": "kim@example.com", ...}], ...}
    """
    me = await _admin(token)
    await consultants.invite(body.email, me, body.note)
    return await _panel()


@router.delete("/consultant-invites/{email}")
async def remove_consultant_invite(email: str, token: Annotated[dict, Depends(get_current_account)]):
    """
    DELETE /api/admin/consultant-invites/{email} — take an email off the invite list.

    Stops NEW applications from that email only: an application already sent
    and an approved consultant are not affected.

    Args:
        email: str, the invited address.
        token: dict, the decoded login token (injected by `get_current_account`).

    Returns:
        dict: the refreshed Admin panel (see `_panel`).

    Raises:
        HTTPException: 403 for non-admins; 404 if that email wasn't invited.
    """
    await _admin(token)
    if not await account_store.remove_invite(email):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That email isn't on the invite list.")
    return await _panel()


# ── Consultants: deactivate / reactivate ──────────────────────────────────────

@router.post("/consultants/{consultant_id}/deactivate")
async def deactivate_consultant(consultant_id: str, body: DeactivateIn,
                                token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/consultants/{id}/deactivate — switch a consultant off (consultants.deactivate).

    Their clients move to someone with room (or wait); they keep their own account.

    Args:
        consultant_id: str, the consultants row id.
        body: DeactivateIn, {"reason": optional note}.
        token: dict, the decoded login token.

    Returns:
        dict: the refreshed panel plus "notice" ("Kim is deactivated. 2 clients moved.").

    Raises:
        HTTPException: 403 non-admins; 404 unknown; 400 admins / default consultant; 409 already off.

    Example:
        POST /api/admin/consultants/9c1d.../deactivate   {"reason": "On leave"}
        -> 200 {"notice": "Kim is deactivated. 2 clients moved to other consultants.", "team": [...], ...}
    """
    me = await _admin(token)
    result = await consultants.deactivate(consultant_id, me, body.reason)
    row = await account_store.get_consultant(consultant_id)
    notice = f"{row['display_name']} is deactivated."
    if result["moved"]:
        notice += f" {result['moved']} client{'s' if result['moved'] != 1 else ''} moved to other consultants."
    if result["waiting"]:
        notice += f" {result['waiting']} waiting for a consultant (everyone else is full)."
    if result.get("help_ended"):
        who = "; ".join(f"{h['client']} ({h['email']})" for h in result["help_ended"])
        notice += (f" Their ongoing help ended now for: {who}. Refund the unused time in the Stripe dashboard "
                   "(Payments -> the payment -> Refund, with \"Reverse transfer\" ticked).")
    return {**await _panel(), "notice": notice}


@router.post("/consultants/{consultant_id}/reactivate")
async def reactivate_consultant(consultant_id: str, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/consultants/{id}/reactivate — switch a consultant back on (consultants.reactivate).

    Args:
        consultant_id: str, the consultants row id.
        token: dict, the decoded login token.

    Returns:
        dict: the refreshed panel plus "notice".

    Raises:
        HTTPException: 403 non-admins; 404 unknown; 409 already active.

    Example:
        POST /api/admin/consultants/9c1d.../reactivate
        -> 200 {"notice": "Kim is active again.", ...}
    """
    me = await _admin(token)
    result = await consultants.reactivate(consultant_id, me)
    row = await account_store.get_consultant(consultant_id)
    notice = f"{row['display_name']} is active again."
    if result["assigned"]:
        notice += f" {result['assigned']} waiting client{'s' if result['assigned'] != 1 else ''} assigned to them."
    return {**await _panel(), "notice": notice}


# ── Testers ───────────────────────────────────────────────────────────────────

async def _person_out(acc: dict) -> dict:
    """
    One account as the Admin panel's Testers list shows it.

    Args:
        acc: dict, an accounts row (a plan holder).

    Returns:
        dict: {"id", "name", "email", "company_name", "account_type", "role"
        ("admin" | "consultant" | "client"), "is_tester", "has_plan",
        "created_at", "updated_at", "last_login_at"} (dates as ISO text).
    """
    def iso(d):
        return d.isoformat() if d else None

    role = ("admin" if is_admin(acc) else
            "consultant" if await consultants.active_consultant(str(acc["id"])) else "client")
    return {
        "id": str(acc["id"]), "name": f"{acc['first_name']} {acc['last_name']}", "email": acc["email"],
        "company_name": acc.get("company_name"), "account_type": acc["account_type"], "role": role,
        "is_tester": bool(acc.get("is_tester")),
        "is_healthcare": bool(acc.get("is_healthcare")),
        "mfa_enabled": mfa.is_on(acc),
        "has_plan": bool(await account_store.list_subscriptions(str(acc["id"]))),
        "created_at": iso(acc.get("created_at")), "updated_at": iso(acc.get("updated_at")),
        "last_login_at": iso(acc.get("last_login_at")),
    }


@router.get("/accounts")
async def people(token: Annotated[dict, Depends(get_current_account)], q: str = "", limit: int = 25):
    """
    GET /api/admin/accounts?q=bakery — plan holders (newest first), to switch testers on and off.

    Args:
        token: dict, the decoded login token.
        q: str, part of a name, email or company ("" = the newest accounts).
        limit: int, how many (1 to 100, default 25).

    Returns:
        dict: {"accounts": [_person_out, ...]}.

    Raises:
        HTTPException: 403 for non-admins.

    Example:
        GET /api/admin/accounts?q=ana
        -> 200 {"accounts": [{"name": "Ana Lee", "is_tester": false, "role": "client", ...}]}
    """
    await _admin(token)
    return {"accounts": [await _person_out(a) for a in await account_store.search_accounts(q, limit)]}


@router.post("/accounts/{account_id}/tester")
async def set_tester(account_id: str, body: TesterIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/accounts/{id}/tester — make an account a tester, or a paying client again.

    Rules are in billing.set_tester (plan holders only; not while they pay through Stripe).

    Args:
        account_id: str, the account.
        body: TesterIn, {"is_tester": true | false}.
        token: dict, the decoded login token.

    Returns:
        dict: {"account": _person_out}.

    Raises:
        HTTPException: 403 non-admins; 404 unknown account; 400 / 409 from billing.set_tester.

    Example:
        POST /api/admin/accounts/7b1e.../tester   {"is_tester": true}
        -> 200 {"account": {"is_tester": true, ...}}
    """
    await _admin(token)
    acc = await account_store.get_account(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That account wasn't found.")
    return {"account": await _person_out(await billing.set_tester(acc, body.is_tester))}


@router.post("/accounts/{account_id}/healthcare")
async def set_healthcare(account_id: str, body: HealthcareIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/accounts/{id}/healthcare — {"is_healthcare": true | false}.

    Healthcare clients get the audit log (api/services/audit.py). It's set
    automatically for NAICS sector 62 at sign-up; this switches it by hand.

    Returns:
        dict: {"account": _person_out}.

    Raises:
        HTTPException: 403 non-admins; 404 unknown account; 400 for a colleague (the owner holds it).
    """
    await _admin(token)
    acc = await account_store.get_account(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That account wasn't found.")
    if acc.get("team_role") in ("developer", "member"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Set it on the business's owner.")
    return {"account": await _person_out(await account_store.set_healthcare(account_id, body.is_healthcare) or acc)}


@router.get("/hipaa-requests")
async def hipaa_requests(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/admin/hipaa-requests — businesses asking to store health information (waiting first).

    Returns:
        dict: {"requests": [{"account_id", "name", "email", "company_name", "organization", "npi",
        "lookup_url" (NPPES registry), "note", "status", "requested_at", ...}]}.
    """
    await _admin(token)
    return {"requests": await hipaa.pending_requests()}


@router.post("/hipaa-requests/{account_id}/{decision}")
async def decide_hipaa(account_id: str, decision: str, body: HipaaDecisionIn,
                       token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/hipaa-requests/{id}/approve (or /deny) — after checking the NPI in the NPPES registry.

    Approve: storing works again and every access is audited — sign a Business Associate
    Agreement with them before patient data is uploaded. Deny: storing works again for
    ordinary business data (no patient health information, per the Terms).

    Raises:
        HTTPException: 403 non-admins; 404 unknown decision or account; 400 nothing waiting.
    """
    me = await _admin(token)
    if decision not in ("approve", "deny"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Use /approve or /deny.")
    await hipaa.decide(account_id, decision == "approve", me, body.note)
    return {"requests": await hipaa.pending_requests()}


@router.post("/accounts/{account_id}/sign-out")
async def sign_out_person(account_id: str, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/accounts/{id}/sign-out — sign someone out on every device (e.g. a lost laptop).

    Returns:
        dict: {"signed_out": n devices}.
    """
    await _admin(token)
    return {"signed_out": await sessions.sign_out_all(account_id, "admin")}


@router.post("/accounts/{account_id}/reset-mfa")
async def reset_mfa(account_id: str, token: Annotated[dict, Depends(get_current_account)]):
    """
    POST /api/admin/accounts/{id}/reset-mfa — clear two-step sign-in (they lost the phone AND the backup codes).

    They're signed out everywhere and set it up again after signing in.

    Returns:
        dict: {"account": _person_out}.
    """
    await _admin(token)
    acc = await account_store.get_account(account_id)
    if acc is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="That account wasn't found.")
    await mfa.reset(account_id)
    await sessions.sign_out_all(account_id, "admin")
    return {"account": await _person_out(await account_store.get_account(account_id))}


# ── Prices (dim.plan_prices -> Stripe -> the whole site) ─────────────────────

@router.get("/prices")
async def prices(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/admin/prices — every plan's live price, its default, and who changed it last.

    Args:
        token: dict, the decoded login token.

    Returns:
        dict: {"prices": price_store.list_prices(), "stripe": bool (Stripe is on)}.

    Raises:
        HTTPException: 403 for non-admins.

    Example:
        GET /api/admin/prices
        -> 200 {"prices": [{"id": "all_tools", "monthly_usd": 99.0, "annual_usd": 990.0, ...}], "stripe": false}
    """
    await _admin(token)
    return {"prices": await price_store.list_prices(), "stripe": billing.stripe_enabled()}


@router.put("/prices/{plan_id}")
async def change_price(plan_id: str, body: PlanPriceIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    PUT /api/admin/prices/{plan_id} — change a plan's monthly and annual price (price_store.set_price).

    Stripe is updated first (when on); current subscribers keep their price.

    Args:
        plan_id: str, e.g. "data_visualization".
        body: PlanPriceIn, {"monthly_usd": 24, "annual_usd": 240}.
        token: dict, the decoded login token.

    Returns:
        dict: {"prices": [...], "stripe": {"enabled": bool, "changes": [str, ...]}}.

    Raises:
        HTTPException: 403 non-admins; 404 unknown plan; 400 price outside the
            rules; 502 Stripe refused (nothing changed).

    Example:
        PUT /api/admin/prices/data_visualization   {"monthly_usd": 24, "annual_usd": 240}
        -> 200 {"prices": [...], "stripe": {"enabled": true, "changes": ["replace price ...: $24.00 per month", ...]}}
    """
    me = await _admin(token)
    return await price_store.set_price(plan_id, body.monthly_usd, body.annual_usd, me, trial_days=body.trial_days)


# ── Seasonal sales (dim.seasonal_sales: they repeat every year) ──────────────

@router.get("/sales")
async def sales(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/admin/sales — every yearly sale, and the one running today.

    Args:
        token: dict, the decoded login token.

    Returns:
        dict: {"sales": offers.list_sales(), "running": offers.current_sale() or None}.

    Raises:
        HTTPException: 403 for non-admins.

    Example:
        GET /api/admin/sales
        -> 200 {"sales": [{"id": "new_year", "starts_on": "01-01", "ends_on": "01-31", ...}], "running": null}
    """
    await _admin(token)
    return {"sales": await offers.list_sales(), "running": await offers.current_sale()}


@router.put("/sales/{sale_id}")
async def change_sale(sale_id: str, body: SeasonalSaleIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    PUT /api/admin/sales/{sale_id} — change a sale's name, dates (MM-DD), percent off, or switch it on / off.

    No Stripe step: the coupon is made the first time a checkout needs it.

    Args:
        sale_id: str, e.g. "year_end".
        body: SeasonalSaleIn.
        token: dict, the decoded login token.

    Returns:
        dict: {"sales": [...], "running": ...} after the change.

    Raises:
        HTTPException: 403 non-admins; 404 unknown sale; 400 bad dates.

    Example:
        PUT /api/admin/sales/year_end
        {"name": "Year-end sale", "starts_on": "11-24", "ends_on": "12-31", "percent_off": 25, "active": true}
    """
    me = await _admin(token)
    await offers.set_sale(sale_id, name=body.name, starts_on=body.starts_on, ends_on=body.ends_on,
                          percent_off=body.percent_off, active=body.active, admin=me)
    return {"sales": await offers.list_sales(), "running": await offers.current_sale()}


@router.get("/consulting-prices")
async def get_consulting_prices(token: Annotated[dict, Depends(get_current_account)]):
    """
    GET /api/admin/consulting-prices — what a consultant's help costs (Admin -> Prices).

    Returns:
        dict: price_store.consulting_prices() — {"values", "defaults", "updated_at", "example"}.
    """
    await _admin(token)
    return await price_store.consulting_prices()


@router.put("/consulting-prices")
async def put_consulting_prices(body: ConsultingPricesIn, token: Annotated[dict, Depends(get_current_account)]):
    """
    PUT /api/admin/consulting-prices — change the setup fee, hourly rate, hours rule or platform fee.

    New purchases use them at once; running ongoing help keeps the price it started at.

    Returns:
        dict: the saved numbers (price_store.consulting_prices()).

    Raises:
        HTTPException: 400 when the weekly maximum is below the Data Cleaning / Engineering hours.
    """
    me = await _admin(token)
    return await price_store.set_consulting_prices(body.model_dump(exclude_none=True), me)
