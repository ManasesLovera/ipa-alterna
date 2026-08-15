"""Repository package; one module per aggregate, all returning DTOs."""

from __future__ import annotations

from ipa.db.repositories.chunk import ChunkRepository
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.event import EventRepository
from ipa.db.repositories.extraction import ExtractionRepository
from ipa.db.repositories.step import StepRepository
from ipa.db.repositories.tag import TagRepository
from ipa.db.repositories.user import UserRepository
from ipa.db.repositories.webhook import WebhookRepository

__all__ = [
    "ChunkRepository",
    "DocumentRepository",
    "EventRepository",
    "ExtractionRepository",
    "StepRepository",
    "TagRepository",
    "UserRepository",
    "WebhookRepository",
]
