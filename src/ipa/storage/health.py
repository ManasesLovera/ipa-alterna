"""Storage readiness checks.

Standalone helper module on purpose: `ipa.api.main` (T01's `/readyz`) imports
*these* functions, and this module imports nothing from the API layer, so no
circular import is possible.
"""

from __future__ import annotations

import structlog

from ipa.storage.factory import get_blob_store, get_cache_store, get_content_store

logger = structlog.get_logger(__name__)


async def check_blob() -> tuple[bool, str]:
    """Check that the blob bucket exists and answers.

    Returns:
        `(ok, detail)` — ok is True only when the bucket is reachable.
    """
    try:
        store = get_blob_store()
        if await store.bucket_exists():
            return True, f"bucket {store.bucket_name} reachable"
        return False, f"bucket {store.bucket_name} missing"
    except Exception as exc:
        logger.warning("health.blob_unavailable", error=str(exc))
        return False, f"blob store unavailable: {exc}"


async def check_content() -> tuple[bool, str]:
    """Check that MongoDB answers a ping.

    Returns:
        `(ok, detail)` — ok is True only when the server answers.
    """
    try:
        await get_content_store().ping()
        return True, "mongo ping ok"
    except Exception as exc:
        logger.warning("health.content_unavailable", error=str(exc))
        return False, f"content store unavailable: {exc}"


async def check_cache() -> tuple[bool, str]:
    """Check that Redis answers a ping.

    Returns:
        `(ok, detail)` — ok is True only when the server answers.
    """
    try:
        await get_cache_store().ping()
        return True, "redis ping ok"
    except Exception as exc:
        logger.warning("health.cache_unavailable", error=str(exc))
        return False, f"cache store unavailable: {exc}"
