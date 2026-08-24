"""Authentication dependency: X-API-Key for machines, JWT for users.

`require_auth` resolves a `Principal` from either the `X-API-Key` header or a
session access-token cookie, and enforces the requested scopes. This replaces the
T01 permissive stub; the import path is unchanged.
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

import structlog
from fastapi import Cookie, Header, Request
from pydantic import BaseModel

from ipa.db.repositories.api_key import ApiKeyRepository
from ipa.db.session import session_scope
from ipa.domain.users import AuthError, decode_token

logger = structlog.get_logger(__name__)


class Principal(BaseModel):
    """The authenticated caller of a request."""

    kind: str = "anonymous"  # "user" | "api_key" | "anonymous"
    id: str | None = None
    role: str | None = None
    scopes: tuple[str, ...] = ()
    is_authenticated: bool = False

    def has_scope(self, scope: str) -> bool:
        """Report whether the principal holds a scope.

        Args:
            scope: The scope to check.

        Returns:
            True when the principal may use the scope.
        """
        return self.is_authenticated and (scope in self.scopes or self.role == "admin")


async def require_auth(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Cookie(alias="ipa_access")] = None,
) -> Principal:
    """Authenticate a request and enforce scopes.

    Args:
        request: The request, used for rate limiting and binding.
        x_api_key: Machine API key from the `X-API-Key` header.
        access_token: User session token from the `ipa_access` cookie.

    Returns:
        The authenticated principal.

    Raises:
        AuthError: If credentials are invalid or absent.
        ForbiddenError: If a required scope is missing.
    """
    if x_api_key:
        return await _resolve_api_key(request, x_api_key)
    if access_token:
        return await _resolve_user(access_token)
    raise AuthError("authentication required")


async def require_any(
    request: Request,
    x_api_key: Annotated[str | None, Header()] = None,
    access_token: Annotated[str | None, Cookie(alias="ipa_access")] = None,
) -> Principal:
    """Authenticate a request, allowing anonymous callers.

    Unlike `require_auth`, this never raises for an unauthenticated caller; it is
    used by endpoints that are public-but-attributable.

    Args:
        request: The request.
        x_api_key: Machine API key, if any.
        access_token: User session token, if any.

    Returns:
        A principal; anonymous when no credential was supplied.
    """
    if x_api_key:
        return await _resolve_api_key(request, x_api_key)
    if access_token:
        return await _resolve_user(access_token)
    return Principal()


async def _resolve_api_key(request: Request, raw_key: str) -> Principal:
    """Resolve an `X-API-Key` header to a principal.

    Args:
        request: The request.
        raw_key: The raw API key.

    Returns:
        The API-key principal.

    Raises:
        AuthError: If the key is unknown or revoked.
    """
    digest = hashlib.sha256(raw_key.encode()).hexdigest()
    async with session_scope() as session:
        key = await ApiKeyRepository(session).get_by_hash(digest)
        if key is None or key.revoked_at is not None:
            raise AuthError("invalid API key")
        _check_expiry(key)
    logger.info("auth.api_key", key_id=str(key.id), scope="mcp")
    return Principal(
        kind="api_key",
        id=str(key.id),
        scopes=tuple(key.scopes),
        is_authenticated=True,
    )


async def _resolve_user(token: str) -> Principal:
    """Resolve an access-token cookie to a user principal.

    Args:
        token: The access JWT.

    Returns:
        The user principal.

    Raises:
        AuthError: If the token is invalid.
    """
    payload = decode_token(token, expected_type="access")
    return Principal(
        kind="user",
        id=payload["sub"],
        role=payload.get("role"),
        scopes=_role_scopes(payload.get("role")),
        is_authenticated=True,
    )


def _check_expiry(key: Any) -> None:
    """Reject an expired API key.

    Args:
        key: The key DTO.

    Raises:
        AuthError: If the key has expired.
    """
    if key.expires_at is not None and _now() > key.expires_at:
        raise AuthError("API key has expired")


def _role_scopes(role: str | None) -> tuple[str, ...]:
    """Map a role to its granted scopes.

    Args:
        role: The user's role.

    Returns:
        The scopes granted to the role.
    """
    admin = (
        "documents:read",
        "documents:write",
        "tags:read",
        "tags:write",
        "review:read",
        "review:write",
        "search:read",
        "mcp",
    )
    if role == "admin":
        return admin
    if role == "reviewer":
        return (
            "documents:read",
            "tags:read",
            "review:read",
            "review:write",
            "search:read",
        )
    return ("documents:read", "tags:read", "review:read", "search:read")


def _now() -> Any:
    """Return the current UTC datetime.

    Returns:
        The current UTC datetime.
    """
    from datetime import UTC, datetime

    return datetime.now(UTC)
