"""cached_json decorator behaviour."""

from __future__ import annotations

from typing import Any

import pytest

from ipa.storage import cache as cache_module
from ipa.storage.cache import cached_json


class RecordingStore:
    """CacheStore double recording reads, writes and stored values."""

    def __init__(self) -> None:
        self.values: dict[str, dict[str, Any]] = {}
        self.gets = 0
        self.sets = 0

    async def get_json(self, key: str) -> dict[str, Any] | None:
        self.gets += 1
        return self.values.get(key)

    async def set_json(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        self.sets += 1
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)

    async def claim_idempotency(self, key: str, ttl_s: int) -> bool:
        return True

    def lock(self, key: str, ttl_s: int) -> Any:
        raise NotImplementedError


async def test_cached_json_fills_then_hits() -> None:
    store = RecordingStore()
    calls: list[str] = []

    @cached_json(lambda doc_id: f"ipa:test:doc:{doc_id}", 30, store=store)
    async def load(doc_id: str) -> dict[str, int]:
        calls.append(doc_id)
        return {"n": len(calls)}

    first = await load("d1")
    second = await load("d1")

    assert first == {"n": 1}
    assert second == {"n": 1}
    assert calls == ["d1"]
    assert store.sets == 1


async def test_cached_json_distinguishes_keys() -> None:
    store = RecordingStore()

    @cached_json(lambda doc_id: f"ipa:test:doc:{doc_id}", 30, store=store)
    async def load(doc_id: str) -> dict[str, str]:
        return {"doc": doc_id}

    assert await load("d1") == {"doc": "d1"}
    assert await load("d2") == {"doc": "d2"}
    assert store.sets == 2


async def test_cached_json_skips_caching_when_key_fn_returns_none() -> None:
    store = RecordingStore()
    calls: list[str] = []

    @cached_json(lambda doc_id: None, 30, store=store)
    async def load(doc_id: str) -> dict[str, int]:
        calls.append(doc_id)
        return {"n": len(calls)}

    await load("d1")
    await load("d1")

    assert calls == ["d1", "d1"]
    assert store.sets == 0


async def test_cached_json_does_not_cache_non_dict_results() -> None:
    store = RecordingStore()
    calls = 0

    @cached_json(lambda: "ipa:test:list", 30, store=store)
    async def load() -> list[int]:
        nonlocal calls
        calls += 1
        return [calls]

    assert await load() == [1]
    assert await load() == [2]  # not cached: non-dict results bypass the cache
    assert store.sets == 0


async def test_cached_json_uses_default_store_from_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = RecordingStore()
    monkeypatch.setattr(cache_module, "_default_cache", lambda: store)

    @cached_json(lambda: "ipa:test:default", 30)
    async def load() -> dict[str, bool]:
        return {"ok": True}

    await load()
    result = await load()

    assert result == {"ok": True}
    assert store.sets == 1
