"""Cached storage singletons and FastAPI dependencies.

Both the API and Celery tasks resolve adapters through these functions so a
process holds at most one client pool per backend. The singletons are plain
module state (not `lru_cache`) because adapters are stateful objects that need
explicit teardown.
"""

from __future__ import annotations

from ipa.core.config import get_settings
from ipa.storage.blob import MinioBlobStore
from ipa.storage.cache import RedisCacheStore
from ipa.storage.content import MongoContentStore

_blob_store: MinioBlobStore | None = None
_content_store: MongoContentStore | None = None
_cache_store: RedisCacheStore | None = None


def get_blob_store() -> MinioBlobStore:
    """Return the process-wide MinIO blob store, creating it on first use.

    Returns:
        The blob store singleton.
    """
    global _blob_store
    if _blob_store is None:
        _blob_store = MinioBlobStore(get_settings().s3)
    return _blob_store


def get_content_store() -> MongoContentStore:
    """Return the process-wide MongoDB content store, creating it on first use.

    Returns:
        The content store singleton.
    """
    global _content_store
    if _content_store is None:
        _content_store = MongoContentStore()
    return _content_store


def get_cache_store() -> RedisCacheStore:
    """Return the process-wide Redis cache store, creating it on first use.

    Returns:
        The cache store singleton.
    """
    global _cache_store
    if _cache_store is None:
        _cache_store = RedisCacheStore()
    return _cache_store


async def init_storage() -> None:
    """Create the blob bucket and every MongoDB index. Idempotent.

    Both are startup responsibilities: without the unique index on
    `(document_id, version)` two concurrent workers can insert the same
    extraction version, and without the TTL index `raw_responses` grows without
    bound. Compose seeds the bucket via its `minio-init` service, but a
    deployment that is not compose has nothing else to create it.

    TODO(T01): call this from the API lifespan and from Celery worker startup —
    `src/ipa/api/main.py` and the worker bootstrap are T01-owned paths.

    Returns:
        None.

    Raises:
        IpaError: If the bucket or the indexes cannot be created.
    """
    await get_blob_store().ensure_bucket()
    await get_content_store().ensure_indexes()


async def close_stores() -> None:
    """Close every singleton's client and forget the instances.

    Used on process shutdown and between tests. Blob clients hold no pool and
    need no teardown. A failure closing one client must neither leak the others
    nor leave a closed client installed as the singleton, so the reset runs
    under `finally`.

    Returns:
        None.
    """
    global _blob_store, _content_store, _cache_store
    try:
        if _cache_store is not None:
            await _cache_store.aclose()
        if _content_store is not None:
            _content_store.close()
    finally:
        _blob_store = None
        _content_store = None
        _cache_store = None


def blob_store_dep() -> MinioBlobStore:
    """FastAPI dependency yielding the blob store singleton.

    Returns:
        The blob store.
    """
    return get_blob_store()


def content_store_dep() -> MongoContentStore:
    """FastAPI dependency yielding the content store singleton.

    Returns:
        The content store.
    """
    return get_content_store()


def cache_store_dep() -> RedisCacheStore:
    """FastAPI dependency yielding the cache store singleton.

    Returns:
        The cache store.
    """
    return get_cache_store()
