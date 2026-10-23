"""
"Connect" sign-in (OAuth 2.0) for QuickBooks, Xero and Supabase.

WHY THIS EXISTS
---------------
Instead of pasting passwords, a client clicks "Connect QuickBooks", signs in
on Intuit's own page, and Intuit sends them back with a one-time code. We
swap that code for an ACCESS TOKEN (a key that works for about an hour) and a
REFRESH TOKEN (used to get a new access token without asking them again).
The tokens are saved ENCRYPTED in dim.data_connections.credentials_encrypted —
the same Fernet encryption as database logins (api/services/cloud_sources.py).

THE FLOW (same for every provider)
----------------------------------
    1. browser  GET /api/connections/{provider}/authorize  -> {"url": "https://appcenter.intuit.com/..."}
    2. browser goes there; the client signs in and clicks "Allow"
    3. provider -> GET /api/connections/oauth/callback?code=...&state=...
    4. we check `state`, swap the code for tokens, save them encrypted, and
       send the browser back to the website (Settings -> Integrations).

`state` is an encrypted, 15-minute ticket made in step 1 that says WHO is
connecting (the client account, and the consultant if one is acting) and,
for Supabase, the PKCE code verifier. Because it is encrypted and signed, it
can't be forged or read, and nothing has to be saved between steps 1 and 3.

KEYS (fewest possible)
----------------------
One Client ID + Client Secret per provider (api/core/config.py), belonging to
the platform. A provider without keys just isn't offered yet. One redirect
URI for all of them: <API address>/api/connections/oauth/callback.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
from datetime import datetime
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status

from api.core.config import get_settings

STATE_TTL_SECONDS = 15 * 60       # the sign-in must finish within 15 minutes
REFRESH_EARLY_SECONDS = 120       # refresh an access token 2 minutes before it expires
HTTP_TIMEOUT_S = 30
# Tests swap this for httpx.MockTransport so no real provider is called.
HTTP_TRANSPORT: httpx.AsyncBaseTransport | None = None

# Each provider's sign-in addresses and what we ask permission for (READ-ONLY
# everywhere: we never change a client's books or database).
PROVIDERS: dict[str, dict] = {
    "quickbooks": {
        "name": "QuickBooks",
        "authorize_url": "https://appcenter.intuit.com/connect/oauth2",
        "token_url": "https://oauth.platform.intuit.com/oauth2/v1/tokens/bearer",
        "revoke_url": "https://developer.api.intuit.com/v2/oauth2/tokens/revoke",
        "scope": "com.intuit.quickbooks.accounting",
        "pkce": False,
    },
    "xero": {
        "name": "Xero",
        "authorize_url": "https://login.xero.com/identity/connect/authorize",
        "token_url": "https://identity.xero.com/connect/token",
        "revoke_url": "https://identity.xero.com/connect/revocation",
        # Granular, read-only scopes (Xero's rules for apps made after March 2026).
        # Xero's own Profit & Loss / Balance Sheet REPORTS need its App Partner
        # Program, so the quick look adds up invoices and bank transactions instead.
        "scope": "openid profile email offline_access accounting.invoices.read accounting.payments.read "
                 "accounting.banktransactions.read accounting.contacts.read",
        "pkce": False,
    },
    "wave": {
        "name": "Wave",
        "authorize_url": "https://api.waveapps.com/oauth2/authorize/",
        "token_url": "https://api.waveapps.com/oauth2/token/",
        "revoke_url": None,          # the business removes access in Wave's settings
        "scope": None,               # from WAVE_SCOPES (Settings), read-only by default
        "pkce": False,
        "client_auth": "body",       # Wave takes the client ID and secret as form fields
    },
    "square": {
        "name": "Square",
        # Production addresses; endpoint() swaps in Square's sandbox (SQUARE_ENVIRONMENT).
        "authorize_url": "https://connect.squareup.com/oauth2/authorize",
        "token_url": "https://connect.squareup.com/oauth2/token",
        "revoke_url": "https://connect.squareup.com/oauth2/revoke",
        # Read-only: the seller's profile, orders, payments, items, stock and customers.
        "scope": "MERCHANT_PROFILE_READ ORDERS_READ PAYMENTS_READ ITEMS_READ INVENTORY_READ CUSTOMERS_READ",
        "pkce": False,
        "client_auth": "json",       # Square takes the token request as JSON
        "extra_params": {"session": "false"},
    },
    "google_sheets": {
        "name": "Google Sheets",
        "authorize_url": "https://accounts.google.com/o/oauth2/v2/auth",
        "token_url": "https://oauth2.googleapis.com/token",
        "revoke_url": "https://oauth2.googleapis.com/revoke",
        # Read-only: we can open the client's sheets, never change them. "email" = whose account it is.
        "scope": "openid email https://www.googleapis.com/auth/spreadsheets.readonly",
        "pkce": True,
        # Google gives a refresh token (to keep reading later) only when asked for "offline" access.
        "extra_params": {"access_type": "offline", "prompt": "consent", "include_granted_scopes": "true"},
    },
    "salesforce": {
        "name": "Salesforce",
        "authorize_url": "https://login.salesforce.com/services/oauth2/authorize",
        "token_url": "https://login.salesforce.com/services/oauth2/token",
        "revoke_url": "https://login.salesforce.com/services/oauth2/revoke",
        # "api" = read the org's records (we only ever run SELECT queries); "refresh_token" = keep access.
        "scope": "api refresh_token",
        "pkce": True,
        "client_auth": "body",
    },
    "supabase": {
        "name": "Supabase",
        "authorize_url": "https://api.supabase.com/v1/oauth/authorize",
        "token_url": "https://api.supabase.com/v1/oauth/token",
        "revoke_url": None,          # the client removes access in Supabase -> Organization -> OAuth Apps
        "scope": None,               # Supabase sets the permissions on the OAuth app itself
        "pkce": True,
    },
}


def _keys(provider: str) -> tuple[str, str]:
    """The platform's Client ID and Secret for a provider ("" when not set)."""
    s = get_settings()
    return {
        "quickbooks": (s.quickbooks_client_id, s.quickbooks_client_secret),
        "xero": (s.xero_client_id, s.xero_client_secret),
        "supabase": (s.supabase_oauth_client_id, s.supabase_oauth_client_secret),
        "google_sheets": (s.google_client_id, s.google_client_secret),
        "square": (s.square_client_id, s.square_client_secret),
        "salesforce": (s.salesforce_client_id, s.salesforce_client_secret),
        "wave": (s.wave_client_id, s.wave_client_secret),
    }.get(provider, ("", ""))


