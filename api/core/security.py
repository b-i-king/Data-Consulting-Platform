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
`JWT_SECRET_KEY`, the server can trust its contents (account id, email)
without looking anything up in the database on every request. Same approach
as the Video_Game_Stats_Tracker template (`api/core/deps.py`).
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


def create_access_token(account_id: str, email: str, account_type: str, role: str = "client") -> str:
    """Build and sign a token that identifies this account. `role` is "client" or "consultant"."""
    settings = get_settings()
    now = datetime.now(timezone.utc)
    payload = {
        "sub": account_id,               # "subject" = who the token belongs to
        "email": email,
        "account_type": account_type,    # "individual" or "business"
        "role": role,                    # "client" or "consultant" (re-checked on every request)
        "iat": now,                      # issued at
        "exp": now + timedelta(minutes=settings.access_token_expire_minutes),  # expires
    }
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """Verify the signature and expiry; return the payload or raise 401."""
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Your session expired. Please sign in again.")
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid sign-in token.")


async def get_current_account(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict:
    """FastAPI dependency: require a valid token and return its payload."""
    if credentials is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="Create a free account (or sign in) to download your cleaned data.",
        )
    return decode_access_token(credentials.credentials)


async def get_acting_context(
    token: Annotated[dict, Depends(get_current_account)],
    x_acting_as: Annotated[str | None, Header()] = None,
) -> dict:
    """FastAPI dependency: {"account", "consultant", "acting"} — see consultants.acting_context."""
    from api.services import consultants   # imported here to avoid a circular import

    return await consultants.acting_context(token, x_acting_as)
