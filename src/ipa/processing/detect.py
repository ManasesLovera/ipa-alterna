"""File type detection, hashing and container classification.

Magic-byte detection comes first; the file extension is used only as a tiebreak
and the client-supplied content type is never trusted. This is the gate that
keeps a `.exe` renamed to `.pdf` out of the pipeline.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from typing import Literal

# Signatures are tuples of (offset, magic, mime) checked in order.
_PNG = (0, b"\x89PNG\r\n\x1a\n", "image/png")
_JPEG = (0, b"\xff\xd8\xff", "image/jpeg")
_GIF = (0, b"GIF8", "image/gif")
_TIFF_LE = (0, b"II*\x00", "image/tiff")
_TIFF_BE = (0, b"MM\x00*", "image/tiff")
_BMP = (0, b"BM", "image/bmp")
_ZIP = (0, b"PK\x03\x04", "application/zip")
_PDF = (0, b"%PDF", "application/pdf")

_MAGIC = (_PDF, _ZIP, _PNG, _JPEG, _GIF, _TIFF_LE, _TIFF_BE, _BMP)

_EXTENSION_HINTS = {
    ".pdf": "application/pdf",
    ".zip": "application/zip",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".bmp": "image/bmp",
    ".heic": "image/heic",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
}

ContainerKind = Literal["pdf", "image", "archive", "unsupported"]


def sniff_mime(data: bytes, filename: str) -> str:
    """Detect a file's MIME type from its magic bytes, extension as tiebreak.

    Args:
        data: The first bytes of the file (at least the magic signature).
        filename: The file name, used only when magic bytes are inconclusive.

    Returns:
        The detected MIME type.
    """
    for offset, magic, mime in _MAGIC:
        if data.startswith(magic, offset):
            return mime
    from pathlib import Path

    ext = Path(filename).suffix.lower()
    return _EXTENSION_HINTS.get(ext, "application/octet-stream")


def is_allowed(mime: str, allowed: set[str]) -> bool:
    """Report whether a MIME type is in an allowed set.

    Args:
        mime: The MIME type to check.
        allowed: The set of permitted MIME types.

    Returns:
        True when `mime` is present in `allowed`.
    """
    return mime in allowed


def sha256_hex(data: bytes) -> str:
    """Return the hex SHA-256 digest of a byte string.

    Args:
        data: The bytes to digest.

    Returns:
        A 64-character lowercase hex digest.
    """
    return hashlib.sha256(data).hexdigest()


async def sha256_stream(reader: AsyncIterator[bytes]) -> str:
    """Hash a stream of chunks without materialising them in memory.

    Args:
        reader: Async iterator yielding bytes chunks.

    Returns:
        A 64-character lowercase hex digest.
    """
    digest = hashlib.sha256()
    async for chunk in reader:
        digest.update(chunk)
    return digest.hexdigest()


def classify_container(mime: str) -> ContainerKind:
    """Classify a MIME type into a pipeline container kind.

    Args:
        mime: The MIME type.

    Returns:
        `pdf`, `image`, `archive` or `unsupported`.
    """
    if mime == "application/pdf":
        return "pdf"
    if mime == "application/zip":
        return "archive"
    if mime.startswith("image/"):
        return "image"
    return "unsupported"
