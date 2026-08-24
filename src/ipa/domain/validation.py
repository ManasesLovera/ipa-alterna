"""Validation service: the review decision and human corrections.

T08's review step delegates here. `decide` applies the auto-approval rules (with
the project default of every document requiring human review); `approve`,
`correct` and `reject` implement the reviewer actions, each writing a new
extraction version rather than mutating in place.
"""

from __future__ import annotations

from datetime import UTC
from typing import Any
from uuid import UUID

import structlog

from ipa.contracts.models import ExtractedField, ExtractionRecord, StepResult
from ipa.contracts.protocols import ContentStore
from ipa.core.enums import DocumentStatus, ExtractionSource, StepStatus
from ipa.core.errors import ConflictError, NotFoundError
from ipa.db.dtos import DocumentPatch
from ipa.db.enums import ValidationDecision
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.extraction import ExtractionRepository
from ipa.db.repositories.step import StepRepository
from ipa.db.repositories.tag import TagRepository
from ipa.db.session import session_scope

logger = structlog.get_logger(__name__)


class ValidationService:
    """Implements the review decision and reviewer actions."""

    def __init__(
        self,
        documents: DocumentRepository,
        extractions: ExtractionRepository,
        steps: StepRepository,
        tags: TagRepository,
        content: ContentStore,
    ) -> None:
        """Initialise the service.

        Args:
            documents: Document repository.
            extractions: Extraction repository.
            steps: Step repository.
            tags: Tag repository.
            content: Content store.
        """
        self._documents = documents
        self._extractions = extractions
        self._steps = steps
        self._tags = tags
        self._content = content

    async def decide(self, document_id: UUID) -> StepResult:
        """Decide whether a document auto-approves or needs human review.

        Args:
            document_id: Identifier of the document.

        Returns:
            A `StepResult` describing the decision.
        """
        document = await self._documents.get(document_id)
        if document is None:
            return StepResult(status=StepStatus.FAILED, detail="document not found")

        tag = await self._tags.get(document.tag_id) if document.tag_id else None
        threshold = tag.auto_approve_threshold if tag else None

        record = await self._content.get_extraction(document_id)
        fields = record.fields if record else []
        confidence = record.document_confidence if record else 0.0

        if threshold is None:
            return await self._pending_review(document_id)
        if any(not f.valid for f in fields):
            return await self._pending_review(document_id)
        if any(f.value is None and f.valid is False for f in fields):
            return await self._pending_review(document_id)
        if confidence < threshold:
            return await self._pending_review(document_id)
        if any(f.confidence < threshold for f in fields):
            return await self._pending_review(document_id)
        return await self._auto_approve(document_id, record, fields)

    async def _pending_review(self, document_id: UUID) -> StepResult:
        """Mark a document as pending human review.

        Args:
            document_id: Identifier of the document.

        Returns:
            A succeeded step result with a needs-review outcome.
        """
        await self._documents.patch(
            document_id,
            DocumentPatch(status=DocumentStatus.PENDING_REVIEW, needs_review=True),
        )
        return StepResult(status=StepStatus.SUCCEEDED, detail="needs review")

    async def _auto_approve(
        self,
        document_id: UUID,
        record: ExtractionRecord | None,
        fields: list[ExtractedField],
    ) -> StepResult:
        """Auto-approve a document and record the decision.

        Args:
            document_id: Identifier of the document.
            record: The current extraction, if any.
            fields: The current fields.

        Returns:
            A succeeded step result with a validated outcome.
        """
        from datetime import datetime

        version = await self._extractions.get(document_id)
        if version is not None:
            await self._extractions.add_validation(
                document_id=document_id,
                extraction_version_id=version.id,
                decision=ValidationDecision.APPROVED,
                auto=True,
            )
        await self._documents.patch(
            document_id,
            DocumentPatch(
                status=DocumentStatus.VALIDATED,
                needs_review=False,
                completed_at=datetime.now(UTC),
            ),
        )
        return StepResult(status=StepStatus.SUCCEEDED, detail="validated")

    async def approve(self, document_id: UUID, notes: str | None = None) -> None:
        """Approve a document's current extraction as-is.

        Args:
            document_id: Identifier of the document.
            notes: Optional reviewer notes.

        Returns:
            None.

        Raises:
            NotFoundError: If the document or its extraction does not exist.
        """
        version = await self._extractions.get(document_id)
        if version is None:
            raise NotFoundError(f"document {document_id} has no extraction")
        await self._extractions.add_validation(
            document_id=document_id,
            extraction_version_id=version.id,
            decision=ValidationDecision.APPROVED,
            reviewer_id=None,
            notes=notes,
        )
        await self._documents.patch(
            document_id,
            DocumentPatch(status=DocumentStatus.VALIDATED, needs_review=False),
        )

    async def correct(
        self,
        document_id: UUID,
        fields: dict[str, Any],
        *,
        expected_version: int,
        notes: str | None = None,
    ) -> None:
        """Create a new HUMAN extraction version from reviewer corrections.

        Args:
            document_id: Identifier of the document.
            fields: The corrected field values keyed by field key.
            expected_version: The version the reviewer loaded; must still be current.
            notes: Optional reviewer notes.

        Returns:
            None.

        Raises:
            ConflictError: If the expected version is stale.
            NotFoundError: If the document or tag does not exist.
        """
        current = await self._extractions.get(document_id)
        if current is None or current.version != expected_version:
            raise ConflictError(
                "the extraction changed while you were reviewing it",
                code="stale_extraction",
            )
        previous = await self._content.get_extraction(document_id)
        if previous is None:
            raise NotFoundError(f"document {document_id} has no extraction")

        corrected_fields: dict[str, Any] = {}
        new_fields: list[ExtractedField] = []
        for field in previous.fields:
            if field.key in fields:
                corrected_fields[field.key] = {"from": field.value, "to": fields[field.key]}
                new_fields.append(
                    ExtractedField(
                        key=field.key,
                        value=fields[field.key],
                        confidence=1.0,
                        page=field.page,
                        evidence=field.evidence,
                        valid=True,
                        validation_errors=[],
                    )
                )
            else:
                new_fields.append(field)

        record = ExtractionRecord(
            document_id=document_id,
            version=current.version + 1,
            tag_id=current.tag_id,
            tag_version=current.tag_version,
            source=ExtractionSource.HUMAN,
            model=None,
            prompt_hash=None,
            fields=new_fields,
            document_confidence=previous.document_confidence,
            created_at=_now(),
            created_by="reviewer",
        )
        mongo_id = await self._content.put_extraction(record)
        new_version = await self._extractions.add(
            document_id=document_id,
            tag_id=current.tag_id,
            tag_version=current.tag_version,
            source=ExtractionSource.HUMAN,
            document_confidence=previous.document_confidence,
            mongo_id=mongo_id,
            created_by="reviewer",
        )
        await self._extractions.add_validation(
            document_id=document_id,
            extraction_version_id=new_version.id,
            decision=ValidationDecision.CORRECTED,
            reviewer_id=None,
            notes=notes,
            corrected_fields=corrected_fields,
        )
        await self._documents.patch(
            document_id,
            DocumentPatch(status=DocumentStatus.VALIDATED, needs_review=False),
        )


def _now() -> Any:
    """Return the current UTC datetime.

    Returns:
        The current UTC datetime.
    """
    from datetime import datetime

    return datetime.now(UTC)



async def review_decision(document_id: UUID) -> StepResult:
    """Decision entry point used by the review step handler.

    Args:
        document_id: Identifier of the document.

    Returns:
        The review decision.
    """
    async with session_scope() as session:
        service = ValidationService(
            documents=DocumentRepository(session),
            extractions=ExtractionRepository(session),
            steps=StepRepository(session),
            tags=TagRepository(session),
            content=_content_store(),
        )
        return await service.decide(document_id)


def _content_store() -> ContentStore:
    """Return the content store singleton.

    Returns:
        The content store.
    """
    from ipa.storage.factory import get_content_store

    return get_content_store()
