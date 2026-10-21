"""
integration_slots — who may hold one of the limited business-tool connections.

QuickBooks, Xero and Wave give the platform a free API allowance shared by ALL
clients (Xero's free plan: 5 connected businesses; the app's own caps are in
api/core/config.py). So:

  * Connecting a business tool comes with a PAID plan (PAID_ONLY). On the free
    plan, clients download an export from the tool and upload it: the analysis
    is the same, and it uses none of the shared allowance.
  * Reading live data also needs the paid plan, so a plan that ended stops
    using the allowance at once.
  * When a tool's spots are full and a paying client wants to connect, spots
    held by accounts WITHOUT a paid plan (a plan that ended) are released
    first (`free_unpaid_slots`), so they never block paying clients.

`release` is also what "Disconnect" does: cancel our access at the tool (best
effort; for Xero, remove the organization connection, which frees one of its
spots) and delete the saved login.
"""

from __future__ import annotations

from fastapi import HTTPException

from api.services import account_store, business_tools, entitlements, oauth

BUSINESS_TOOLS = ("quickbooks", "xero", "wave")


def paid_only_message(name: str) -> str:
    """The plain-English 402 message for a free account trying to connect or read a business tool."""
    return (f"Connecting {name} comes with any paid plan. On the free plan, download an export from {name} "
            "(a report or list saved as CSV or Excel) and upload it here — the analysis is the same.")


async def release(holder_id: str, connector: str) -> None:
    """
    Disconnect one client's integration: revoke our access at the tool (best effort), then delete the login.

    Args:
        holder_id: str, the plan holder's account id.
        connector: str, e.g. "xero".
    """
    row = await account_store.get_integration(holder_id, connector)
    creds = oauth.unpack(row["credentials_encrypted"]) if row and row.get("credentials_encrypted") else None
    if creds:
        try:
            if connector == "xero":
                _, fresh = await oauth.load(holder_id, connector)
                await business_tools.xero_remove_connection(fresh["tokens"]["access_token"],
                                                            fresh["extra"].get("connection_id"))
                creds = fresh
            await oauth.revoke(connector, creds["tokens"])
        except HTTPException:
            pass   # already revoked on their side: just delete ours
    await account_store.delete_integration(holder_id, connector)


async def free_unpaid_slots(connector: str) -> int:
    """
    Release this tool's connections held by accounts without a paid plan; return how many were released.

    Only called when the tool's spots are full, so it checks at most the
    tool's maximum (5-15 accounts).

    Example:
        >>> await free_unpaid_slots("xero")    # one client's plan ended last month
        1
    """
    released = 0
    for holder_id in await account_store.list_connected(connector):
        account = await account_store.get_account(holder_id)
        if account and not await entitlements.has_paid_plan(account):
            await release(holder_id, connector)
            released += 1
    return released
