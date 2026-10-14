"""
Which services an account has — drives the signed-in workspace's left pane.

WHAT THIS FILE DOES
-------------------
  * active_services(account)   the service ids an account can use, in the
                               order of api/services/pricing.py. Data Cleaning
                               is always included.
  * add_services(account, ids) put services on the account's plan.

WHO HOLDS THE PLAN?
-------------------
An individual holds their own plan. In a business, the team OWNER holds it
and every team member shares it — so a colleague sees the same services, and
only the owner can add more.

EVERY SERVICE AT ONCE
---------------------
Two kinds of accounts get every available service (not "early access" ones):
  * consultants (they work on any service for their clients, and test the app)
  * clients assigned to a consultant ("I'd like a consultant to do this for me")

SUBSCRIPTIONS
-------------
A live subscription (table `subscriptions`, kept in sync by Stripe — see
api/services/billing.py) unlocks its plan's services ("All tools" = all of
them). A plan that includes Data Cleaning also makes downloads free
(`downloads_included`).

PLACEHOLDER UNTIL PAYMENTS
--------------------------
There's no checkout yet, so services chosen in the guest questionnaire (sent
with signup) or added in the workspace are active right away. When payments
are added, add_services() should be called from the payment confirmation
(source "payment") and the workspace's "Add" button should open checkout.
"""

from __future__ import annotations

from api.services import account_store, billing
from api.services.pricing import SERVICES, SERVICES_BY_ID

ALWAYS_INCLUDED = "data_cleaning"


class NotPlanOwnerError(PermissionError):
    """A team member (not the owner) tried to change the team's plan."""


async def plan_holder_id(account: dict) -> str:
    """The account whose plan applies: the team owner for businesses, else the account itself."""
    team_id = account.get("team_id")
    if team_id:
        team = await account_store.get_team(str(team_id))
        if team:
            return str(team["owner_account_id"])
    return str(account["id"])


async def gets_everything(account: dict) -> bool:
    """True for consultants and consultant-managed clients."""
    if await account_store.get_consultant_by_account(str(account["id"])):
        return True
    holder_id = await plan_holder_id(account)
    holder = account if holder_id == str(account["id"]) else await account_store.get_account(holder_id)
    if not holder:
        return False
    return bool(holder.get("consultant_id"))


async def active_services(account: dict) -> list[str]:
    """Service ids this account can use, in catalog order (Data Cleaning always included)."""
    if await gets_everything(account):
        return [s.id for s in SERVICES if s.available]
    holder = await plan_holder_id(account)
    stored = set(await account_store.list_services(holder))
    stored |= set((await billing.live_plan_services(holder))[0])     # from subscriptions
    stored.add(ALWAYS_INCLUDED)
    return [s.id for s in SERVICES if s.id in stored]


async def downloads_included(account: dict) -> bool:
    """True when a live plan includes Data Cleaning: downloads cost nothing extra."""
    return ALWAYS_INCLUDED in (await billing.live_plan_services(await plan_holder_id(account)))[0]


async def add_services(account: dict, service_ids: list[str], source: str = "added") -> list[str]:
    """
    Add services to the account's plan and return the new active list.
    Raises ValueError for unknown ids and NotPlanOwnerError for team members.
    """
    unknown = [s for s in service_ids if s not in SERVICES_BY_ID]
    if unknown:
        raise ValueError(f"Unknown service id(s): {', '.join(unknown)}")
    holder = await plan_holder_id(account)
    if holder != str(account["id"]):
        raise NotPlanOwnerError("Only the team owner can add services to the plan.")
    new = [s for s in dict.fromkeys(service_ids) if s != ALWAYS_INCLUDED]
    if new:
        await account_store.add_services(holder, new, source)
    return await active_services(account)
