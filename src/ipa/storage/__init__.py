"""Concrete storage adapters: MinIO blob store, MongoDB content store, Redis cache.

Services depend on the protocols in `ipa.contracts.protocols`; the classes here
satisfy them structurally. Resolve instances through `ipa.storage.factory`,
never by constructing adapters at call sites.
"""

from __future__ import annotations

from ipa.storage.blob import MinioBlobStore
from ipa.storage.cache import RedisCacheStore, cached_json
from ipa.storage.cache_keys import (
    document_status,
    extraction,
    search,
    tag_schema,
)
from ipa.storage.content import MongoContentStore
from ipa.storage.keys import original_key, page_key, thumb_key

__all__ = [
    "MinioBlobStore",
    "MongoContentStore",
    "RedisCacheStore",
    "cached_json",
    "document_status",
    "extraction",
    "original_key",
    "page_key",
    "search",
    "tag_schema",
    "thumb_key",
]
