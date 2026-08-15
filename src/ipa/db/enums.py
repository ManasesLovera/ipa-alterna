"""Enumerations persisted by the database layer only.

These values live in Postgres columns but are not part of the shared contract
in `ipa.core.enums`; if a later task needs one at the API boundary it should
promote the enum to `ipa.core.enums` (owned by T01) and re-export from here.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    """Role of a human user of the platform."""

    ADMIN = "admin"
    REVIEWER = "reviewer"
    VIEWER = "viewer"


class DocumentSource(StrEnum):
    """Channel through which a document entered the platform."""

    UI = "ui"
    API = "api"


class ValidationDecision(StrEnum):
    """Outcome of a validation pass over an extraction version."""

    APPROVED = "approved"
    REJECTED = "rejected"
    CORRECTED = "corrected"


class WebhookDeliveryStatus(StrEnum):
    """Delivery state of a single webhook attempt record."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"
