"""Tag and schema-registry service.

Implements the domain rules around tags: version bumping on schema changes,
immutable snapshots, auto-approval defaults, deactivation instead of hard delete
when documents reference a tag, and cached compiled schemas.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import structlog

from ipa.api.schemas.tags import (
    CompiledSchema,
    TagCreate,
    TagFieldCreate,
    TagFieldRead,
    TagFieldUpdate,
    TagRead,
    TagUpdate,
    TagVersionRead,
)
from ipa.contracts.protocols import CacheStore
from ipa.core.enums import FieldType
from ipa.core.errors import ConflictError, NotFoundError
from ipa.db.dtos import DbTagFieldCreate, TagDto, TagFieldDto
from ipa.db.repositories.tag import TagRepository
from ipa.domain.prompts import render_prompt
from ipa.processing.schema import TagFieldSpec, build_json_schema, schema_hash
from ipa.storage import cache_keys

logger = structlog.get_logger(__name__)

Uuid = UUID | str


def _to_uuid(value: Uuid) -> UUID:
    """Coerce a UUID or its string form to a UUID.

    Args:
        value: The identifier.

    Returns:
        A `UUID`.

    Raises:
        ValueError: If the string is not a valid UUID.
    """
    return UUID(value) if isinstance(value, str) else value


class TagService:
    """Business rules and orchestration for tags and their compiled schemas."""

    def __init__(self, repo: TagRepository, cache: CacheStore) -> None:
        """Initialise the service.

        Args:
            repo: The tag repository.
            cache: The cache store used for compiled-schema caching.
        """
        self._repo = repo
        self._cache = cache

    async def create_tag(self, spec: TagCreate) -> TagRead:
        """Create a tag, optionally with its initial fields.

        Args:
            spec: The tag specification.

        Returns:
            The created tag read model.

        Raises:
            ConflictError: If the slug already exists.
        """
        fields = [self._coerce_field(f) for f in spec.fields] if spec.fields else None
        tag = await self._repo.create(
            slug=spec.slug,
            name=spec.name,
            description=spec.description,
            auto_approve_threshold=spec.auto_approve_threshold,
            prompt_template=spec.prompt_template,
            llm_model=spec.llm_model,
            classification_hints=spec.classification_hints,
            fields=fields,
        )
        await self._snapshot_version(tag)
        return self._to_read(tag, await self._repo.list_fields(tag.id))

    async def update_tag(self, tag_id: UUID, spec: TagUpdate) -> TagRead:
        """Patch a tag's non-schema attributes.

        Args:
            tag_id: Identifier of the tag.
            spec: The attributes to update.

        Returns:
            The updated tag read model.

        Raises:
            NotFoundError: If the tag does not exist.
        """
        tag = await self._must_get(tag_id)
        updated = await self._repo.update(
            tag.id,
            name=spec.name,
            description=spec.description,
            auto_approve_threshold=spec.auto_approve_threshold,
            prompt_template=spec.prompt_template,
            llm_model=spec.llm_model,
            classification_hints=spec.classification_hints,
        )
        assert updated is not None
        return self._to_read(updated, await self._repo.list_fields(updated.id))

    async def get_tag(self, tag_id: UUID, version: int | None = None) -> TagRead:
        """Fetch a tag, or a historical snapshot.

        Args:
            tag_id: Identifier of the tag.
            version: Snapshot version to return; current when None.

        Returns:
            The tag read model.

        Raises:
            NotFoundError: If the tag or version does not exist.
        """
        tag = await self._must_get(tag_id)
        if version is None or version == tag.version:
            return self._to_read(tag, await self._repo.list_fields(tag.id))
        snapshot = await self._repo.get_version(_to_uuid(tag_id), version)
        if snapshot is None:
            raise NotFoundError(f"tag {tag_id} has no version {version}")
        return self._from_snapshot(tag, snapshot.snapshot)

    async def get_tag_by_slug(self, slug: str) -> TagRead:
        """Fetch a tag by slug.

        Args:
            slug: The tag slug.

        Returns:
            The tag read model.

        Raises:
            NotFoundError: If the slug is unknown.
        """
        tag = await self._repo.get_by_slug(slug)
        if tag is None:
            raise NotFoundError(f"no tag with slug '{slug}'")
        return self._to_read(tag, await self._repo.list_fields(tag.id))

    async def list_tags(
        self, *, include_inactive: bool = False, limit: int = 100
    ) -> list[TagRead]:
        """List tags, optionally including inactive ones.

        Args:
            include_inactive: Include deactivated tags.
            limit: Maximum number of results.

        Returns:
            The tag read models, ordered by slug.
        """
        tags = await self._repo.list_tags(include_inactive=include_inactive)
        result: list[TagRead] = []
        for tag in tags[:limit]:
            result.append(self._to_read(tag, await self._repo.list_fields(tag.id)))
        return result

    async def deactivate_tag(self, tag_id: UUID) -> TagRead:
        """Deactivate a tag, hiding it from pickers.

        Args:
            tag_id: Identifier of the tag.

        Returns:
            The deactivated tag read model.

        Raises:
            NotFoundError: If the tag does not exist.
        """
        await self._must_get(tag_id)
        updated = await self._repo.update(_to_uuid(tag_id), is_active=False)
        assert updated is not None
        return self._to_read(updated, await self._repo.list_fields(updated.id))

    async def delete_tag(self, tag_id: UUID, *, has_documents: bool = False) -> None:
        """Delete a tag only when no document references it.

        Args:
            tag_id: Identifier of the tag.
            has_documents: Whether the caller has established documents reference
                this tag (checked by T07's document repository).

        Returns:
            None.

        Raises:
            NotFoundError: If the tag does not exist.
            ConflictError: If a document references the tag.
        """
        await self._must_get(tag_id)
        if has_documents:
            message = "tag is referenced by documents; deactivate it instead"
            raise ConflictError(message, code="tag_in_use")
        await self._repo.delete(_to_uuid(tag_id))

    async def add_field(self, tag_id: UUID, spec: TagFieldCreate) -> TagRead:
        """Add a field to a tag, bumping its version.

        Args:
            tag_id: Identifier of the tag.
            spec: The field to add.

        Returns:
            The updated tag read model.

        Raises:
            NotFoundError: If the tag does not exist.
        """
        tag = await self._must_get(tag_id)
        fields = await self._repo.list_fields(tag.id)
        coerced = self._coerce_field(spec)
        position = max((f.position for f in fields), default=-1) + 1
        data = coerced.model_dump()
        data["position"] = position
        await self._repo._write_fields(tag.id, [DbTagFieldCreate(**data)])
        await self._bump_and_snapshot(tag)
        return await self.get_tag(tag.id)

    async def update_field(
        self, tag_id: UUID, field_id: UUID, spec: TagFieldUpdate
    ) -> TagRead:
        """Update a field's attributes, bumping the tag version.

        Args:
            tag_id: Identifier of the tag.
            field_id: Identifier of the field.
            spec: The attributes to change.

        Returns:
            The updated tag read model.

        Raises:
            NotFoundError: If the tag or field does not exist.
        """
        await self._must_get(tag_id)
        await self._repo._update_field(_to_uuid(tag_id), _to_uuid(field_id), spec)
        return await self.get_tag(tag_id)

    async def delete_field(self, tag_id: UUID, field_id: UUID) -> TagRead:
        """Remove a field, bumping the tag version.

        Args:
            tag_id: Identifier of the tag.
            field_id: Identifier of the field.

        Returns:
            The updated tag read model.

        Raises:
            NotFoundError: If the tag or field does not exist.
        """
        await self._must_get(tag_id)
        await self._repo._delete_field(_to_uuid(tag_id), _to_uuid(field_id))
        return await self.get_tag(tag_id)

    async def reorder_fields(
        self, tag_id: UUID, ordered_ids: list[UUID]
    ) -> list[TagFieldRead]:
        """Reorder a tag's fields without a version bump.

        Args:
            tag_id: Identifier of the tag.
            ordered_ids: Field ids in the desired order.

        Returns:
            The reordered field read models.

        Raises:
            NotFoundError: If the tag does not exist.
        """
        tag = await self._must_get(tag_id)
        await self._repo._reorder_fields(
            tag.id, [_to_uuid(value) for value in ordered_ids]
        )
        return [self._field_read(f) for f in await self._repo.list_fields(tag.id)]

    async def replace_fields(self, tag_id: UUID, specs: list[TagFieldCreate]) -> TagRead:
        """Replace a tag's fields in one version bump.

        Args:
            tag_id: Identifier of the tag.
            specs: The new ordered fields.

        Returns:
            The updated tag read model.

        Raises:
            NotFoundError: If the tag does not exist.
        """
        tag = await self._must_get(tag_id)
        fields = [self._coerce_field(spec) for spec in specs]
        for index, field in enumerate(fields):
            data = field.model_dump()
            data["position"] = index
            fields[index] = DbTagFieldCreate(**data)
        await self._repo.replace_fields(tag.id, fields)
        await self._bump_and_snapshot(tag)
        return await self.get_tag(tag.id)

    async def compiled_schema(self, tag_id: UUID, version: int | None = None) -> CompiledSchema:
        """Return the compiled schema for a tag version, cache-first.

        Args:
            tag_id: Identifier of the tag.
            version: Version to compile; current when None.

        Returns:
            The compiled schema, prompt and hash.

        Raises:
            NotFoundError: If the tag or version does not exist.
        """
        tag = await self._must_get(tag_id)
        target_version = version or tag.version
        cache_key = cache_keys.tag_schema(tag.id, target_version)
        cached = await self._cache.get_json(cache_key)
        if cached is not None:
            return CompiledSchema.model_validate(cached)
        compiled = await self._compile(tag, target_version)
        await self._cache.set_json(cache_key, compiled.model_dump(), ttl_s=3600)
        return compiled

    async def preview_schema(self, specs: list[TagFieldCreate]) -> dict[str, Any]:
        """Compile a candidate field list without persisting anything.

        Args:
            specs: The candidate fields.

        Returns:
            The compiled JSON Schema.
        """
        coerced = [self._coerce_field(spec) for spec in specs]
        spec_objs = [
            TagFieldSpec(
                key=c.key,
                field_type=c.field_type,
                label=c.label,
                description=c.description or "",
                required=c.is_required,
                enum_values=c.enum_values or [],
                regex=c.regex,
            )
            for c in coerced
        ]
        return build_json_schema(spec_objs)

    async def list_versions(self, tag_id: UUID) -> list[TagVersionRead]:
        """Return the immutable snapshots for a tag.

        Args:
            tag_id: Identifier of the tag.

        Returns:
            The snapshots, newest first.

        Raises:
            NotFoundError: If the tag does not exist.
        """
        await self._must_get(tag_id)
        versions = await self._repo.list_versions(_to_uuid(tag_id))
        return [
            TagVersionRead(
                version=v.version, snapshot=v.snapshot, created_at=str(v.created_at)
            )
            for v in versions
        ]

    async def _compile(self, tag: TagDto, version: int) -> CompiledSchema:
        """Compile a tag version into a schema (uncached path).

        Args:
            tag: The tag DTO.
            version: The version to compile.

        Returns:
            The compiled schema.

        Raises:
            NotFoundError: If the requested version is unknown.
        """
        if version == tag.version:
            fields = await self._repo.list_fields(tag.id)
            specs = [self._field_spec(f) for f in fields]
            json_schema = build_json_schema(specs)
            prompt = render_prompt(
                tag_name=tag.name, tag_description=tag.description, fields=specs
            )
            return CompiledSchema(
                json_schema=json_schema,
                schema_hash=schema_hash(json_schema),
                prompt=prompt,
                tag_version=version,
            )
        snapshot = await self._repo.get_version(tag.id, version)
        if snapshot is None:
            raise NotFoundError(f"tag {tag.id} has no version {version}")
        json_schema = snapshot.snapshot.get("json_schema", {})
        return CompiledSchema(
            json_schema=json_schema,
            schema_hash=schema_hash(json_schema),
            prompt=snapshot.snapshot.get("prompt", ""),
            tag_version=version,
        )

    async def _bump_and_snapshot(self, tag: TagDto) -> None:
        """Bump a tag's version and write an immutable snapshot.

        Args:
            tag: The tag to bump.
        """
        bumped = await self._repo.bump_version(tag.id)
        if bumped is None:
            raise NotFoundError(f"no tag with id {tag.id}")
        await self._snapshot_version(bumped)

    async def _snapshot_version(self, tag: TagDto) -> None:
        """Write an immutable snapshot for the tag's current version.

        Args:
            tag: The tag to snapshot.
        """
        compiled = await self._compile(tag, tag.version)
        fields = await self._repo.list_fields(tag.id)
        snapshot = {
            "version": tag.version,
            "slug": tag.slug,
            "name": tag.name,
            "description": tag.description,
            "json_schema": compiled.json_schema,
            "prompt": compiled.prompt,
            "fields": [self._field_dict(f) for f in fields],
        }
        await self._repo.snapshot(tag.id, tag.version, snapshot)

    async def _must_get(self, tag_id: Uuid) -> TagDto:
        """Return a tag or raise NotFoundError.

        Args:
            tag_id: Identifier of the tag (UUID or its string form).

        Returns:
            The tag DTO.

        Raises:
            NotFoundError: If the tag does not exist.
        """
        tag = await self._repo.get(_to_uuid(tag_id))
        if tag is None:
            raise NotFoundError(f"no tag with id {tag_id}")
        return tag

    def _to_read(self, tag: TagDto, fields: list[TagFieldDto]) -> TagRead:
        """Assemble a TagRead from a tag and its fields.

        Args:
            tag: The tag DTO.
            fields: The field DTOs.

        Returns:
            The tag read model.
        """
        return TagRead(
            id=str(tag.id),
            slug=tag.slug,
            name=tag.name,
            description=tag.description,
            version=tag.version,
            is_active=tag.is_active,
            auto_approve_threshold=tag.auto_approve_threshold,
            prompt_template=tag.prompt_template,
            llm_model=tag.llm_model,
            classification_hints=tag.classification_hints,
            fields=[self._field_read(f) for f in fields],
            created_at=str(tag.created_at),
            updated_at=str(tag.updated_at),
        )

    def _from_snapshot(self, tag: TagDto, snapshot: dict[str, Any]) -> TagRead:
        """Assemble a TagRead from a historical snapshot.

        Args:
            tag: The tag DTO.
            snapshot: The snapshot dict.

        Returns:
            The tag read model as of the snapshot.
        """
        return TagRead(
            id=str(tag.id),
            slug=snapshot.get("slug", tag.slug),
            name=snapshot.get("name", tag.name),
            description=snapshot.get("description", tag.description),
            version=snapshot.get("version", tag.version),
            is_active=tag.is_active,
            auto_approve_threshold=tag.auto_approve_threshold,
            prompt_template=tag.prompt_template,
            llm_model=tag.llm_model,
            classification_hints=tag.classification_hints,
            fields=[TagFieldRead.model_validate(f) for f in snapshot.get("fields", [])],
            created_at=str(tag.created_at),
            updated_at=str(tag.updated_at),
        )

    @staticmethod
    def _coerce_field(spec: TagFieldCreate) -> DbTagFieldCreate:
        """Normalise a field create to the DB shape.

        Args:
            spec: The field spec.

        Returns:
            The spec as a DB-layer field create with a defaulted enum list.
        """
        data = spec.model_dump()
        data["enum_values"] = data.get("enum_values") or []
        return DbTagFieldCreate(**data)

    @staticmethod
    def _field_spec(field: TagFieldDto) -> TagFieldSpec:
        """Map a repository field DTO to a schema-compiler spec.

        Args:
            field: The field DTO.

        Returns:
            The field spec.
        """
        item_type = None
        if field.item_type:
            try:
                item_type = FieldType(field.item_type)
            except ValueError:
                item_type = FieldType.STRING
        return TagFieldSpec(
            key=field.key,
            field_type=field.field_type,
            label=field.label,
            description=field.description or "",
            required=field.is_required,
            enum_values=field.enum_values or [],
            item_type=item_type,
            object_schema=field.object_schema,
            regex=field.regex,
        )

    @staticmethod
    def _field_dict(field: TagFieldDto) -> dict[str, Any]:
        """Render a field DTO as a plain dict for snapshots.

        Args:
            field: The field DTO.

        Returns:
            The dict, shaped like a `TagFieldRead` so snapshots round-trip.
        """
        return {
            "id": str(field.id),
            "key": field.key,
            "label": field.label,
            "field_type": str(field.field_type.value),
            "description": field.description,
            "is_required": field.is_required,
            "position": field.position,
            "enum_values": field.enum_values,
            "regex": field.regex,
            "min_value": field.min_value,
            "max_value": field.max_value,
            "min_length": field.min_length,
            "max_length": field.max_length,
            "item_type": field.item_type,
            "object_schema": field.object_schema,
        }

    @staticmethod
    def _field_read(field: TagFieldDto) -> TagFieldRead:
        """Render a field DTO as an API field read model.

        Args:
            field: The field DTO.

        Returns:
            A `TagFieldRead` model.
        """
        return TagFieldRead(
            id=str(field.id),
            key=field.key,
            label=field.label,
            field_type=field.field_type,
            description=field.description,
            is_required=field.is_required,
            position=field.position,
            enum_values=field.enum_values,
            regex=field.regex,
            min_value=field.min_value,
            max_value=field.max_value,
            min_length=field.min_length,
            max_length=field.max_length,
            item_type=field.item_type,
            object_schema=field.object_schema,
        )
