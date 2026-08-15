"""Tag repository."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.core.errors import ConflictError
from ipa.db.dtos import TagDto, TagFieldCreate, TagFieldDto, TagVersionDto
from ipa.db.models import Tag, TagField, TagVersion


class TagRepository:
    """Reads and writes `tags`, `tag_fields` and `tag_versions` rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def create(
        self,
        *,
        slug: str,
        name: str,
        description: str = "",
        auto_approve_threshold: float | None = None,
        prompt_template: str | None = None,
        llm_model: str | None = None,
        classification_hints: str | None = None,
        fields: list[TagFieldCreate] | None = None,
    ) -> TagDto:
        """Insert a tag and, in the same transaction, its fields.

        Args:
            slug: Unique citext identifier.
            name: Human-readable tag name.
            description: Free-form description.
            auto_approve_threshold: Confidence at or above which documents
                auto-validate; None always routes to human review.
            prompt_template: Optional extraction prompt override.
            llm_model: Optional model id override.
            classification_hints: Hints used when classifying uploads.
            fields: Ordered schema fields to create with the tag.

        Returns:
            The created tag DTO.

        Raises:
            ConflictError: When the slug already exists.
        """
        entity = Tag(
            slug=slug,
            name=name,
            description=description,
            auto_approve_threshold=(
                Decimal(str(auto_approve_threshold)) if auto_approve_threshold is not None else None
            ),
            prompt_template=prompt_template,
            llm_model=llm_model,
            classification_hints=classification_hints,
        )
        self._session.add(entity)
        try:
            await self._session.flush()
        except sa.exc.IntegrityError as exc:
            raise ConflictError(f"tag slug already exists: {slug}") from exc
        if fields:
            await self._write_fields(entity.id, fields)
        await self._session.refresh(entity)
        return TagDto.model_validate(entity)

    async def get(self, tag_id: UUID) -> TagDto | None:
        """Fetch one tag.

        Args:
            tag_id: Identifier of the tag.

        Returns:
            The tag DTO, or None when the id is unknown.
        """
        entity = await self._session.get(Tag, tag_id)
        return TagDto.model_validate(entity) if entity else None

    async def get_by_slug(self, slug: str) -> TagDto | None:
        """Fetch one tag by slug; citext makes the match case-insensitive.

        Args:
            slug: Slug to look up.

        Returns:
            The tag DTO, or None when the slug is unknown.
        """
        stmt = sa.select(Tag).where(Tag.slug == slug)
        entity = (await self._session.execute(stmt)).scalars().one_or_none()
        return TagDto.model_validate(entity) if entity else None

    async def list_tags(self, *, include_inactive: bool = False) -> list[TagDto]:
        """List tags ordered by slug.

        Args:
            include_inactive: Also return deactivated tags.

        Returns:
            Tag DTOs ordered by slug.
        """
        stmt = sa.select(Tag)
        if not include_inactive:
            stmt = stmt.where(Tag.is_active.is_(True))
        stmt = stmt.order_by(Tag.slug)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [TagDto.model_validate(row) for row in rows]

    async def bump_version(self, tag_id: UUID) -> TagDto | None:
        """Increment a tag's schema version counter.

        Args:
            tag_id: Identifier of the tag.

        Returns:
            The updated tag DTO, or None when the id is unknown.
        """
        stmt = sa.update(Tag).where(Tag.id == tag_id).values(version=Tag.version + 1).returning(Tag)
        row = (await self._session.execute(stmt)).scalars().one_or_none()
        return TagDto.model_validate(row) if row else None

    async def list_fields(self, tag_id: UUID) -> list[TagFieldDto]:
        """Return a tag's fields ordered by position.

        Args:
            tag_id: Identifier of the tag.

        Returns:
            Field DTOs in schema order.
        """
        stmt = sa.select(TagField).where(TagField.tag_id == tag_id)
        stmt = stmt.order_by(TagField.position)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [TagFieldDto.model_validate(row) for row in rows]

    async def replace_fields(self, tag_id: UUID, fields: list[TagFieldCreate]) -> list[TagFieldDto]:
        """Replace the tag's field list in one transaction.

        Args:
            tag_id: Identifier of the tag.
            fields: The new ordered schema fields.

        Returns:
            The newly written field DTOs in schema order.
        """
        await self._session.execute(sa.delete(TagField).where(TagField.tag_id == tag_id))
        await self._write_fields(tag_id, fields)
        return await self.list_fields(tag_id)

    async def snapshot(self, tag_id: UUID, version: int, snapshot: dict[str, Any]) -> TagVersionDto:
        """Record an immutable snapshot of the tag and its fields.

        Args:
            tag_id: Identifier of the tag.
            version: Version number being frozen.
            snapshot: Full tag plus fields plus generated JSON Schema.

        Returns:
            The created snapshot DTO.

        Raises:
            ConflictError: When the (tag, version) pair already exists.
        """
        entity = TagVersion(tag_id=tag_id, version=version, snapshot=snapshot)
        self._session.add(entity)
        try:
            await self._session.flush()
        except sa.exc.IntegrityError as exc:
            raise ConflictError(f"tag {tag_id} already has a version {version} snapshot") from exc
        await self._session.refresh(entity)
        return TagVersionDto.model_validate(entity)

    async def get_version(self, tag_id: UUID, version: int) -> TagVersionDto | None:
        """Fetch one immutable tag snapshot.

        Args:
            tag_id: Identifier of the tag.
            version: Snapshot version to fetch.

        Returns:
            The snapshot DTO, or None when it does not exist.
        """
        stmt = sa.select(TagVersion).where(
            TagVersion.tag_id == tag_id, TagVersion.version == version
        )
        entity = (await self._session.execute(stmt)).scalars().one_or_none()
        return TagVersionDto.model_validate(entity) if entity else None

    async def _write_fields(self, tag_id: UUID, fields: list[TagFieldCreate]) -> None:
        """Insert field rows for a tag.

        Args:
            tag_id: Identifier of the tag.
            fields: Ordered schema fields to insert.
        """
        for field in fields:
            data = field.model_dump()
            data["tag_id"] = tag_id
            for key in ("min_value", "max_value"):
                if data.get(key) is not None:
                    data[key] = Decimal(str(data[key]))
            self._session.add(TagField(**data))
        await self._session.flush()