def endpoint(provider: str, key: str) -> str | None:
    """A provider address (authorize_url / token_url / revoke_url); Square's sandbox when not in production."""
    url = PROVIDERS[provider].get(key)
    if url and provider == "square" and get_settings().square_environment.strip().lower() != "production":
        url = url.replace("connect.squareup.com", "connect.squareupsandbox.com")
    return url


def configured(provider: str) -> bool:
    """
    Is the "Connect" button available for this provider (both keys set)?

    Example:
        >>> configured("xero")      # XERO_CLIENT_ID and XERO_CLIENT_SECRET set?
        False
    """
    client_id, secret = _keys(provider)
    return bool(client_id and secret)


def redirect_uri() -> str:
    """
    The ONE address every provider sends people back to (register it in each developer site).

    Example:
        >>> redirect_uri()
        'https://dcp-api.onrender.com/api/connections/oauth/callback'
    """
    return f"{get_settings().api_base_url}/api/connections/oauth/callback"


def http_client() -> httpx.AsyncClient:
    """An HTTP client for provider calls (tests plug in a fake transport)."""
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_S, transport=HTTP_TRANSPORT)


# ── The state ticket ─────────────────────────────────────────────────────────

def _state_fernet() -> Fernet:
    """Encryption for state tickets: its own key, derived from JWT_SECRET_KEY."""
    digest = hashlib.sha256(("oauth-state:" + get_settings().jwt_secret_key).encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


# Where the browser goes after the provider's sign-in. The keys ("app" = the
# workspace, "start" = Get Started) are what the website sends as return_to;
# the values are the website's addresses (web/lib/routes.ts).
RETURN_PAGES = {"app": "/workspace/settings/integrations", "start": "/get-started/connect"}


def make_state(provider: str, account_id: str, consultant_id: str | None, verifier: str | None = None,
               return_to: str = "app") -> str:
    """Encrypt who is connecting, where to go back (workspace or Get Started) and the PKCE verifier."""
    payload = {"p": provider, "a": account_id, "c": consultant_id, "v": verifier, "n": secrets.token_hex(8),
               "r": return_to if return_to in RETURN_PAGES else "app"}
    return _state_fernet().encrypt(json.dumps(payload).encode()).decode()


def read_state(state: str) -> dict:
    """
    Open a state ticket.

    Returns:
        dict: {"provider", "account_id", "consultant_id", "verifier", "return_to" ("app" | "start")}.

    Raises:
        HTTPException: 400 when it was changed, made by someone else, or is older than 15 minutes.
    """
    try:
        raw = _state_fernet().decrypt(state.encode(), ttl=STATE_TTL_SECONDS)
    except (InvalidToken, ValueError):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="That sign-in link expired or isn't valid. Please click Connect again.")
    p = json.loads(raw)
    return {"provider": p["p"], "account_id": p["a"], "consultant_id": p.get("c"), "verifier": p.get("v"),
            "return_to": p.get("r", "app")}


