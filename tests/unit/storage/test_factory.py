"""Factory singletons and storage health checks."""

from __future__ import annotations

import pytest

from ipa.core.errors import IpaError
from ipa.storage import factory, health


class FakeBlobStore:
    """Blob store double for health checks."""

    def __init__(self, exists: bool = True) -> None:
        self.bucket_name = "ipa-documents"
        self._exists = exists

    async def bucket_exists(self) -> bool:
        return self._exists


class BrokenBlobStore(FakeBlobStore):
    """Blob store double whose check fails."""

    async def bucket_exists(self) -> bool:
        raise IpaError("MinIO bucket_exists failed: connection refused")


class FakePingStore:
    """Ping-able store double for content/cache health checks."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def ping(self) -> None:
        if self._fail:
            raise RuntimeError("unreachable")
        return None


async def test_singletons_are_cached_and_resettable() -> None:
    await factory.close_stores()

    blob_a, blob_b = factory.get_blob_store(), factory.get_blob_store()
    content_a, content_b = factory.get_content_store(), factory.get_content_store()
    cache_a, cache_b = factory.get_cache_store(), factory.get_cache_store()

    assert blob_a is blob_b
    assert content_a is content_b
    assert cache_a is cache_b

    await factory.close_stores()

    assert factory.get_blob_store() is not blob_a
    await factory.close_stores()


def test_fastapi_dependencies_return_the_singletons() -> None:
    assert factory.blob_store_dep() is factory.get_blob_store()
    assert factory.content_store_dep() is factory.get_content_store()
    assert factory.cache_store_dep() is factory.get_cache_store()


async def test_check_blob_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "get_blob_store", lambda: FakeBlobStore(True))

    ok, detail = await health.check_blob()

    assert ok is True
    assert "reachable" in detail


async def test_check_blob_reports_missing_bucket(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "get_blob_store", lambda: FakeBlobStore(False))

    ok, detail = await health.check_blob()

    assert ok is False
    assert "missing" in detail


async def test_check_blob_reports_outage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "get_blob_store", lambda: BrokenBlobStore())

    ok, detail = await health.check_blob()

    assert ok is False
    assert "unavailable" in detail


async def test_check_content_ok_and_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "get_content_store", lambda: FakePingStore())

    ok, detail = await health.check_content()
    assert ok is True
    assert "mongo" in detail

    monkeypatch.setattr(health, "get_content_store", lambda: FakePingStore(fail=True))

    ok, detail = await health.check_content()
    assert ok is False
    assert "unavailable" in detail


async def test_check_cache_ok_and_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health, "get_cache_store", lambda: FakePingStore())

    ok, detail = await health.check_cache()
    assert ok is True
    assert "redis" in detail

    monkeypatch.setattr(health, "get_cache_store", lambda: FakePingStore(fail=True))

    ok, detail = await health.check_cache()
    assert ok is False
    assert "unavailable" in detail
