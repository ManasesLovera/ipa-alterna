"""Auth REST router: login, refresh, logout, me, users and API keys."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.api.auth import Principal, require_auth
from ipa.api.schemas.auth import (
    ApiKeyCreate,
    ApiKeyResult,
    ChangePasswordRequest,
    LoginRequest,
    UserCreate,
)
from ipa.db.repositories.api_key import ApiKeyRepository
from ipa.db.repositories.user import UserRepository
from ipa.db.session import get_session
from ipa.domain.users import (
    AuthError,
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_api_key,
    hash_password,
    key_prefix,
    verify_password,
)

router = APIRouter(tags=["auth"], prefix="/auth")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[Principal, Depends(require_auth)]

_ACCESS_COOKIE = "ipa_access"
_REFRESH_COOKIE = "ipa_refresh"


@router.post("/login")
async def login(
    body: LoginRequest,
    response: Response,
    session: SessionDep,
) -> dict[str, str]:
    """Log a user in, setting access and refresh cookies.

    Args:
        body: The login credentials.
        response: The response to attach cookies to.
        session: The request session.

    Returns:
        A status message.
    """
    user = await UserRepository(session).get_by_email(body.email)
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="invalid credentials")
    access = create_access_token(user.id, user.role)
    refresh = create_refresh_token(user.id)
    _set_cookies(response, access, refresh)
    return {"status": "ok"}


@router.post("/refresh")
async def refresh(
    response: Response,
    refresh_token: Annotated[str | None, Cookie(alias=_REFRESH_COOKIE)] = None,
) -> dict[str, str]:
    """Rotate the refresh token and issue a new access token.

    Args:
        response: The response to attach new cookies to.
        refresh_token: The current refresh cookie.

    Returns:
        A status message.
    """
    if not refresh_token:
        raise HTTPException(status_code=401, detail="no refresh token")
    try:
        payload = decode_token(refresh_token, expected_type="refresh")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc.detail)) from exc
    access = create_access_token(UUID(payload["sub"]), payload.get("role") or "viewer")
    new_refresh = create_refresh_token(UUID(payload["sub"]))
    _set_cookies(response, access, new_refresh)
    return {"status": "ok"}


@router.post("/logout")
async def logout(response: Response) -> dict[str, str]:
    """Clear the auth cookies.

    Args:
        response: The response to clear cookies on.

    Returns:
        A status message.
    """
    response.delete_cookie(_ACCESS_COOKIE)
    response.delete_cookie(_REFRESH_COOKIE)
    return {"status": "ok"}


@router.get("/me")
async def me(principal: AuthDep) -> dict[str, object]:
    """Return the current principal.

    Args:
        principal: The authenticated principal.

    Returns:
        The principal details.
    """
    return {
        "kind": principal.kind,
        "id": principal.id,
        "role": principal.role,
        "scopes": list(principal.scopes),
    }


@router.post("/change-password")
async def change_password(
    body: ChangePasswordRequest,
    principal: AuthDep,
    session: SessionDep,
) -> dict[str, str]:
    """Change the current user's password.

    Args:
        body: The old and new passwords.
        principal: The authenticated principal.
        session: The request session.

    Returns:
        A status message.
    """
    if not principal.id:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = await UserRepository(session).get(UUID(principal.id))
    if user is None or not verify_password(body.old_password, user.password_hash):
        raise HTTPException(status_code=400, detail="old password is incorrect")
    await UserRepository(session).update_password(
        user.id, hash_password(body.new_password)
    )
    return {"status": "ok"}


@router.post("/api-keys", response_model=ApiKeyResult, status_code=status.HTTP_201_CREATED)
async def create_api_key(
    body: ApiKeyCreate,
    session: SessionDep,
    _auth: AuthDep,
) -> ApiKeyResult:
    """Create an API key; the raw key is returned once.

    Args:
        body: The key name and scopes.
        session: The request session.
        _auth: The authenticated principal.

    Returns:
        The key result with the raw key.
    """
    from ipa.core.config import get_settings

    raw, digest = generate_api_key(get_settings().env)
    key = await ApiKeyRepository(session).create(
        name=body.name,
        key_hash=digest,
        prefix=key_prefix(raw),
        scopes=body.scopes,
    )
    return ApiKeyResult(
        id=str(key.id), name=key.name, prefix=key.prefix, scopes=key.scopes, raw_key=raw
    )


@router.delete("/api-keys/{api_key_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_api_key(
    api_key_id: UUID,
    session: SessionDep,
    _auth: AuthDep,
) -> None:
    """Revoke an API key.

    Args:
        api_key_id: Identifier of the key.
        session: The request session.
        _auth: The authenticated principal.

    Returns:
        None.
    """
    await ApiKeyRepository(session).revoke(api_key_id)


@router.post("/users", status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    session: SessionDep,
    _auth: AuthDep,
) -> dict[str, str]:
    """Create a user.

    Args:
        body: The user details.
        session: The request session.
        _auth: The authenticated principal.

    Returns:
        A status message.
    """
    from ipa.db.enums import UserRole

    user = await UserRepository(session).create(
        email=body.email,
        full_name=body.full_name,
        password_hash=hash_password(body.password),
        role=UserRole(body.role),
    )
    return {"id": str(user.id), "email": user.email}


def _set_cookies(response: Response, access: str, refresh: str) -> None:
    """Attach the auth cookies to a response.

    Args:
        response: The response.
        access: The access token.
        refresh: The refresh token.
    """
    response.set_cookie(
        _ACCESS_COOKIE, access, httponly=True, samesite="lax", max_age=15 * 60
    )
    response.set_cookie(
        _REFRESH_COOKIE,
        refresh,
        httponly=True,
        samesite="lax",
        path="/auth/refresh",
        max_age=7 * 24 * 60 * 60,
    )
