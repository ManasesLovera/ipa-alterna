"""ZIP archive listing and extraction with zip-bomb and traversal defences.

A ZIP is a container, not a document. Expansion is bounded by hard limits on
total size, per-entry compression ratio, entry count and nesting depth, and every
entry's normalised path is checked so it cannot escape the extraction root.
Violations raise `QuarantineError` with a specific code; nothing is ever written
to disk.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

from ipa.core.errors import QuarantineError

DEFAULT_MAX_TOTAL = 1 << 30  # 1 GiB
DEFAULT_MAX_RATIO = 100
DEFAULT_MAX_ENTRIES = 500


@dataclass(frozen=True)
class ArchiveEntry:
    """One member of an archive.

    Attributes:
        name: The entry's path within the archive.
        size: Uncompressed size in bytes.
        compressed_size: Compressed size in bytes.
        encrypted: Whether the entry is password-protected.
    """

    name: str
    size: int
    compressed_size: int
    encrypted: bool


@dataclass(frozen=True)
class ExtractedFile:
    """One file extracted from an archive.

    Attributes:
        name: The entry's normalised path within the archive.
        data: The file's bytes.
        mime: Detected MIME type.
    """

    name: str
    data: bytes
    mime: str


class _Limits:
    """Bounded resource limits enforced during extraction."""

    def __init__(
        self,
        max_total: int = DEFAULT_MAX_TOTAL,
        max_ratio: int = DEFAULT_MAX_RATIO,
        max_entries: int = DEFAULT_MAX_ENTRIES,
    ) -> None:
        self.max_total = max_total
        self.max_ratio = max_ratio
        self.max_entries = max_entries
        self.total: int = 0
        self.count: int = 0


def list_archive(data: bytes) -> list[ArchiveEntry]:
    """List the members of a ZIP archive.

    Args:
        data: The ZIP bytes.

    Returns:
        A list of `ArchiveEntry` in archive order.

    Raises:
        QuarantineError: If the data is not a readable ZIP.
    """
    import zipfile

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise QuarantineError(
            "The archive is not a readable ZIP.", code="invalid_archive"
        ) from exc
    with archive:
        entries: list[ArchiveEntry] = []
        for info in archive.infolist():
            entries.append(
                ArchiveEntry(
                    name=info.filename,
                    size=info.file_size,
                    compressed_size=info.compress_size,
                    encrypted=bool(info.flag_bits & 0x1),
                )
            )
        return entries


def extract_archive(
    data: bytes,
    allowed_mimes: set[str],
    limits: dict[str, int] | None = None,
) -> list[ExtractedFile]:
    """Extract files from a ZIP, enforcing safety limits.

    Args:
        data: The ZIP bytes.
        allowed_mimes: MIME types whose extraction is permitted.
        limits: Overrides for `max_total`, `max_ratio` and `max_entries`.

    Returns:
        The extracted, MIME-checked files.

    Raises:
        QuarantineError: On any zip-bomb, traversal or invalid-input condition.
    """
    import zipfile

    from ipa.processing.detect import sniff_mime

    config = _Limits(**{**(limits or {})})
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise QuarantineError(
            "The archive is not a readable ZIP.", code="invalid_archive"
        ) from exc

    with archive:
        result: list[ExtractedFile] = []
        for info in archive.infolist():
            config.count += 1
            if config.count > config.max_entries:
                raise QuarantineError(
                    f"Archive has more than {config.max_entries} entries.",
                    code="too_many_entries",
                )
            if info.is_dir():
                continue
            safe_name = _safe_name(info.filename)
            if info.file_size > config.max_total - config.total:
                raise QuarantineError(
                    "Archive expands beyond the total size limit.",
                    code="zip_bomb",
                )
            if info.compress_size and info.file_size / info.compress_size > config.max_ratio:
                raise QuarantineError(
                    f"Entry {info.filename} has an implausible compression ratio.",
                    code="zip_bomb",
                )
            if info.flag_bits & 0x1:
                raise QuarantineError(
                    f"Archive entry {info.filename} is encrypted.",
                    code="encrypted_archive_entry",
                )
            try:
                file_bytes = archive.read(info)
            except RuntimeError as exc:
                raise QuarantineError(
                    f"Could not read archive entry {info.filename}.",
                    code="archive_read_error",
                ) from exc
            config.total += len(file_bytes)
            mime = sniff_mime(file_bytes, safe_name)
            if mime not in allowed_mimes:
                raise QuarantineError(
                    f"Archive entry {safe_name} has disallowed type {mime}.",
                    code="disallowed_entry_type",
                )
            result.append(ExtractedFile(name=safe_name, data=file_bytes, mime=mime))
        return result


def _safe_name(name: str) -> str:
    """Normalise an archive entry path and reject anything escaping the root.

    Args:
        name: The raw entry path from the archive.

    Returns:
        The cleaned, absolute-relative path.

    Raises:
        QuarantineError: If the path escapes the extraction root.
    """
    cleaned = name.replace("\\", "/")
    path = Path(cleaned)
    if path.is_absolute() or ".." in path.parts:
        raise QuarantineError(
            f"Archive entry {name} attempts a path traversal.", code="path_traversal"
        )
    return cleaned
