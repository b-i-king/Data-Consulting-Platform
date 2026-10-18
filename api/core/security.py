"""
Login tokens (JSON Web Tokens, "JWTs") and the "who is calling?" dependency.

WHAT THIS FILE DOES
-------------------
1. `create_access_token(...)` — after someone creates an account, we hand
   them a signed token. The browser sends it back on later requests in the
   header `Authorization: Bearer <token>`.
2. `get_current_account(...)` — a FastAPI "dependency". Any endpoint that
   lists it as a parameter will only run if a valid token was sent; FastAPI
   then passes the decoded token (a dict) into the endpoint.
3. `get_acting_context(...)` — the same, plus WHO the request is for: the
   caller's own account, or (for a consultant) the client named in the
   `X-Acting-As` header, after checking that client is assigned to them
   (api/services/consultants.py). Use it on anything that reads or changes a
   client's plan or billing.

Guests never need a token: the upload / clean / preview endpoints do not use
`get_current_account`. Only downloading the cleaned file does.

WHY A JWT?
----------
A JWT is a small, signed piece of text. Because it is signed with
`JWT_SECRET_KEY`, the server can trust its contents (account id, email). Same
approach as the Video_Game_Stats_Tracker template (`api/core/deps.py`).

SESSIONS (api/services/sessions.py)
-----------------------------------
Each token also carries "sid", the signed-in device's session, and every
request checks that session is still live: signed out, idle 8 hours, bumped
by a 4th device, or a password change all end it at once — a JWT alone can't
be taken back. "mfa": True says the sign-in included the two-step code.
"""

from datetime import datetime, timedelta, timezone
from typing import Annotated

import jwt
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from api.core.config import get_settings

# `auto_error=False` lets us return our own clear error message instead of
# FastAPI's generic one when the header is missing.
_bearer = HTTPBearer(auto_error=False)


def create_access_token(account_id: str, email: str, account_type: str, role: str = "client",
                        sid: str | None = None, mfa: bool = False) -> str:
    """
    Build and sign a token that identifies this account. `role` is "client" or "consultant".

    The token's payload holds the account id ("sub"), email, account type,
    role, issue time and expiry (ACCESS_TOKEN_EXPIRE_MINUTES from now, 7 days
    by default). It is signed with JWT_SECRET_KEY, so any change to the text
    breaks the signature. The role is a hint only: consultant rights are
    re-checked against the database on every request.

    Args:
        account_id: str, the account's id; stored as the token "subject" ("sub").
        email: str, the account's email address.
        account_type: str, "individual" or "business".
        role: str, "client" (default) or "consultant".

    Returns:
        str: the encoded, signed JWT, sent by the browser as
        `Authorization: Bearer <token>`.

    Example:
        >>> token = create_access_token("abc-123", "ana@example.com", "business")
        >>> decode_access_token(token)["sub"]
        'abc-123'
    """
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": account_id,               # "subject" = who the token belongs to
        "email": email,
        "account_type": account_type,    # "individual" or "business"
        "role": role,                    # "client" or "consultant" (re-checked on every request)
        "iat": now,                      # issued at
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),  # expires
        "sid": sid,   # the device's session (checked on every request)
        "mfa": mfa,   # signed in with the two-step code
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """
    Verify the signature and expiry; return the payload or raise 401.

    Args:
        token: str, the raw JWT text (without the "Bearer " prefix).

    Returns:
        dict: the decoded payload, with keys "sub", "email", "account_type",
        "role", "iat" and "exp".

    Raises:
        HTTPException: 401 "Your session expired..." if the token is past its
            expiry; 401 "Invalid sign-in token." if the signature is wrong, the
            text is malformed, or it was signed with another secret.

    Example:
        >>> payload = decode_access_token(create_access_token("abc-123", "ana@example.com", "individual"))
        >>> payload["email"], payload["role"]
        ('ana@example.com', 'client')
    """
    settings = get_settings()
    try:
        data = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Your session expired. Please sign in again.")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid sign-in token.")
    if data.get("purpose"):   # e.g. the 5-minute two-step ticket is not a login
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid sign-in token.")
    return data


async def get_current_account(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """
    FastAPI dependency: require a valid token and return its payload.

    A "dependency" is a function FastAPI runs before the endpoint; listing
    `Depends(get_current_account)` as an endpoint parameter makes the endpoint
    sign-in only. The token is read from the `Authorization: Bearer <token>`
    header by the `HTTPBearer` helper.

    Args:
        credentials: HTTPAuthorizationCredentials | None, the parsed
            Authorization header supplied by FastAPI; None when it is missing.

    Returns:
        dict: the decoded token payload (see `decode_access_token`).

    Raises:
        HTTPException: 401 with "Create a free account (or sign in)..." when no
            token was sent, the 401 errors from `decode_access_token`, or 401
            when the device's session has ended (sessions.check).

    Example:
        In a router:

            @router.get("/accounts/me")
            async def me(token: Annotated[dict, Depends(get_current_account)]):
                return {"id": token["sub"]}
    """
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Create a free account (or sign in) to download your cleaned data.",
        )
    from api.services import sessions   # imported here to avoid a circular import

    token = decode_access_token(credentials.credentials)
    await sessions.check(token)
    return token


async def get_acting_context(
    token: Annotated[dict, Depends(get_current_account)],
    x_acting_as: Annotated[str | None, Header()] = None,
) -> dict:
    """
    FastAPI dependency: {"account", "consultant", "acting"} — see consultants.acting_context.

    Works out WHO a request is for. Without an `X-Acting-As` header (or when it
    names the caller), the caller's own account is used. A consultant may send
    `X-Acting-As: <client account id>` to work on a client, but only one
    assigned to them (for a business, the team owner's consultant counts).

    Args:
        token: dict, the decoded login token from `get_current_account`.
        x_acting_as: str | None, the `X-Acting-As` request header (a client
            account id), or None when not sent.

    Returns:
        dict: {"account": the account whose plan and billing apply,
        "consultant": the caller's consultant row or None for clients,
        "acting": True when a consultant is working on a client's account}.

    Raises:
        HTTPException: 401 if the caller's account no longer exists; 403 if a
            non-consultant sends X-Acting-As or the client isn't assigned to
            the caller; 404 if the named client does not exist.

    Example:
        Request from a consultant working on a client:

            GET /api/accounts/services
            Authorization: Bearer <consultant token>
            X-Acting-As: 7f3c...client-id

        -> the endpoint receives {"account": <client row>, "consultant": <row>, "acting": True}
    """
    from api.services import consultants   # imported here to avoid a circular import

    return await consultants.acting_context(token, x_acting_as)
