"""Dead-letter management for permanently failed steps."""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)

_STREAM = "ipa:dead_letters"


def list_dead_letters() -> list[dict[str, object]]:
    """List permanently failed steps from the dead-letter stream.

    Reads the most recent records from the Redis stream and returns them.

    Returns:
        A list of dead-letter records.
    """
    try:
        from redis.asyncio import Redis

        client = Redis.from_url(_stream_url())
        raw = client.xrevrange(_STREAM, max="-", min="+", count=100)
        records: list[dict[str, object]] = []
        for message_id, fields in raw:
            record = dict(fields)
            record["_id"] = message_id
            records.append(record)
        return records
    except Exception as exc:  # pragma: no cover - Redis may be unavailable
        logger.warning("dlq.unavailable", error=str(exc))
        return []


def _stream_url() -> str:
    """Return the Redis URL for the dead-letter stream.

    Returns:
        The configured Redis URL.
    """
    from ipa.core.config import get_settings

    return get_settings().redis.url
