"""RedisCacheStore behaviour against fakeredis, including outage degradation."""

from __future__ import annotations

import asyncio
from typing import Any, cast

import pytest
from fakeredis import FakeAsyncRedis
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError

from ipa.contracts.protocols import CacheStore
from ipa.storage.cache import RedisCacheStore


class UnreachableRedis:
    """Redis double whose every command fails as if the server were down."""

    async def _fail(self, *args: Any, **kwargs: Any) -> None:
        raise RedisConnectionError("connection refused")

    get = _fail
    set = _fail
    delete = _fail
    eval = _fail
    ping = _fail

    async def aclose(self) -> None:
        return None


@pytest.fixture
def store() -> RedisCacheStore:
    """Return a store backed by fakeredis in the "test" namespace."""
    return RedisCacheStore(client=cast(Redis, FakeAsyncRedis()), env="test")


async def test_store_satisfies_cache_store_protocol(store: RedisCacheStore) -> None:
    assert isinstance(store, CacheStore)


async def test_key_namespacing(store: RedisCacheStore) -> None:
    assert store.key("extraction", "abc") == "ipa:test:extraction:abc"
    assert store.key("lock", "doc-1") == "ipa:test:lock:doc-1"


async def test_json_round_trip_with_ttl(store: RedisCacheStore) -> None:
    await store.set_json("ipa:test:k:v1", {"a": 1, "b": [2, 3]}, ttl_s=60)

    assert await store.get_json("ipa:test:k:v1") == {"a": 1, "b": [2, 3]}
    ttl = await store._client.ttl("ipa:test:k:v1")
    assert 0 < ttl <= 60


async def test_get_json_missing_returns_none(store: RedisCacheStore) -> None:
    assert await store.get_json("ipa:test:k:absent") is None


async def test_get_json_undecodable_value_reads_as_a_miss(store: RedisCacheStore) -> None:
    await store._client.set("ipa:test:k:junk", b"{not json")

    assert await store.get_json("ipa:test:k:junk") is None


async def test_delete_succeeds(store: RedisCacheStore) -> None:
    await store.set_json("ipa:test:k:v1", {"a": 1}, ttl_s=60)
    await store.delete("ipa:test:k:v1")
    await store.delete("ipa:test:k:v1")  # already absent

    assert await store.get_json("ipa:test:k:v1") is None


async def test_claim_idempotency_won_once_only(store: RedisCacheStore) -> None:
    assert await store.claim_idempotency("upload-42", ttl_s=30) is True
    assert await store.claim_idempotency("upload-42", ttl_s=30) is False

    held = await store._client.get("ipa:test:idem:upload-42")
    assert held is not None
    ttl = await store._client.ttl("ipa:test:idem:upload-42")
    assert 0 < ttl <= 30


async def test_lock_admits_only_one_concurrent_holder(store: RedisCacheStore) -> None:
    outcomes: list[bool] = []

    async def worker() -> None:
        async with await store.lock("process:doc-1", ttl_s=10) as acquired:
            outcomes.append(acquired)
            await asyncio.sleep(0.05)

    await asyncio.gather(worker(), worker())

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1


async def test_lock_release_is_token_checked(store: RedisCacheStore) -> None:
    lock_key = store.key("lock", "process:doc-1")

    async with await store.lock("process:doc-1", ttl_s=10):
        # Simulate lease expiry and re-acquisition by another worker.
        await store._client.set(lock_key, "someone-elses-token")

    # Exiting our context must not have released the other holder's lock.
    assert await store._client.get(lock_key) == b"someone-elses-token"


async def test_lock_releases_own_key(store: RedisCacheStore) -> None:
    lock_key = store.key("lock", "process:doc-2")

    async with await store.lock("process:doc-2", ttl_s=10) as acquired:
        assert acquired is True

    assert await store._client.get(lock_key) is None


async def test_outage_degrades_cache_but_not_idempotency() -> None:
    down = RedisCacheStore(client=cast(Redis, UnreachableRedis()), env="test")

    assert await down.get_json("any") is None
    await down.set_json("any", {"a": 1}, ttl_s=60)  # must not raise
    await down.delete("any")  # must not raise

    with pytest.raises(RedisConnectionError):
        await down.claim_idempotency("any", ttl_s=60)
    with pytest.raises(RedisConnectionError):
        async with await down.lock("any", ttl_s=10):
            pass


async def test_ping(store: RedisCacheStore) -> None:
    await store.ping()
