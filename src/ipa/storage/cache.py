"""Redis cache adapter: JSON cache, idempotency registry, distributed locks.

Degradation policy: the cache is an optimisation, so `get_json`/`set_json`/
`delete` swallow connection errors (returning None / logging a warning) — a
Redis outage must not fail a request. Idempotency claims and locks guard
correctness, so those raise instead of degrading.
"""

from __future__ import annotations

import functools
import json
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any
from uuid import uuid4

import structlog
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from ipa.contracts.protocols import CacheStore
from ipa.core.config import RedisSettings, get_settings
from ipa.storage.cache_keys import build_key

logger = structlog.get_logger(__name__)

_DEGRADED_ERRORS = (RedisConnectionError, RedisTimeoutError)
"""Errors a cache read/write degrades on instead of failing the request."""

_RELEASE_LOCK_SCRIPT = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) "
    "else return 0 end"
)
"""Compare-and-delete: release the lock only when we still own it."""


class RedisCacheStore:
    """`CacheStore` over `redis.asyncio`."""

    def __init__(
        self,
        *,
        client: Redis | None = None,
        env: str | None = None,
        settings: RedisSettings | None = None,
    ) -> None:
        """Initialise the store.

        Args:
            client: Pre-built async Redis client; built from `settings.url`
                when None (tests inject `fakeredis` here).
            env: Environment segment of the key namespace; the process setting
                when None.
            settings: Redis settings; the process settings when None.
        """
        resolved = settings if settings is not None else get_settings().redis
        self._env = env if env is not None else get_settings().env
        self._client = client if client is not None else Redis.from_url(resolved.url)

    def key(self, kind: str, identifier: str) -> str:
        """Return the namespaced key for a kind and identifier.

        Args:
            kind: Key category, e.g. `idem` or `lock`.
            identifier: Unique remainder of the key.

        Returns:
            The full key `ipa:{env}:{kind}:{identifier}`.
        """
        return build_key(self._env, kind, identifier)

    async def get_json(self, key: str) -> dict[str, Any] | None:
        """Read a cached JSON value, degrading quietly on a Redis outage.

        Args:
            key: Cache key.

        Returns:
            The decoded value, or None on a miss or connection failure.
        """
        try:
            raw = await self._client.get(key)
        except _DEGRADED_ERRORS as exc:
            logger.warning("cache.get_unavailable", key=key, error=str(exc))
            return None
        if raw is None:
            return None
        return json.loads(raw)

    async def set_json(self, key: str, value: dict[str, Any], ttl_s: int) -> None:
        """Cache a JSON value with an expiry, swallowing connection failures.

        Args:
            key: Cache key.
            value: JSON-serialisable value.
            ttl_s: Time to live in seconds.

        Returns:
            None.
        """
        try:
            await self._client.set(key, json.dumps(value), ex=ttl_s)
        except _DEGRADED_ERRORS as exc:
            logger.warning("cache.set_unavailable", key=key, error=str(exc))

    async def delete(self, key: str) -> None:
        """Remove a key, succeeding if it is already absent.

        Args:
            key: Cache key.

        Returns:
            None.
        """
        try:
            await self._client.delete(key)
        except _DEGRADED_ERRORS as exc:
            logger.warning("cache.delete_unavailable", key=key, error=str(exc))

    async def claim_idempotency(self, key: str, ttl_s: int) -> bool:
        """Atomically claim an idempotency key.

        Does **not** degrade: silently skipping the claim on a Redis outage
        would allow duplicate processing, so connection errors propagate.

        Args:
            key: Idempotency key, typically from the `Idempotency-Key` header.
            ttl_s: How long the claim is held.

        Returns:
            True when this caller won the claim, False when it was already held.
        """
        won = await self._client.set(self.key("idem", key), "claimed", nx=True, ex=ttl_s)
        return bool(won)

    async def lock(self, key: str, ttl_s: int) -> AbstractAsyncContextManager[bool]:
        """Return an async context manager holding a distributed lock.

        Await this method to obtain the context manager, per the `CacheStore`
        protocol. Non-blocking: the context yields False when another holder
        already has the lock. Release is token-checked via a Lua script, so an
        expired lock that someone else re-acquired is never stolen by our exit
        path. Does **not** degrade on a Redis outage.

        Args:
            key: Lock name.
            ttl_s: Lock lease in seconds; the lock auto-expires.

        Returns:
            A context manager yielding True when the lock was acquired.
        """

        @asynccontextmanager
        async def _ctx() -> AsyncIterator[bool]:
            token = uuid4().hex
            full_key = self.key("lock", key)
            acquired = await self._client.set(full_key, token, nx=True, ex=ttl_s)
            try:
                yield bool(acquired)
            finally:
                if acquired:
                    try:
                        release: Any = self._client.eval(_RELEASE_LOCK_SCRIPT, 1, full_key, token)
                        await release
                    except _DEGRADED_ERRORS as exc:
                        # Best effort: the lease expires on its own anyway.
                        logger.warning("cache.lock_release_failed", key=key, error=str(exc))

        return _ctx()

    async def ping(self) -> None:
        """Ping the server; used by readiness checks.

        Returns:
            None.

        Raises:
            Exception: Any driver error, surfaced by the health checker.
        """
        await self._client.ping()

    async def aclose(self) -> None:
        """Close the underlying client. Call once on process shutdown.

        Returns:
            None.
        """
        await self._client.aclose()


def _default_cache() -> CacheStore:
    """Resolve the process-wide cache store lazily.

    The import is deferred because `ipa.storage.factory` imports this module;
    importing it at module level would create a cycle.

    Returns:
        The cache store singleton from the factory.
    """
    from ipa.storage.factory import get_cache_store

    return get_cache_store()


def cached_json(
    key_fn: Callable[..., str | None],
    ttl_s: int,
    store: CacheStore | None = None,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Decorate an async service method whose dict result can be cached.

    Args:
        key_fn: Builds the cache key from the call's arguments; returning None
            skips caching for that call (e.g. uncacheable arguments).
        ttl_s: Cache entry lifetime in seconds.
        store: Cache store to use; the process singleton when None.

    Returns:
        The decorator wrapping the method with read-through caching.
    """

    def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
        """Bind the decorator to one function.

        Args:
            fn: Async function returning a JSON-serialisable value.

        Returns:
            The wrapped async function.
        """

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            cache = store if store is not None else _default_cache()
            key = key_fn(*args, **kwargs)
            if key is None:
                return await fn(*args, **kwargs)
            cached = await cache.get_json(key)
            if cached is not None:
                return cached
            result = await fn(*args, **kwargs)
            if isinstance(result, dict):
                await cache.set_json(key, result, ttl_s)
            return result

        return wrapper

    return decorator
