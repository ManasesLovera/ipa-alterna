"""TagService: versioning, snapshots, schema caching and domain rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ipa.api.schemas.tags import (
    TagCreate,
    TagFieldCreate,
    TagFieldUpdate,
    TagUpdate,
)
from ipa.core.enums import FieldType
from ipa.core.errors import ConflictError, NotFoundError
from ipa.db.dtos import (
    TagDto,
    TagFieldDto,
    TagVersionDto,
)
from ipa.domain.tags import TagService
from ipa.processing.schema import schema_hash


class FakeCache:
    """In-memory CacheStore."""

    def __init__(self) -> None:
        self._data: dict[str, dict[str, Any]] = {}
        self.set_calls: list[str] = []
        self.delete_calls: list[str] = []

    async def get_json(self, key: str) -> dict[str, Any] | None:
        return self._data.get(key)

    async def set_json(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        self._data[key] = value
        self.set_calls.append(key)

    async def delete(self, key: str) -> None:
        self._data.pop(key, None)
        self.delete_calls.append(key)


class FakeTagRepo:
    """In-memory stand-in for TagRepository."""

    def __init__(self) -> None:
        self._tags: dict[UUID, TagDto] = {}
        self._fields: dict[UUID, list[TagFieldDto]] = {}
        self._versions: dict[UUID, list[TagVersionDto]] = {}
        self._next_position = 0

    def _seed_tag(self, *, slug: str = "invoice", version: int = 1) -> TagDto:
        tag = TagDto(
            id=uuid4(),
            slug=slug,
            name="Invoice",
            description="An invoice",
            version=version,
            is_active=True,
            auto_approve_threshold=None,
            prompt_template=None,
            llm_model=None,
            classification_hints=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._tags[tag.id] = tag
        self._fields[tag.id] = []
        return tag

    def _field(self, tag_id: UUID, *, key: str = "number", required: bool = False) -> TagFieldDto:
        field = TagFieldDto(
            id=uuid4(),
            tag_id=tag_id,
            key=key,
            label=key,
            field_type=FieldType.STRING,
            description=None,
            is_required=required,
            position=self._next_position,
            enum_values=None,
            regex=None,
            min_value=None,
            max_value=None,
            min_length=None,
            max_length=None,
            item_type=None,
            object_schema=None,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        self._next_position += 1
        self._fields[tag_id].append(field)
        return field

    async def create(self, **kwargs: Any) -> TagDto:
        tag = self._seed_tag(slug=kwargs["slug"])
        tag = TagDto(
            **{**tag.model_dump(), "name": kwargs["name"], "description": kwargs["description"]}
        )
        self._tags[tag.id] = tag
        for field in kwargs.get("fields") or []:
            self._field(tag.id, key=field.key, required=field.is_required)
        return tag

    async def get(self, tag_id: UUID) -> TagDto | None:
        return self._tags.get(tag_id)

    async def get_by_slug(self, slug: str) -> TagDto | None:
        for tag in self._tags.values():
            if tag.slug == slug:
                return tag
        return None

    async def list_tags(self, *, include_inactive: bool = False) -> list[TagDto]:
        tags = list(self._tags.values())
        if not include_inactive:
            tags = [t for t in tags if t.is_active]
        return sorted(tags, key=lambda t: t.slug)

    async def bump_version(self, tag_id: UUID) -> TagDto | None:
        tag = self._tags.get(tag_id)
        if tag is None:
            return None
        updated = TagDto(**{**tag.model_dump(), "version": tag.version + 1})
        self._tags[tag_id] = updated
        return updated

    async def list_fields(self, tag_id: UUID) -> list[TagFieldDto]:
        return list(self._fields.get(tag_id, []))

    async def replace_fields(self, tag_id: UUID, fields: list[Any]) -> list[TagFieldDto]:
        self._fields[tag_id] = [
            self._field(tag_id, key=f.key, required=f.is_required) for f in fields
        ]
        return self._fields[tag_id]

    async def snapshot(self, tag_id: UUID, version: int, snapshot: dict[str, Any]) -> TagVersionDto:
        row = TagVersionDto(
            id=uuid4(),
            tag_id=tag_id,
            version=version,
            snapshot=snapshot,
            created_at=datetime.now(UTC),
        )
        self._versions.setdefault(tag_id, []).append(row)
        return row

    async def get_version(self, tag_id: UUID, version: int) -> TagVersionDto | None:
        for row in self._versions.get(tag_id, []):
            if row.version == version:
                return row
        return None

    async def list_versions(self, tag_id: UUID) -> list[TagVersionDto]:
        return sorted(self._versions.get(tag_id, []), key=lambda v: v.version, reverse=True)

    async def update(self, tag_id: UUID, **kwargs: Any) -> TagDto | None:
        tag = self._tags.get(tag_id)
        if tag is None:
            return None
        data = tag.model_dump()
        for key, value in kwargs.items():
            if value is not None:
                data[key] = value
        updated = TagDto(**data)
        self._tags[tag_id] = updated
        return updated

    async def delete(self, tag_id: UUID) -> bool:
        return self._tags.pop(tag_id, None) is not None

    async def _update_field(self, tag_id: UUID, field_id: UUID, updates: TagFieldUpdate) -> bool:
        return True

    async def _delete_field(self, tag_id: UUID, field_id: UUID) -> bool:
        self._fields[tag_id] = [f for f in self._fields.get(tag_id, []) if f.id != field_id]
        return True

    async def _reorder_fields(self, tag_id: UUID, ordered_ids: list[UUID]) -> None:
        by_id = {f.id: f for f in self._fields.get(tag_id, [])}
        self._fields[tag_id] = [by_id[i] for i in ordered_ids if i in by_id]

    async def _write_fields(self, tag_id: UUID, fields: list[Any]) -> None:
        for field in fields:
            self._field(tag_id, key=field.key, required=field.is_required)


def _field_spec(key: str, *, required: bool = False) -> TagFieldCreate:
    return TagFieldCreate(
        key=key, label=key, field_type=FieldType.STRING, is_required=required
    )


def _service() -> tuple[TagService, FakeTagRepo, FakeCache]:
    repo = FakeTagRepo()
    cache = FakeCache()
    return TagService(repo, cache), repo, cache


async def test_create_tag_with_fields_bumps_nothing_but_has_version_one() -> None:
    service, _, _ = _service()
    spec = TagCreate(
        slug="invoice",
        name="Invoice",
        fields=[_field_spec("number", required=True), _field_spec("vendor")],
    )

    tag = await service.create_tag(spec)

    assert tag.version == 1
    assert tag.auto_approve_threshold is None
    assert [f.key for f in tag.fields] == ["number", "vendor"]


async def test_create_tag_defaults_to_human_review() -> None:
    service, _, _ = _service()
    spec = TagCreate(slug="invoice", name="Invoice", fields=[_field_spec("number")])

    tag = await service.create_tag(spec)

    assert tag.auto_approve_threshold is None


async def test_add_field_bumps_version_and_writes_snapshot() -> None:
    service, _, _ = _service()
    spec = TagCreate(slug="invoice", name="Invoice", fields=[_field_spec("number")])
    tag = await service.create_tag(spec)

    updated = await service.add_field(tag.id, _field_spec("vendor"))

    assert updated.version == 2
    versions = await service.list_versions(tag.id)
    assert len(versions) == 2
    assert versions[0].version == 2


async def test_updating_only_label_does_not_bump_version() -> None:
    service, _, _ = _service()
    spec = TagCreate(slug="invoice", name="Invoice", fields=[_field_spec("number")])
    tag = await service.create_tag(spec)
    assert tag.version == 1

    updated = await service.update_tag(tag.id, TagUpdate(name="Invoices"))

    assert updated.version == 1
    assert updated.name == "Invoices"


async def test_replace_fields_bumps_exactly_once() -> None:
    service, _, _ = _service()
    spec = TagCreate(slug="invoice", name="Invoice", fields=[_field_spec("number")])
    tag = await service.create_tag(spec)

    updated = await service.replace_fields(
        tag.id,
        [_field_spec("a"), _field_spec("b"), _field_spec("c"), _field_spec("d"), _field_spec("e")],
    )

    assert updated.version == 2


async def test_get_tag_returns_historical_snapshot() -> None:
    service, _, _ = _service()
    spec = TagCreate(slug="invoice", name="Invoice", fields=[_field_spec("number")])
    tag = await service.create_tag(spec)
    await service.add_field(tag.id, _field_spec("vendor"))

    v1 = await service.get_tag(tag.id, version=1)

    assert v1.version == 1
    assert [f.key for f in v1.fields] == ["number"]


async def test_compiled_schema_valid_and_cached() -> None:
    service, _, cache = _service()
    spec = TagCreate(
        slug="invoice", name="Invoice", fields=[_field_spec("number", required=True)]
    )
    tag = await service.create_tag(spec)

    compiled = await service.compiled_schema(tag.id)
    compiled_again = await service.compiled_schema(tag.id)

    assert compiled.schema_hash == schema_hash(compiled.json_schema)
    assert compiled.schema_hash == compiled_again.schema_hash
    assert len(cache.set_calls) == 1  # cached on first, served on second


async def test_cache_invalidates_on_field_change() -> None:
    service, _, cache = _service()
    spec = TagCreate(slug="invoice", name="Invoice", fields=[_field_spec("number")])
    tag = await service.create_tag(spec)
    first = await service.compiled_schema(tag.id)

    await service.add_field(tag.id, _field_spec("vendor"))
    second = await service.compiled_schema(tag.id)

    assert first.schema_hash != second.schema_hash
    assert len(cache.set_calls) == 2


async def test_delete_tag_when_documents_reference_raises_conflict() -> None:
    service, _, _ = _service()
    tag = await service.create_tag(TagCreate(slug="invoice", name="Invoice"))

    try:
        await service.delete_tag(tag.id, has_documents=True)
    except ConflictError as exc:
        assert exc.code == "tag_in_use"
    else:
        raise AssertionError("expected ConflictError")


async def test_get_tag_by_slug() -> None:
    service, _, _ = _service()
    await service.create_tag(TagCreate(slug="invoice", name="Invoice"))

    tag = await service.get_tag_by_slug("invoice")

    assert tag.slug == "invoice"


async def test_deactivate_tag_hides_from_default_list() -> None:
    service, _, _ = _service()
    tag = await service.create_tag(TagCreate(slug="invoice", name="Invoice"))

    await service.deactivate_tag(tag.id)
    active = await service.list_tags()

    assert tag.id not in [t.id for t in active]


async def test_get_tag_unknown_raises_not_found() -> None:
    service, _, _ = _service()

    try:
        await service.get_tag(uuid4())
    except NotFoundError:
        pass
    else:
        raise AssertionError("expected NotFoundError")
