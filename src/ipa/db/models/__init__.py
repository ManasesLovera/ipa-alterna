"""ORM model package; importing it registers every table on `Base.metadata`."""

from __future__ import annotations

from ipa.db.models.chunk import EMBED_DIM, Chunk
from ipa.db.models.document import Document, DocumentEvent, DocumentPage, DocumentStep
from ipa.db.models.extraction import ExtractionVersion, Validation
from ipa.db.models.system import VECTOR_CONFIG_KEY, IdempotencyKey, SchemaConfig
from ipa.db.models.tag import Tag, TagField, TagVersion
from ipa.db.models.user import ApiKey, User
from ipa.db.models.webhook import Webhook, WebhookDelivery

__all__ = [
    "EMBED_DIM",
    "VECTOR_CONFIG_KEY",
    "ApiKey",
    "Chunk",
    "Document",
    "DocumentEvent",
    "DocumentPage",
    "DocumentStep",
    "ExtractionVersion",
    "IdempotencyKey",
    "SchemaConfig",
    "Tag",
    "TagField",
    "TagVersion",
    "User",
    "Validation",
    "Webhook",
    "WebhookDelivery",
]
