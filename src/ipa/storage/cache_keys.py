"""Namespaced Redis cache key builders.

All cache keys follow `ipa:{env}:{kind}:{id}` so environments sharing one Redis
never collide and every key is greppable. Build keys through these helpers or
`RedisCacheStore.key`, never by hand.
"""

from __future__ import annotations

from uuid import UUID

from ipa.core.config import get_settings

PREFIX = "ipa"


def build_key(env: str, kind: str, identifier: str) -> str:
    """Build a namespaced key without touching settings.

    Args:
        env: Environment name, e.g. `local` or `test`.
        kind: Key category, e.g. `extraction` or `lock`.
        identifier: Unique remainder of the key.

    Returns:
        The full key `ipa:{env}:{kind}:{identifier}`.
    """
    return f"{PREFIX}:{env}:{kind}:{identifier}"


def cache_key(kind: str, identifier: str) -> str:
    """Build a namespaced key for the current environment.

    Args:
        kind: Key category, e.g. `extraction`.
        identifier: Unique remainder of the key.

    Returns:
        The full key `ipa:{env}:{kind}:{identifier}`.
    """
    return build_key(get_settings().env, kind, identifier)


def extraction(document_id: UUID, version: int) -> str:
    """Return the cache key for one extraction version.

    Args:
        document_id: Owning document.
        version: Extraction version.

    Returns:
        The namespaced cache key.
    """
    return cache_key("extraction", f"{document_id}:v{version}")


def tag_schema(tag_id: UUID, version: int) -> str:
    """Return the cache key for one tag schema version.

    Args:
        tag_id: Owning tag.
        version: Schema version.

    Returns:
        The namespaced cache key.
    """
    return cache_key("tag_schema", f"{tag_id}:v{version}")


def search(query_hash: str) -> str:
    """Return the cache key for one search result set.

    Args:
        query_hash: Digest identifying the query and its filters.

    Returns:
        The namespaced cache key.
    """
    return cache_key("search", query_hash)


def document_status(document_id: UUID) -> str:
    """Return the cache key for a document's status projection.

    Args:
        document_id: Owning document.

    Returns:
        The namespaced cache key.
    """
    return cache_key("document_status", str(document_id))
