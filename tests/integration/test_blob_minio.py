"""integration_light: MinioBlobStore round trip against the real MinIO service.

Runs in CI (MinIO started as a job step) and locally against `make up`; skips
when MinIO is not reachable so the rest of the integration suite still runs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import uuid4

import httpx
import pytest

from ipa.core.config import S3Settings
from ipa.storage.blob import MinioBlobStore

pytestmark = pytest.mark.integration_light


async def _minio_reachable(endpoint: str) -> bool:
    """Probe the MinIO liveness endpoint.

    Args:
        endpoint: Configured S3 endpoint URL.

    Returns:
        True when the service answers, False on any transport error.
    """
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            response = await client.get(f"{endpoint.rstrip('/')}/minio/health/live")
            return response.status_code == 200
    except httpx.HTTPError:
        return False


async def test_blob_round_trip_against_minio() -> None:
    settings = S3Settings(_env_file=None)
    if not await _minio_reachable(settings.endpoint):
        pytest.skip("MinIO is not reachable; start it with `make up` first")

    store = MinioBlobStore(settings)
    await store.ensure_bucket()

    key = f"test/t03/{uuid4()}.txt"
    payload = b"t03 integration round trip"

    assert await store.put(key, payload, "text/plain") == key
    assert await store.exists(key) is True
    assert await store.get(key) == payload

    streamed = b"".join([chunk async for chunk in store.get_stream(key, chunk_size=4)])
    assert streamed == payload

    url = await store.presigned_url(key, expires_s=60)
    async with httpx.AsyncClient(timeout=5.0) as client:
        response = await client.get(url)
    assert response.status_code == 200
    assert response.content == payload

    await store.delete(key)
    assert await store.exists(key) is False


async def test_put_stream_against_minio() -> None:
    settings = S3Settings(_env_file=None)
    if not await _minio_reachable(settings.endpoint):
        pytest.skip("MinIO is not reachable; start it with `make up` first")

    store = MinioBlobStore(settings)
    await store.ensure_bucket()

    key = f"test/t03/{uuid4()}.bin"
    payload = bytes(range(256)) * 1024  # 256 KiB

    async def chunks() -> AsyncIterator[bytes]:
        for start in range(0, len(payload), 4096):
            yield payload[start : start + 4096]

    await store.put_stream(key, chunks(), "application/octet-stream", size=len(payload))
    assert await store.get(key) == payload

    await store.delete(key)
