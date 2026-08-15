"""Append-only document event repository."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.core.enums import PipelineStep
from ipa.db.dtos import DocumentEventDto
from ipa.db.models import DocumentEvent


class EventRepository:
    """Writes and reads `document_events` rows; never updates or deletes."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def append(
        self,
        document_id: UUID,
        event_type: str,
        *,
        step: PipelineStep | None = None,
        payload: dict[str, Any] | None = None,
        actor: str | None = None,
        trace_id: str | None = None,
    ) -> DocumentEventDto:
        """Append one audit event.

        Args:
            document_id: Owning document.
            event_type: Stable event name, e.g. `step_succeeded`.
            step: Pipeline step the event relates to, if any.
            payload: Structured event details.
            actor: Who or what caused the event.
            trace_id: OpenTelemetry trace of the event.

        Returns:
            The created event DTO.
        """
        entity = DocumentEvent(
            document_id=document_id,
            step=step,
            event_type=event_type,
            payload=payload,
            actor=actor,
            trace_id=trace_id,
        )
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return DocumentEventDto.model_validate(entity)

    async def list_events(self, document_id: UUID, *, limit: int = 100) -> list[DocumentEventDto]:
        """List a document's events, oldest first.

        Args:
            document_id: Owning document.
            limit: Maximum rows to return.

        Returns:
            Event DTOs ordered by `created_at` ascending.
        """
        stmt = (
            sa.select(DocumentEvent)
            .where(DocumentEvent.document_id == document_id)
            .order_by(DocumentEvent.created_at.asc(), DocumentEvent.id.asc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [DocumentEventDto.model_validate(row) for row in rows]
