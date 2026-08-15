"""Webhook and delivery repository."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.db.dtos import WebhookDeliveryDto, WebhookDto
from ipa.db.enums import WebhookDeliveryStatus
from ipa.db.models import Webhook, WebhookDelivery


class WebhookRepository:
    """Reads and writes `webhooks` and `webhook_deliveries` rows."""

    def __init__(self, session: AsyncSession) -> None:
        """Bind the repository to a session.

        Args:
            session: The async session performing the I/O.
        """
        self._session = session

    async def create(self, *, url: str, secret: str, events: list[str]) -> WebhookDto:
        """Insert a webhook subscription.

        Args:
            url: Target URL deliveries are POSTed to.
            secret: Signing secret for delivery payloads.
            events: Event names this webhook subscribes to.

        Returns:
            The created webhook DTO.
        """
        entity = Webhook(url=url, secret=secret, events=events)
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return WebhookDto.model_validate(entity)

    async def get(self, webhook_id: UUID) -> WebhookDto | None:
        """Fetch one webhook.

        Args:
            webhook_id: Identifier of the webhook.

        Returns:
            The webhook DTO, or None when the id is unknown.
        """
        entity = await self._session.get(Webhook, webhook_id)
        return WebhookDto.model_validate(entity) if entity else None

    async def list_active(self) -> list[WebhookDto]:
        """List active webhook subscriptions.

        Returns:
            Active webhook DTOs ordered by creation time.
        """
        stmt = (
            sa.select(Webhook).where(Webhook.is_active.is_(True)).order_by(Webhook.created_at.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [WebhookDto.model_validate(row) for row in rows]

    async def record_delivery(
        self,
        *,
        webhook_id: UUID,
        event_type: str,
        payload: dict[str, Any] | None = None,
        attempt: int = 1,
    ) -> WebhookDeliveryDto:
        """Append a pending delivery attempt record.

        Args:
            webhook_id: Destination webhook.
            event_type: Event name that triggered the delivery.
            payload: Serialized event body.
            attempt: 1-based attempt counter.

        Returns:
            The created delivery DTO.
        """
        entity = WebhookDelivery(
            webhook_id=webhook_id,
            event_type=event_type,
            payload=payload,
            attempt=attempt,
        )
        self._session.add(entity)
        await self._session.flush()
        await self._session.refresh(entity)
        return WebhookDeliveryDto.model_validate(entity)

    async def complete_delivery(
        self,
        delivery_id: int,
        *,
        status: WebhookDeliveryStatus,
        response_status: int | None = None,
        error: str | None = None,
    ) -> WebhookDeliveryDto | None:
        """Record the outcome of a delivery attempt.

        Args:
            delivery_id: Identifier of the delivery record.
            status: Final status of the attempt.
            response_status: HTTP status returned by the target, when any.
            error: Error description when the attempt failed.

        Returns:
            The updated delivery DTO, or None when the id is unknown.
        """
        delivered_at = datetime.now(UTC) if status == WebhookDeliveryStatus.DELIVERED else None
        stmt = (
            sa.update(WebhookDelivery)
            .where(WebhookDelivery.id == delivery_id)
            .values(
                status=status,
                response_status=response_status,
                error=error,
                delivered_at=delivered_at,
            )
            .returning(WebhookDelivery)
        )
        row = (await self._session.execute(stmt)).scalars().one_or_none()
        return WebhookDeliveryDto.model_validate(row) if row else None
