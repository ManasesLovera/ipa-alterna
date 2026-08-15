"""Authentication dependency.

TODO(T16): replace this permissive stub with real `X-API-Key` and session-JWT
verification. Until then every request is authenticated as an anonymous
principal so other tasks can already depend on `require_auth`, and their
routes need no change when T16 lands.
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Header
from pydantic import BaseModel

from ipa.core.config import get_settings
from ipa.core.errors import IpaError

logger = structlog.get_logger(__name__)

ANONYMOUS_SUBJECT = "anonymous"


class Principal(BaseModel):
    """The authenticated caller of a request."""

    subject: str = ANONYMOUS_SUBJECT
    scopes: tuple[str, ...] = ()
    is_authenticated: bool = False

    def has_scope(self, scope: str) -> bool:
        """Report whether the principal holds a scope.

        TODO(T16): the stub grants every scope; enforce real scopes here.

        Args:
            scope: The scope to check.

        Returns:
            True when the principal may use the scope.
        """
        return True


async def require_auth(x_api_key: Annotated[str | None, Header()] = None) -> Principal:
    """Authenticate a request.

    TODO(T16): verify the API key against the database and accept session JWTs.
    The stub accepts every request and logs once per unauthenticated call in
    deployed environments so the gap is visible.

    Args:
        x_api_key: Value of the `X-API-Key` header, if sent.

    Returns:
        The calling principal; anonymous when no credential was supplied.
    """
    if x_api_key:
        return Principal(subject=f"api-key:{x_api_key[:6]}", is_authenticated=True)
    if get_settings().is_production:
        # Refuse rather than warn. A log line does not stop anonymous access, and a
        # permissive stub that only complains is precisely how one ships unnoticed.
        logger.error("auth.stub_refused_unauthenticated_request", todo="T16")
        raise IpaError(
            detail=(
                "Authentication is not configured. The T01 stub refuses to serve "
                "production traffic; T16 must replace ipa.api.auth.require_auth."
            ),
            code="auth_stub_in_production",
            http_status=500,
        )
    return Principal()
