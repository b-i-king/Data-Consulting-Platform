"""
Field encryption: lock up the most sensitive values before they're stored.

WHAT THIS FILE DOES
-------------------
  encrypt(text, purpose)    -> "enc1:<Fernet token>"  (AES-128 + HMAC; a new
                               random token every time, so equal values don't
                               look equal in the database)
  decrypt(text, purpose)    -> the original text (text without "enc1:" is
                               returned as is, so older plain rows still read)
  blind_index(text, purpose)-> a keyed one-way fingerprint (HMAC-SHA256) of the
                               normalized value, for MATCHING without decrypting
                               ("is this the same email?"). It can't be turned
                               back into the email.

WHERE IT'S USED
---------------
  * "pii"  emails and phone numbers in stored client data
           (api/services/pii.py: fact.account_data attributes, fact.entities details)
  * "mfa"  two-step sign-in keys (dim.accounts.mfa_secret)

THE KEY
-------
DATA_ENCRYPTION_KEY (a long random value in Render's environment). Each
purpose gets its own key derived from it, so one purpose's key can't open
another's data. Empty: derived from JWT_SECRET_KEY (fine for development).
KEEP IT SAFE: if the key is lost or changed, encrypted values can't be read.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from cryptography.fernet import Fernet, InvalidToken

from api.core.config import get_settings

PREFIX = "enc1:"


def _master() -> bytes:
    """The master secret every purpose's key is derived from."""
    s = get_settings()
    return (s.data_encryption_key or ("derived-from-jwt:" + s.jwt_secret_key)).encode()


def _key(purpose: str) -> bytes:
    """32 bytes for one purpose: HMAC(master, purpose)."""
    return hmac.new(_master(), f"dcp:{purpose}".encode(), hashlib.sha256).digest()


def _fernet(purpose: str) -> Fernet:
    return Fernet(base64.urlsafe_b64encode(_key(purpose)))


def is_encrypted(value) -> bool:
    """True for text made by encrypt()."""
    return isinstance(value, str) and value.startswith(PREFIX)


def encrypt(text: str, purpose: str) -> str:
    """
    Encrypt a value for storage ("enc1:..."). Already-encrypted text is left as is.

    Args:
        text: str, e.g. "ana@example.com".
        purpose: str, "pii" or "mfa".

    Returns:
        str: "enc1:<token>".

    Example:
        >>> decrypt(encrypt("ana@example.com", "pii"), "pii")
        'ana@example.com'
    """
    if is_encrypted(text):
        return text
    return PREFIX + _fernet(purpose).encrypt(str(text).encode()).decode()


def decrypt(text, purpose: str):
    """
    Decrypt a stored value; plain values (not "enc1:...") come back unchanged.

    Args:
        text: the stored value (any type; only "enc1:" text is decrypted).
        purpose: str, the same purpose it was encrypted with.

    Returns:
        The original text, or `text` itself when it isn't encrypted. A value
        that can't be decrypted (the key changed) becomes "[unreadable]".
    """
    if not is_encrypted(text):
        return text
    try:
        return _fernet(purpose).decrypt(text[len(PREFIX):].encode()).decode()
    except InvalidToken:
        return "[unreadable]"


def blind_index(text: str, purpose: str) -> str:
    """
    A keyed one-way fingerprint of a value (trimmed, lowercase) for matching without decrypting.

    Args:
        text: str, e.g. "Ana@Example.com ".
        purpose: str, e.g. "pii".

    Returns:
        str: 32 hex characters; the same for the same value (any letter case).

    Example:
        >>> blind_index("Ana@Example.com", "pii") == blind_index("ana@example.com", "pii")
        True
    """
    norm = str(text).strip().lower()
    return hmac.new(_key(purpose + ":index"), norm.encode(), hashlib.sha256).hexdigest()[:32]
