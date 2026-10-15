"""
Which services an account has — drives the signed-in workspace's left pane.

WHAT THIS FILE DOES
-------------------
  * active_services(account)   the service ids an account can use, in the
                               order of api/services/pricing.py. Data Cleaning
                               is always included.
  * add_services(account, ids) record PROJECT INTEREST ("Projects with a
                               consultant" on Your needs) — it does NOT unlock tools.
  * interests(account)         those project interests (shown to the consultant).

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

WHAT UNLOCKS A TOOL (and what doesn't)
-------------------------------------
Only these unlock tools: a live subscription (a package of tools, or All
tools), being a consultant, or being a consultant-managed client. Data
Cleaning is always open. Services picked as "Projects with a consultant" are
INTERESTS only (table account_services): one-time work a consultant scopes
after the free 30-minute consultation.
"""

from __future__ import annotations

from api.services import account_store, billing
from api.services.pricing import SERVICES, SERVICES_BY_ID

ALWAYS_INCLUDED = "data_cleaning"


class NotPlanOwnerError(PermissionError):
    """A team member (not the owner) tried to change the team's plan."""


async def plan_holder_id(account: dict) -> str:
    """
    Find the account whose plan applies: the team owner for businesses, else the account itself.

    An individual holds their own plan. A business team member shares the team
    OWNER's plan, so their services and subscriptions come from the owner. If the
    account's team cannot be found, the account itself is used.

    Args:
        account: dict, an accounts row (needs "id"; "team_id" is optional).

    Returns:
        str: the id of the account that holds the plan.

    Example:
        >>> await plan_holder_id(colleague)          # colleague on Alice's team
        '<Alice's account id>'
        >>> await plan_holder_id(individual) == str(individual["id"])
        True
    """
    team_id = account.get("team_id")
    if team_id:
        team = await account_store.get_team(str(team_id))
        if team:
            return str(team["owner_account_id"])
    return str(account["id"])


async def gets_everything(account: dict) -> bool:
    """
    Tell whether an account gets every available service at once.

    True for consultants (they work on any service for their clients) and for
    consultant-managed clients. For a business, the consultant is set on the team
    owner, so a colleague of a consultant-managed owner also gets everything.

    Args:
        account: dict, an accounts row (needs "id"; "team_id" is optional).

    Returns:
        bool: True for consultants and consultant-managed clients; False otherwise
            (also when the plan holder's account can no longer be found).

    Example:
        >>> await gets_everything(consultant_account)
        True
    """
    if await account_store.get_consultant_by_account(str(account["id"])):
        return True
    holder_id = await plan_holder_id(account)
    holder = account if holder_id == str(account["id"]) else await account_store.get_account(holder_id)
    if not holder:
        return False
    return bool(holder.get("consultant_id"))


async def active_services(account: dict) -> list[str]:
    """
    List the service ids this account can use, in catalog order (Data Cleaning always included).

    - Consultants and consultant-managed clients get every available service.
    - Everyone else gets the services unlocked by the plan holder's live
      subscriptions, plus Data Cleaning.
    Project interests (account_services) do NOT unlock anything here.

    Args:
        account: dict, an accounts row.

    Returns:
        list[str]: service ids in the order of pricing.SERVICES.

    Example:
        >>> await active_services(new_client)   # no subscription yet
        ['data_cleaning']
    """
    if await gets_everything(account):
        return [s.id for s in SERVICES if s.available]
    holder = await plan_holder_id(account)
    unlocked = set((await billing.live_plan_services(holder))[0])     # the tools in their package
    unlocked.add(ALWAYS_INCLUDED)
    return [s.id for s in SERVICES if s.id in unlocked]


async def interests(account: dict) -> list[str]:
    """
    List the projects the client said they would like help with (catalog order).

    These are the "Projects with a consultant" picks stored in account_services
    for the plan holder. They are shown to the consultant and do not unlock any
    tools.

    Args:
        account: dict, an accounts row (a team member sees the owner's interests).

    Returns:
        list[str]: service ids in the order of pricing.SERVICES.

    Example:
        >>> await interests(client)
        ['data_visualization', 'data_science_ml']
    """
    stored = set(await account_store.list_services(await plan_holder_id(account)))
    return [s.id for s in SERVICES if s.id in stored]


async def downloads_included(account: dict) -> bool:
    """
    Tell whether downloads are free because a live plan includes Data Cleaning.

    The plan holder's live subscriptions are checked (a Data Cleaning tool or the
    All tools bundle both count). Without one, downloads are charged by runtime.

    Args:
        account: dict, an accounts row (a team member uses the owner's plan).

    Returns:
        bool: True when downloads cost nothing extra.

    Example:
        >>> await downloads_included(subscriber)   # subscribed to All tools
        True
    """
    return ALWAYS_INCLUDED in (await billing.live_plan_services(await plan_holder_id(account)))[0]


async def add_services(account: dict, service_ids: list[str], source: str = "added") -> list[str]:
    """
    Record project interest in services and return the account's interests.

    Only the plan holder (an individual, or a business's team owner) may add
    interests. Duplicate ids are removed and Data Cleaning is skipped (it is
    always included). This does NOT unlock tools — only a subscription does.

    Args:
        account: dict, the signed-in (or acted-for) accounts row.
        service_ids: list[str], service ids from pricing.SERVICES.
        source: str, where the request came from, saved with each row
            (default "added").

    Returns:
        list[str]: the account's interests after the change, in catalog order.

    Raises:
        ValueError: for unknown service ids.
        NotPlanOwnerError: when a team member (not the owner) tries to add services.

    Example:
        >>> await add_services(owner, ["data_visualization", "data_cleaning"])
        ['data_visualization']
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
    return await interests(account)
