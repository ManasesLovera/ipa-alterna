"""ValidationService: the auto-approve decision rules."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from ipa.contracts.models import ExtractedField, ExtractionRecord
from ipa.core.enums import ExtractionSource
from ipa.domain.validation import ValidationService


def _field(key: str, value: Any, confidence: float, valid: bool = True) -> ExtractedField:
    return ExtractedField(key=key, value=value, confidence=confidence, valid=valid)


def _record(confidence: float, fields: list[ExtractedField]) -> ExtractionRecord:
    return ExtractionRecord(
        document_id=uuid4(),
        version=1,
        tag_id=uuid4(),
        tag_version=1,
        source=ExtractionSource.MODEL,
        fields=fields,
        document_confidence=confidence,
        created_at=datetime.now(UTC),
    )


class FakeDocs:
    """In-memory document repository."""

    def __init__(self, tag_id: UUID | None = None, status: str = "processing") -> None:
        self.tag_id = tag_id
        self.status = status
        self.patches: list[Any] = []

    async def get(self, document_id: UUID) -> Any:
        return type(
            "D",
            (),
            {
                "tag_id": self.tag_id,
                "status": self.status,
                "document_confidence": 0.9,
            },
        )()

    async def patch(self, document_id: UUID, patch: Any) -> None:
        self.patches.append(patch)


class FakeExtractions:
    """In-memory extraction repository."""

    def __init__(self) -> None:
        self.version = None
        self.validations: list[Any] = []

    async def get(self, document_id: UUID) -> Any:
        return self.version

    async def add_validation(self, **kwargs: Any) -> Any:
        self.validations.append(kwargs)
        return None


class FakeSteps:
    """In-memory step repository."""

    async def reset_from(self, *args: Any, **kwargs: Any) -> list[Any]:
        return []


class FakeTags:
    """In-memory tag repository."""

    def __init__(self, threshold: float | None = None) -> None:
        self.threshold = threshold

    async def get(self, tag_id: UUID) -> Any:
        return type("T", (), {"auto_approve_threshold": self.threshold})()


class FakeContent:
    """In-memory content store."""

    def __init__(self, record: ExtractionRecord | None = None) -> None:
        self.record = record

    async def get_extraction(self, document_id: UUID, version: int | None = None) -> Any:
        return self.record


def _service(
    *,
    threshold: float | None = None,
    record: ExtractionRecord | None = None,
    tag_id: UUID | None = None,
) -> ValidationService:
    tag_id = tag_id or uuid4()
    return ValidationService(
        documents=FakeDocs(tag_id=tag_id),
        extractions=FakeExtractions(),
        steps=FakeSteps(),
        tags=FakeTags(threshold),
        content=FakeContent(record),
    )


async def test_null_threshold_always_pending_review_even_at_high_confidence() -> None:
    service = _service(
        threshold=None,
        record=_record(0.99, [_field("number", "x", 0.99)]),
    )

    result = await service.decide(uuid4())

    assert result.detail == "needs review"


async def test_threshold_met_auto_approves() -> None:
    service = _service(
        threshold=0.8,
        record=_record(0.9, [_field("number", "x", 0.9)]),
    )

    result = await service.decide(uuid4())

    assert result.detail == "validated"


async def test_weakest_link_blocks_auto_approval() -> None:
    service = _service(
        threshold=0.8,
        record=_record(0.9, [_field("number", "x", 0.9), _field("amount", "y", 0.3)]),
    )

    result = await service.decide(uuid4())

    assert result.detail == "needs review"


async def test_invalid_field_blocks_auto_approval() -> None:
    service = _service(
        threshold=0.8,
        record=_record(0.9, [_field("number", "bad", 0.9, valid=False)]),
    )

    result = await service.decide(uuid4())

    assert result.detail == "needs review"
