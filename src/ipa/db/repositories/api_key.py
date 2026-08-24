"""API key repository."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.db.dtos import ApiKeyDto
from ipa.db.models import ApiKey


class ApiKeyRepository:
    """Reads and writes `api_keys` rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def create(
        self,
        *,
        name: str,
        key_hash: str,
        prefix: str,
        scopes: list[str],
        expires_at: datetime | None = None,
    ) -> ApiKeyDto:
        """Insert an API key.

        Args:
            name: Human-friendly key name.
            key_hash: SHA-256 of the raw key; the raw key is never stored.
            prefix: Short display prefix for the raw key.
            scopes: Scopes granted to the key.
            expires_at: Optional expiry.

        Returns:
            The created key DTO.
        """
        entity = ApiKey(
            name=name,
            key_hash=key_hash,
            prefix=prefix,
            scopes=scopes,
            expires_at=expires_at,
        )
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return ApiKeyDto.model_validate(entity)

    async def get_by_hash(self, key_hash: str) -> ApiKeyDto | None:
        """Fetch a key by its SHA-256 digest.

        Args:
            key_hash: SHA-256 of the raw key.

        Returns:
            The key DTO, or None when unknown.
        """
        stmt = sa.select(ApiKey).where(ApiKey.key_hash == key_hash)
        entity = (await self._session.execute(stmt)).scalars().one_or_none()
        return ApiKeyDto.model_validate(entity) if entity else None

    async def revoke(self, api_key_id: UUID) -> ApiKeyDto | None:
        """Revoke a key, marking it revoked but never deleting the row.

        Args:
            api_key_id: Identifier of the key.

        Returns:
            The revoked key DTO, or None when unknown.
        """
        stmt = (
            sa.update(ApiKey)
            .where(ApiKey.id == api_key_id, ApiKey.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
            .returning(ApiKey)
        )
        entity = (await self._session.execute(stmt)).scalars().one_or_none()
        return ApiKeyDto.model_validate(entity) if entity else None

    async def touch(self, api_key_id: UUID) -> None:
        """Update `last_used_at` on a key.

        Args:
            api_key_id: Identifier of the key.
        """
        await self._session.execute(
            sa.update(ApiKey)
            .where(ApiKey.id == api_key_id)
            .values(last_used_at=datetime.now(UTC))
        )
