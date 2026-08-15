"""Shared pytest fixtures.

Unit tests never touch a real service; anything external is mocked.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from ipa.core.config import get_settings

IPA_ENV_PREFIXES = ("IPA_", "NVIDIA_", "OTEL_")


@pytest.fixture(autouse=True)
def clean_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Isolate every test from the developer's environment and settings cache.

    Args:
        monkeypatch: Pytest environment patcher.

    Yields:
        None, with all IPA/NVIDIA/OTEL variables removed and the cache cleared.
    """
    import os

    for name in list(os.environ):
        if name.startswith(IPA_ENV_PREFIXES):
            monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