# ── Steps 1 and 3 ────────────────────────────────────────────────────────────

def authorize_url(provider: str, account_id: str, consultant_id: str | None = None, return_to: str = "app") -> str:
    """
    The provider's sign-in page address, for "Connect".

    Args:
        provider: str, "quickbooks", "xero" or "supabase".
        account_id: str, the client (plan holder) the connection is saved on.
        consultant_id: str | None, the consultant connecting it for them.
        return_to: str, "app" (Settings -> Integrations) or "start" (Get Started).

    Returns:
        str: a URL to send the browser to.

    Raises:
        HTTPException: 404 for an unknown provider; 503 when its keys aren't set.

    Example:
        >>> authorize_url("quickbooks", aid)[:44]
        'https://appcenter.intuit.com/connect/oauth2?'
    """
    if provider not in PROVIDERS:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unknown integration.")
    if not configured(provider):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"{PROVIDERS[provider]['name']} isn't switched on yet. Please check back soon.")
    p = PROVIDERS[provider]
    params = {"client_id": _keys(provider)[0], "redirect_uri": redirect_uri(), "response_type": "code"}
    verifier = None
    if p["pkce"]:
        # PKCE: a random secret whose fingerprint goes in the link; the secret
        # itself is only sent with the code swap, so a stolen code is useless.
        verifier = secrets.token_urlsafe(48)
        params["code_challenge"] = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
        params["code_challenge_method"] = "S256"
    scope = get_settings().wave_scopes if provider == "wave" else p["scope"]
    if scope:
        params["scope"] = scope
    params.update(p.get("extra_params", {}))
    params["state"] = make_state(provider, account_id, consultant_id, verifier, return_to)
    return f"{endpoint(provider, 'authorize_url')}?{urlencode(params)}"


