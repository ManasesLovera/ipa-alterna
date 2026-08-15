"""Extraction version and validation repository."""

from __future__ import annotations

from decimal import Decimal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.core.enums import ExtractionSource
from ipa.core.errors import ConflictError
from ipa.db.dtos import ExtractionVersionDto, ValidationDto
from ipa.db.enums import ValidationDecision
from ipa.db.models import ExtractionVersion, Validation


class ExtractionRepository:
    """Reads and writes `extraction_versions` and `validations` rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def add(
        self,
        *,
        document_id: UUID,
        tag_id: UUID,
        tag_version: int,
        source: ExtractionSource,
        document_confidence: float,
        mongo_id: str,
        model: str | None = None,
        prompt_hash: str | None = None,
        created_by: str | None = None,
    ) -> ExtractionVersionDto:
        """Append a new extraction version and make it the current one.

        Runs as one transaction: the previous current version is unflagged
        first, then the new row is inserted. The partial unique index enforces
        a single current version even under concurrency.

        Args:
            document_id: Owning document.
            tag_id: Tag the extraction ran against.
            tag_version: Tag version the extraction ran against.
            source: Whether the model or a human produced this version.
            document_confidence: Aggregate confidence in [0, 1].
            mongo_id: Identifier of the payload document in MongoDB.
            model: Model id that produced the extraction, when model-sourced.
            prompt_hash: Digest of the rendered prompt, when model-sourced.
            created_by: Actor identifier for human-sourced versions.

        Returns:
            The created extraction version DTO.

        Raises:
            ConflictError: When a concurrent insert raced this one.
        """
        next_version_stmt = sa.select(
            sa.func.coalesce(sa.func.max(ExtractionVersion.version), 0) + 1
        ).where(ExtractionVersion.document_id == document_id)
        version = (await self._session.execute(next_version_stmt)).scalar_one()
        await self._session.execute(
            sa.update(ExtractionVersion)
            .where(
                ExtractionVersion.document_id == document_id,
                ExtractionVersion.is_current.is_(True),
            )
            .values(is_current=False)
        )
        entity = ExtractionVersion(
            document_id=document_id,
            version=version,
            tag_id=tag_id,
            tag_version=tag_version,
            source=source,
            model=model,
            prompt_hash=prompt_hash,
            document_confidence=Decimal(str(document_confidence)),
            mongo_id=mongo_id,
            is_current=True,
            created_by=created_by,
        )
        self._session.add(entity)
        try:
            await self._session.flush()
        except IntegrityError as exc:
            raise ConflictError(f"extraction version race on document {document_id}") from exc
        await self._session.refresh(entity)
        return ExtractionVersionDto.model_validate(entity)

    async def get(
        self, document_id: UUID, version: int | None = None
    ) -> ExtractionVersionDto | None:
        """Fetch one extraction version; the current one when version is None.

        Args:
            document_id: Owning document.
            version: Specific version to fetch, or None for the current one.

        Returns:
            The extraction version DTO, or None when no such version exists.
        """
        stmt = sa.select(ExtractionVersion).where(ExtractionVersion.document_id == document_id)
        if version is None:
            stmt = stmt.where(ExtractionVersion.is_current.is_(True))
        else:
            stmt = stmt.where(ExtractionVersion.version == version)
        entity = (await self._session.execute(stmt)).scalars().one_or_none()
        return ExtractionVersionDto.model_validate(entity) if entity else None

    async def list_versions(self, document_id: UUID) -> list[ExtractionVersionDto]:
        """List all extraction versions of a document, newest first.

        Args:
            document_id: Owning document.

        Returns:
            Extraction version DTOs ordered by version descending.
        """
        stmt = (
            sa.select(ExtractionVersion)
            .where(ExtractionVersion.document_id == document_id)
            .order_by(ExtractionVersion.version.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [ExtractionVersionDto.model_validate(row) for row in rows]

    async def add_validation(
        self,
        *,
        document_id: UUID,
        extraction_version_id: UUID,
        decision: ValidationDecision,
        reviewer_id: UUID | None = None,
        notes: str | None = None,
        corrected_fields: dict | None = None,
        auto: bool = False,
    ) -> ValidationDto:
        """Record a validation decision over an extraction version.

        Args:
            document_id: Owning document.
            extraction_version_id: The version being validated.
            decision: The review outcome.
            reviewer_id: Reviewing user, when a human decided.
            notes: Free-form reviewer notes.
            corrected_fields: Field corrections keyed by field key.
            auto: True when the decision was produced by the auto-approve rules.

        Returns:
            The created validation DTO.
        """
        entity = Validation(
            document_id=document_id,
            extraction_version_id=extraction_version_id,
            decision=decision,
            reviewer_id=reviewer_id,
            notes=notes,
            corrected_fields=corrected_fields,
            auto=auto,
        )
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return ValidationDto.model_validate(entity)

    async def list_validations(self, document_id: UUID) -> list[ValidationDto]:
        """List a document's validation decisions, newest first.

        Args:
            document_id: Owning document.

        Returns:
            Validation DTOs ordered by `created_at` descending.
        """
        stmt = (
            sa.select(Validation)
            .where(Validation.document_id == document_id)
            .order_by(Validation.created_at.desc(), Validation.id.desc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [ValidationDto.model_validate(row) for row in rows]
