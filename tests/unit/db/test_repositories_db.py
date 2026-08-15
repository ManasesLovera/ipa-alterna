"""Repository round-trips against a real PostgreSQL."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.core.enums import DocumentStatus, FieldType, PipelineStep
from ipa.db.dtos import DocumentPatch, TagFieldCreate
from ipa.db.enums import DocumentSource, UserRole, WebhookDeliveryStatus
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.event import EventRepository
from ipa.db.repositories.tag import TagRepository
from ipa.db.repositories.user import UserRepository
from ipa.db.repositories.webhook import WebhookRepository

pytestmark = pytest.mark.integration_light


async def test_document_round_trip_and_dedupe_lookup(db_session: AsyncSession) -> None:
    repository = DocumentRepository(db_session)
    digest = "a" * 64

    created = await repository.create(
        sha256=digest,
        original_filename="scan.pdf",
        mime_type="application/pdf",
        size_bytes=100,
        blob_key=f"originals/{digest[:2]}/{digest}",
        source=DocumentSource.API,
        uploaded_by="agent-1",
        metadata={"origin": "test"},
    )
    assert created.status == DocumentStatus.RECEIVED
    assert created.needs_review is True
    assert created.metadata == {"origin": "test"}

    found = await repository.find_by_sha256(digest)
    assert found is not None
    assert found.id == created.id

    assert await repository.find_by_sha256("b" * 64) is None

    patched = await repository.patch(
        created.id,
        DocumentPatch(
            status=DocumentStatus.PROCESSING,
            current_step=PipelineStep.OCR,
            document_confidence=0.75,
        ),
    )
    assert patched is not None
    assert patched.status == DocumentStatus.PROCESSING
    assert patched.current_step == PipelineStep.OCR
    assert patched.document_confidence == pytest.approx(0.75)

    assert await repository.patch(uuid4(), DocumentPatch(title="x")) is None


async def test_tag_with_fields_and_snapshot(db_session: AsyncSession) -> None:
    repository = TagRepository(db_session)

    tag = await repository.create(
        slug="Invoice",
        name="Invoice",
        auto_approve_threshold=0.9,
        fields=[
            TagFieldCreate(
                key="total",
                label="Total",
                field_type=FieldType.NUMBER,
                is_required=True,
                position=0,
                min_value=0,
            ),
            TagFieldCreate(
                key="currency",
                label="Currency",
                field_type=FieldType.ENUM,
                position=1,
                enum_values=["EUR", "USD"],
            ),
        ],
    )
    assert tag.version == 1
    assert tag.auto_approve_threshold == pytest.approx(0.9)

    by_slug = await repository.get_by_slug("invoice")
    assert by_slug is not None
    assert by_slug.id == tag.id

    fields = await repository.list_fields(tag.id)
    assert [field.key for field in fields] == ["total", "currency"]
    assert fields[1].enum_values == ["EUR", "USD"]

    bumped = await repository.bump_version(tag.id)
    assert bumped is not None
    assert bumped.version == 2

    snapshot = await repository.snapshot(tag.id, 1, {"tag": {}, "fields": [], "json_schema": {}})
    fetched = await repository.get_version(tag.id, 1)
    assert fetched is not None
    assert fetched.id == snapshot.id


async def test_event_append_and_list(db_session: AsyncSession, document_factory: Any) -> None:
    document = await document_factory(sha256="9" * 64)
    repository = EventRepository(db_session)

    first = await repository.append(document.id, "uploaded", actor="uploader", trace_id="trace-1")
    await repository.append(
        document.id,
        "step_started",
        step=PipelineStep.OCR,
        payload={"attempt": 1},
    )

    events = await repository.list_events(document.id)
    assert [event.event_type for event in events] == ["uploaded", "step_started"]
    assert events[1].payload == {"attempt": 1}
    assert first.id < events[1].id


async def test_user_round_trip(db_session: AsyncSession) -> None:
    repository = UserRepository(db_session)
    created = await repository.create(
        email="Reviewer@Example.com",
        full_name="Rev Iewer",
        password_hash="argon2:fake",
        role=UserRole.REVIEWER,
    )
    assert created.is_active is True

    by_email = await repository.get_by_email("reviewer@example.com")
    assert by_email is not None
    assert by_email.id == created.id

    by_id = await repository.get(created.id)
    assert by_id is not None
    assert by_id.role == UserRole.REVIEWER


async def test_webhook_and_delivery_round_trip(db_session: AsyncSession) -> None:
    repository = WebhookRepository(db_session)
    created = await repository.create(
        url="https://example.com/hook", secret="s3cret", events=["document.completed"]
    )
    assert created.is_active is True

    active = await repository.list_active()
    assert [hook.id for hook in active] == [created.id]

    delivery = await repository.record_delivery(
        webhook_id=created.id, event_type="document.completed", payload={"x": 1}
    )
    completed = await repository.complete_delivery(
        delivery.id, status=WebhookDeliveryStatus.DELIVERED, response_status=200
    )
    assert completed is not None
    assert completed.status == WebhookDeliveryStatus.DELIVERED
    assert completed.response_status == 200
    assert completed.delivered_at is not None

    failed = await repository.complete_delivery(
        delivery.id, status=WebhookDeliveryStatus.FAILED, error="timeout"
    )
    assert failed is not None
    assert failed.delivered_at is None