def _expires_at(body: dict) -> int:
    """When the access token expires (Unix time): from "expires_in" seconds, or Square's ISO "expires_at"."""
    if body.get("expires_in"):
        return int(time.time()) + int(body["expires_in"])
    if isinstance(body.get("expires_at"), str):
        try:
            return int(datetime.fromisoformat(body["expires_at"].replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return int(time.time()) + 1800


def _tokens_from(body: dict, old: dict | None = None) -> dict:
    """Keep what we need from a token response; expires_at is a Unix time."""
    return {
        "access_token": body["access_token"],
        # Providers ROTATE refresh tokens: always keep the newest one.
        "refresh_token": body.get("refresh_token") or (old or {}).get("refresh_token"),
        "expires_at": _expires_at(body),
    }


async def _token_request(provider: str, data: dict) -> dict:
    """POST to the provider's token address (client ID + secret as HTTP Basic, the standard way)."""
    client_id, secret = _keys(provider)
    how = PROVIDERS[provider].get("client_auth")   # "body" (Wave: in the form), "json" (Square), else HTTP Basic
    async with http_client() as http:
        if how == "json":
            res = await http.post(endpoint(provider, "token_url"), json={**data, "client_id": client_id,
                                                                         "client_secret": secret},
                                  headers={"Accept": "application/json"})
        else:
            res = await http.post(endpoint(provider, "token_url"),
                                  data={**data, "client_id": client_id, "client_secret": secret} if how == "body" else data,
                                  auth=None if how == "body" else (client_id, secret), headers={"Accept": "application/json"})
    if res.status_code >= 400:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"{PROVIDERS[provider]['name']} didn't accept the sign-in. Please click Connect again.")
    return res.json()


async def exchange_code(provider: str, code: str, verifier: str | None = None) -> dict:
    """
    Step 3: swap the one-time code for tokens.

    Returns:
        dict: {"access_token", "refresh_token", "expires_at"}.

    Raises:
        HTTPException: 409 when the provider refuses the code.
    """
    data = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()}
    if verifier:
        data["code_verifier"] = verifier
    return _tokens_from(await _token_request(provider, data))


async def refresh(provider: str, tokens: dict) -> dict:
    """
    Get a new access token with the refresh token (no sign-in needed).

    Raises:
        HTTPException: 409 when the client removed our access: they must connect again.
    """
    body = await _token_request(provider, {"grant_type": "refresh_token", "refresh_token": tokens["refresh_token"]})
    return _tokens_from(body, tokens)


def needs_refresh(tokens: dict, now: float | None = None) -> bool:
    """True when the access token is expired or about to be."""
    return (now or time.time()) >= tokens.get("expires_at", 0) - REFRESH_EARLY_SECONDS


async def revoke(provider: str, tokens: dict) -> None:
    """On disconnect: tell the provider to cancel our tokens (best effort — never blocks a disconnect)."""
    url = PROVIDERS.get(provider, {}).get("revoke_url")
    if not url or not tokens.get("refresh_token"):
        return
    client_id, secret = _keys(provider)
    try:
        async with http_client() as http:
            if provider == "quickbooks":
                await http.post(url, json={"token": tokens["refresh_token"]}, auth=(client_id, secret),
                                headers={"Accept": "application/json"})
            elif provider == "square":
                await http.post(endpoint(provider, "revoke_url"), json={"client_id": client_id,
                                                                         "access_token": tokens["access_token"]},
                                headers={"Authorization": f"Client {secret}"})
            else:
                await http.post(url, data={"token": tokens["refresh_token"]}, auth=(client_id, secret))
    except httpx.HTTPError:
        pass


# ── Saved credentials (encrypted JSON in dim.data_connections) ──────────────

def pack(tokens: dict, extra: dict) -> str:
    """Tokens + provider details (realm, tenant, project) -> one encrypted text."""
    from api.services.cloud_sources import encrypt

    return encrypt(json.dumps({"mode": "oauth", "tokens": tokens, "extra": extra}))


def unpack(credentials_encrypted: str) -> dict | None:
    """
    The saved OAuth credentials, or None when the saved login is something else
    (a pasted database connection string).

    Returns:
        dict | None: {"mode": "oauth", "tokens": {...}, "extra": {...}}.
    """
    from api.services.cloud_sources import decrypt

    text = decrypt(credentials_encrypted)
    if not text.startswith("{"):
        return None
    data = json.loads(text)
    return data if data.get("mode") == "oauth" else None


# ── After sign-in, and every later use ─────────────────────────────────────

