"""
Audit log for HEALTHCARE clients: who touched their data, what they did, and when.

WHAT THIS FILE DOES
-------------------
Healthcare data (HIPAA) needs a record of every access. Writing that for every
client would cost database space and time for no reason, so it's ONLY for
accounts marked healthcare (dim.accounts.is_healthcare): switched on when an
admin APPROVES the business's HIPAA request (api/services/hipaa.py — a NAICS 62
"Health Care" sign-up asks automatically), or by an admin's switch (Admin ->
Testers list -> Healthcare). For everyone else record() returns at once and
nothing is written.

WHAT IS RECORDED (dim.audit_log)
--------------------------------
One row per access: when, whose data (the plan holder), who (their account,
and their role: owner / developer / member / consultant / admin), what:

    sign_in        a person on that team signed in
    view_stored    stored data was opened ("Use my stored data", the summary)
    export         stored data was downloaded as CSV
    load           data was loaded into storage           (rows, source name)
    delete_stored  stored data was deleted
    download       a cleaned file was downloaded           (file name, rows)
    connect / sync / disconnect / view_live   connected tools (QuickBooks, ...)
    ai_question    Beacon was asked about the data         (never the answer)

`detail` holds names and counts only — never the data itself. The owner (and
admins) read it in Settings -> Activity log, or download it as CSV.

  record(ctx, action, detail)   from an endpoint's acting context
  record_sign_in(account)       from sign-in
  for_account(holder_id)        the log, newest first
"""

from __future__ import annotations

from api.services import account_store
from api.services.entitlements import is_admin, plan_holder

ACTIONS = ("sign_in", "view_stored", "export", "load", "delete_stored", "download", "connect", "sync",
           "disconnect", "view_live", "ai_question")


def _role(ctx: dict) -> str:
    """Who did it, as a role: admin / consultant when working on a client, else their team role."""
    if ctx.get("acting"):
        return "admin" if ctx.get("admin") else "consultant"
    return ctx["account"].get("team_role") or "client"


async def record(ctx: dict, action: str, detail: dict | None = None) -> None:
    """
    Write one audit row IF the account is a healthcare client (otherwise nothing happens).

    Args:
        ctx: dict, the acting context ({"account", "consultant", "acting", "admin"}).
        action: str, one of ACTIONS.
        detail: dict | None, names and counts only (e.g. {"source": "visits.csv", "rows": 1200}).

    Returns:
        None.

    Example:
        >>> await record(ctx, "export", {"source": "visits.csv", "rows": 1200})
    """
    holder = await plan_holder(ctx["account"])
    if not holder.get("is_healthcare"):
        return
    actor = ctx["consultant"]["account_id"] if ctx.get("acting") and ctx.get("consultant") else ctx["account"]["id"]
    await account_store.add_audit(str(holder["id"]), str(actor), _role(ctx), action, detail or {})


async def record_sign_in(account: dict) -> None:
    """Write a sign_in row when someone on a healthcare client's team signs in."""
    holder = await plan_holder(account)
    if holder.get("is_healthcare"):
        role = "admin" if is_admin(account) else account.get("team_role") or "client"
        await account_store.add_audit(str(holder["id"]), str(account["id"]), role, "sign_in", {})


async def for_account(holder_id: str, limit: int = 200) -> list[dict]:
    """
    The audit log for one healthcare client, newest first.

    Args:
        holder_id: str, the plan holder's account id.
        limit: int, how many rows (up to 5,000).

    Returns:
        list[dict]: {"at" (ISO), "action", "actor_name", "actor_email", "actor_role", "detail"}.
    """
    return [{"at": r["at"].isoformat(), "action": r["action"], "actor_name": r.get("actor_name"),
             "actor_email": r.get("actor_email"), "actor_role": r["actor_role"], "detail": r.get("detail") or {}}
            for r in await account_store.list_audit(holder_id, limit)]
