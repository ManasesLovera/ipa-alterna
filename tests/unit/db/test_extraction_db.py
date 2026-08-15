"""Extraction versioning and the partial unique index, against real PostgreSQL."""

from __future__ import annotations

from typing import Any

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.core.enums import ExtractionSource
from ipa.core.errors import ConflictError
from ipa.db.enums import ValidationDecision
from ipa.db.models import ExtractionVersion, Tag

pytestmark = pytest.mark.integration_light


async def _make_tag(session: AsyncSession, slug: str = "invoice") -> Tag:
    tag = Tag(slug=slug, name="Invoice")
    session.add(tag)
    await session.flush()
    await session.refresh(tag)
    return tag


async def test_partial_unique_index_rejects_second_current(
    db_session: AsyncSession, document_factory: Any
) -> None:
    document = await document_factory(sha256="d" * 64)
    tag = await _make_tag(db_session)

    db_session.add(
        ExtractionVersion(
            document_id=document.id,
            version=1,
            tag_id=tag.id,
            tag_version=1,
            source=ExtractionSource.MODEL,
            document_confidence=0.9,
            mongo_id="mongo-1",
            is_current=True,
        )
    )
    await db_session.flush()

    db_session.add(
        ExtractionVersion(
            document_id=document.id,
            version=2,
            tag_id=tag.id,
            tag_version=1,
            source=ExtractionSource.MODEL,
            document_confidence=0.8,
            mongo_id="mongo-2",
            is_current=True,
        )
    )
    with pytest.raises(sa.exc.IntegrityError):
        await db_session.flush()


async def test_add_flips_current_pointer_and_versions_monotonically(
    db_session: AsyncSession, document_factory: Any
) -> None:
    from ipa.db.repositories.extraction import ExtractionRepository

    document = await document_factory(sha256="e" * 64)
    tag = await _make_tag(db_session, slug="receipt")
    repository = ExtractionRepository(db_session)

    first = await repository.add(
        document_id=document.id,
        tag_id=tag.id,
        tag_version=1,
        source=ExtractionSource.MODEL,
        document_confidence=0.7,
        mongo_id="mongo-1",
        model="fake/llm",
    )
    second = await repository.add(
        document_id=document.id,
        tag_id=tag.id,
        tag_version=1,
        source=ExtractionSource.HUMAN,
        document_confidence=1.0,
        mongo_id="mongo-2",
        created_by="reviewer@example.com",
    )

    assert first.version == 1
    assert second.version == 2
    assert second.is_current is True

    current = await repository.get(document.id)
    assert current is not None
    assert current.version == 2

    first_row = await repository.get(document.id, version=1)
    assert first_row is not None
    assert first_row.is_current is False

    versions = await repository.list_versions(document.id)
    assert [row.version for row in versions] == [2, 1]


async def test_validation_round_trip(db_session: AsyncSession, document_factory: Any) -> None:
    from ipa.db.repositories.extraction import ExtractionRepository

    document = await document_factory(sha256="f" * 64)
    tag = await _make_tag(db_session, slug="po")
    repository = ExtractionRepository(db_session)
    version = await repository.add(
        document_id=document.id,
        tag_id=tag.id,
        tag_version=1,
        source=ExtractionSource.MODEL,
        document_confidence=0.5,
        mongo_id="mongo-1",
    )

    validation = await repository.add_validation(
        document_id=document.id,
        extraction_version_id=version.id,
        decision=ValidationDecision.CORRECTED,
        corrected_fields={"total": {"value": 10}},
    )
    listed = await repository.list_validations(document.id)
    assert len(listed) == 1
    assert listed[0].decision == ValidationDecision.CORRECTED
    assert validation.auto is False


async def test_duplicate_slug_conflict(db_session: AsyncSession) -> None:
    from ipa.db.repositories.tag import TagRepository

    repository = TagRepository(db_session)
    await repository.create(slug="dup", name="First")
    with pytest.raises(ConflictError):
        await repository.create(slug="dup", name="Second")
    await db_session.rollback()
