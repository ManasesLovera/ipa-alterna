"""Identifier and blob-key helpers.

The blob key scheme is a shared contract (see `docs/01-conventions.md`); build
keys through these helpers rather than formatting strings at call sites.
"""

from __future__ import annotations

import hashlib
import uuid
from uuid import UUID


def new_id() -> UUID:
    """Return a fresh random identifier.

    Returns:
        A UUID4.
    """
    return uuid.uuid4()


def sha256_hex(data: bytes) -> str:
    """Return the hex SHA-256 digest of a byte string.

    Args:
        data: The bytes to digest.

    Returns:
        A 64-character lowercase hex digest.
    """
    return hashlib.sha256(data).hexdigest()


def original_key(sha256: str) -> str:
    """Return the content-addressed blob key of an original upload.

    Args:
        sha256: Hex digest of the file bytes.

    Returns:
        A key of the form `originals/{sha[:2]}/{sha}`.
    """
    return f"originals/{sha256[:2]}/{sha256}"


def page_image_key(document_id: UUID, page: int) -> str:
    """Return the blob key of a rendered page image.

    Args:
        document_id: Owning document.
        page: 1-based page number.

    Returns:
        A key of the form `pages/{document_id}/{page:05d}.png`.
    """
    return f"pages/{document_id}/{page:05d}.png"


def thumbnail_key(document_id: UUID, page: int) -> str:
    """Return the blob key of a page thumbnail.

    Args:
        document_id: Owning document.
        page: 1-based page number.

    Returns:
        A key of the form `thumbs/{document_id}/{page:05d}.jpg`.
    """
    return f"thumbs/{document_id}/{page:05d}.jpg"