async def finish_connect(provider: str, tokens: dict, realm_id: str = "") -> tuple[str, dict]:
    """
    What to save after a successful sign-in: a name to show, and the provider
    details every later call needs.

      QuickBooks: the company id (`realmId`, sent with the callback) and its name.
      Xero:       the organization ("tenant") the client picked, and the
                  connection id (to free the slot on disconnect).
      Wave:       the business (the first that isn't a personal one).
      Supabase:   the client's projects; with exactly one it is chosen
                  straight away, otherwise they pick one in Integrations.

    Returns:
        tuple[str, dict]: (display_name, extra).
    """
    from api.services import business_tools, cloud_sources, google_sheets

    if provider == "google_sheets":
        return f"Google Sheets: {await google_sheets.account_email(tokens['access_token'])}", {}
    if provider == "square":
        seller = await business_tools.square_merchant(tokens["access_token"])
        return seller["name"], {"merchant_id": seller["merchant_id"], "currency": seller["currency"]}
    if provider == "salesforce":
        # Which org, and its REST address (orgs live on different servers).
        async with http_client() as http:
            res = await http.get("https://login.salesforce.com/services/oauth2/userinfo",
                                 headers={"Authorization": f"Bearer {tokens['access_token']}"})
        if res.status_code != 200:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Salesforce didn't say which org was connected.")
        info = res.json()
        rest = info["urls"]["rest"].replace("{version}", "61.0")
        return f"Salesforce: {info.get('preferred_username') or info.get('organization_id')}", {"rest": rest}
    if provider == "quickbooks":
        if not realm_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="QuickBooks didn't say which company was connected.")
        name = await business_tools.qb_company_name(tokens["access_token"], realm_id)
        return name, {"realm_id": realm_id}
    if provider == "xero":
        org = await business_tools.xero_tenant(tokens["access_token"])
        return org["name"], {"tenant_id": org["tenant_id"], "connection_id": org["connection_id"]}
    if provider == "wave":
        biz = await business_tools.wave_business(tokens["access_token"])
        return biz["name"], {"business_id": biz["business_id"]}
    projects = await cloud_sources.mgmt_projects(tokens["access_token"])
    if len(projects) == 1:
        return f"Supabase: {projects[0]['name']}", {"project_ref": projects[0]["ref"], "project_name": projects[0]["name"]}
    return "Supabase (choose a project)", {"project_ref": None, "project_name": None}


async def load(account_id: str, connector: str) -> tuple[dict, dict]:
    """
    A connected integration's row and its OAuth credentials, with a FRESH
    access token: refreshed (and the rotated refresh token saved, encrypted)
    when it is about to expire.

    Returns:
        tuple[dict, dict]: (data_connections row, {"tokens": {...}, "extra": {...}}).

    Raises:
        HTTPException: 404 when it isn't connected with a sign-in.
    """
    from api.services import account_store

    row = await account_store.get_integration(account_id, connector)
    creds = unpack(row["credentials_encrypted"]) if row and row.get("credentials_encrypted") else None
    if not creds:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"{PROVIDERS.get(connector, {}).get('name', 'That')} isn't connected.")
    if needs_refresh(creds["tokens"]):
        creds["tokens"] = await refresh(connector, creds["tokens"])
        await save(account_id, connector, row["display_name"], creds, row.get("consultant_id"), kind=row.get("kind"))
    return row, creds


async def save(account_id: str, connector: str, display_name: str, creds: dict, consultant_id=None,
               kind: str | None = None) -> dict:
    """Save (or update) a signed-in integration, its credentials encrypted."""
    from api.services import account_store
    from api.services.connections import CONNECTORS

    return await account_store.save_integration(
        account_id=account_id, connector=connector, kind=kind or CONNECTORS[connector]["kind"],
        display_name=display_name, credentials_encrypted=pack(creds["tokens"], creds["extra"]),
        consultant_id=str(consultant_id) if consultant_id else None,
        refresh_frequency="daily" if CONNECTORS[connector]["stores_data"] else "manual")
