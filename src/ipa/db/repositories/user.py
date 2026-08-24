"""User repository."""

from __future__ import annotations

from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.core.errors import ConflictError
from ipa.db.dtos import UserDto
from ipa.db.enums import UserRole
from ipa.db.models import User


class UserRepository:
    """Reads and writes `users` rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def create(
        self, *, email: str, full_name: str, password_hash: str, role: UserRole
    ) -> UserDto:
        """Insert a user.

        Args:
            email: Login email; unique, compared case-insensitively via citext.
            full_name: Display name.
            password_hash: Pre-hashed credential; hashing is not done here.
            role: Platform role.

        Returns:
            The created user DTO.

        Raises:
            ConflictError: When the email is already registered.
        """
        entity = User(email=email, full_name=full_name, password_hash=password_hash, role=role)
        self._session.add(entity)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(f"user already exists: {email}") from exc
        await self._session.refresh(entity)
        return UserDto.model_validate(entity)

    async def get(self, user_id: UUID) -> UserDto | None:
        """Fetch one user.

        Args:
            user_id: Identifier of the user.

        Returns:
            The user DTO, or None when the id is unknown.
        """
        entity = await self._session.get(User, user_id)
        return UserDto.model_validate(entity) if entity else None

    async def get_by_email(self, email: str) -> UserDto | None:
        """Fetch one user by email (case-insensitive).

        Args:
            email: Login email to look up.

        Returns:
            The user DTO, or None when the email is unknown.
        """
        stmt = sa.select(User).where(User.email == email)
        entity = (await self._session.execute(stmt)).scalars().one_or_none()
        return UserDto.model_validate(entity) if entity else None

    async def update_password(self, user_id: UUID, password_hash: str) -> UserDto | None:
        """Replace a user's password hash.

        Args:
            user_id: Identifier of the user.
            password_hash: The new Argon2id hash.

        Returns:
            The updated user DTO, or None when the id is unknown.
        """
        stmt = (
            sa.update(User)
            .where(User.id == user_id)
            .values(password_hash=password_hash)
            .returning(User)
        )
        entity = (await self._session.execute(stmt)).scalars().one_or_none()
        return UserDto.model_validate(entity) if entity else None
