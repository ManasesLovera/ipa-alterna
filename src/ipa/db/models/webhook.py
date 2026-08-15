"""Webhook registration and delivery attempt models.

Used by the webhooks task (T16); the tables are created here so the data
model lands once with the rest of the schema.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ipa.db.base import Base, CreatedOnlyMixin, TimestampMixin, UuidPkMixin, str_enum
from ipa.db.enums import WebhookDeliveryStatus


class Webhook(TimestampMixin, UuidPkMixin, Base):
    """An outbound webhook subscription."""

    __tablename__ = "webhooks"

    url: Mapped[str] = mapped_column(sa.Text, nullable=False)
    secret: Mapped[str] = mapped_column(sa.Text, nullable=False)
    events: Mapped[list[str]] = mapped_column(
        ARRAY(sa.Text), nullable=False, server_default=sa.text("'{}'")
    )
    is_active: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, server_default=sa.true())


class WebhookDelivery(CreatedOnlyMixin, Base):
    """One delivery attempt record for a webhook event."""

    __tablename__ = "webhook_deliveries"

    id: Mapped[int] = mapped_column(sa.BigInteger, primary_key=True, autoincrement=True)
    webhook_id: Mapped[UUID] = mapped_column(sa.Uuid, sa.ForeignKey("webhooks.id"), nullable=False)
    event_type: Mapped[str] = mapped_column(sa.Text, nullable=False)
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[WebhookDeliveryStatus] = mapped_column(
        str_enum(WebhookDeliveryStatus, "webhook_delivery_status"),
        nullable=False,
        server_default=sa.text("'pending'"),
    )
    attempt: Mapped[int] = mapped_column(sa.Integer, nullable=False, server_default=sa.text("0"))
    response_status: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    delivered_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
