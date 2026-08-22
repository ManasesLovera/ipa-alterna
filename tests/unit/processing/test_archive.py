"""Archive listing and extraction with zip-bomb and traversal defences."""

from __future__ import annotations

from pathlib import Path

import pytest

from ipa.core.errors import QuarantineError
from ipa.processing.archive import extract_archive, list_archive

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


def _read(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def test_list_archive_returns_entries() -> None:
    entries = list_archive(_read("benign.zip"))

    assert len(entries) >= 2
    assert entries[0].name == "hello.txt"
    assert entries[0].size > 0


def test_extract_benign_archive() -> None:
    allowed = {"application/pdf", "text/plain"}
    files = extract_archive(_read("benign.zip"), allowed)

    assert len(files) == 2
    names = {f.name for f in files}
    assert "hello.txt" in names
    assert "invoice.pdf" in names


def test_zip_bomb_raises_quarantine() -> None:
    allowed = {"text/plain"}

    with pytest.raises(QuarantineError):
        extract_archive(_read("zipbomb.zip"), allowed)


def test_traversal_raises_quarantine() -> None:
    with pytest.raises(QuarantineError) as excinfo:
        extract_archive(_read("traversal.zip"), {"text/plain"})

    assert excinfo.value.code == "path_traversal"


def test_too_many_entries_raises_quarantine() -> None:
    allowed = {"text/plain"}
    limits = {"max_entries": 1}

    with pytest.raises(QuarantineError) as excinfo:
        extract_archive(_read("benign.zip"), allowed, limits)

    assert excinfo.value.code == "too_many_entries"


def test_invalid_archive_raises_quarantine() -> None:
    with pytest.raises(QuarantineError):
        extract_archive(b"not a zip", {"text/plain"})


def test_disallowed_entry_type_raises_quarantine() -> None:
    allowed = {"image/png"}  # benign.zip contains pdf + text

    with pytest.raises(QuarantineError) as excinfo:
        extract_archive(_read("benign.zip"), allowed)

    assert excinfo.value.code == "disallowed_entry_type"
