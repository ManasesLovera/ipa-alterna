"""User and API-key auth domain: hashing, verification and token lifecycle."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt
import structlog
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from ipa.core.config import get_settings
from ipa.core.errors import IpaError

logger = structlog.get_logger(__name__)

ACCESS_TOKEN_TTL = timedelta(minutes=15)
REFRESH_TOKEN_TTL = timedelta(days=7)
_REFRESH_ALGORITHM = "HS256"


class AuthError(IpaError):
    """Authentication failed; the caller must present valid credentials."""

    code = "unauthorized"
    http_status = 401
    title = "Unauthorized"


class ForbiddenError(IpaError):
    """The caller lacks the required scope or role."""

    code = "insufficient_scope"
    http_status = 403
    title = "Forbidden"


_password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password with Argon2id.

    Args:
        password: The plaintext password.

    Returns:
        An Argon2id-encoded hash.
    """
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a password against an Argon2id hash.

    Args:
        password: The plaintext password.
        password_hash: The stored hash.

    Returns:
        True when the password matches.
    """
    try:
        return _password_hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def generate_api_key(env: str = "local") -> tuple[str, str]:
    """Generate a new API key and return it with its SHA-256 digest.

    The raw key is returned exactly once; only the digest is stored.

    Args:
        env: The environment name embedded in the key.

    Returns:
        A tuple of `(raw_key, sha256_hex)`.
    """
    raw = f"ipa_{env}_{secrets.token_urlsafe(24)}"
    return raw, hashlib.sha256(raw.encode()).hexdigest()


def key_prefix(raw_key: str) -> str:
    """Return an 8-character display prefix for a key.

    Args:
        raw_key: The raw API key.

    Returns:
        The first 8 characters.
    """
    return raw_key[:8]


def create_access_token(user_id: UUID, role: str) -> str:
    """Create a short-lived access JWT.

    Args:
        user_id: The user's id.
        role: The user's role.

    Returns:
        A signed JWT.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + ACCESS_TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def create_refresh_token(user_id: UUID) -> str:
    """Create a rotating refresh JWT.

    Args:
        user_id: The user's id.

    Returns:
        A signed JWT.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    jti = secrets.token_hex(16)
    payload = {
        "sub": str(user_id),
        "type": "refresh",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + REFRESH_TOKEN_TTL).timestamp()),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=_REFRESH_ALGORITHM)


def decode_token(token: str, expected_type: str = "access") -> dict[str, Any]:
    """Decode and validate a JWT of a given type.

    Args:
        token: The JWT.
        expected_type: The token type expected.

    Returns:
        The decoded payload.

    Raises:
        AuthError: If the token is invalid, expired or of the wrong type.
    """
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or expired token") from exc
    if payload.get("type") != expected_type:
        raise AuthError("wrong token type")
    return payload


def sign_webhook(secret: str, timestamp: str, body: bytes) -> str:
    """Sign a webhook delivery payload.

    Args:
        secret: The webhook signing secret.
        timestamp: The unix-seconds timestamp string.
        body: The serialized JSON body.

    Returns:
        The HMAC signature `sha256=<hex>`.
    """
    message = f"{timestamp}.".encode() + body
    digest = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
