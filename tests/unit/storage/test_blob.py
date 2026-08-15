"""MinioBlobStore behaviour against a fake MinIO SDK client."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import pytest
from minio.error import S3Error

from ipa.contracts.protocols import BlobStore
from ipa.core.config import S3Settings
from ipa.core.errors import IpaError, NotFoundError
from ipa.storage.blob import MinioBlobStore


def _s3_error(code: str, resource: str) -> S3Error:
    """Build an S3Error the way the SDK raises it.

    Args:
        code: S3 error code, e.g. "NoSuchKey".
        resource: Resource the error refers to.

    Returns:
        The error instance.
    """
    return S3Error(None, code, "fake error", resource, "request-id", "host-id")


class FakeResponse:
    """Stand-in for the urllib3 response returned by Minio.get_object."""

    def __init__(self, payload: bytes) -> None:
        self._payload = payload
        self._position = 0
        self.closed = False
        self.released = False

    def read(self, amount: int | None = -1) -> bytes:
        if amount is None or amount < 0:
            chunk = self._payload[self._position :]
        else:
            chunk = self._payload[self._position : self._position + amount]
        self._position += len(chunk)
        return chunk

    def close(self) -> None:
        self.closed = True

    def release_conn(self) -> None:
        self.released = True


class FakeMinio:
    """In-memory Minio SDK double recording every mutating call."""

    def __init__(self, *, bucket_exists: bool = True) -> None:
        self.objects: dict[str, bytes] = {}
        self.put_calls: list[dict[str, Any]] = []
        self.presign_calls: list[tuple[str, timedelta]] = []
        self.made_buckets: list[str] = []
        self.bucket_always_exists = bucket_exists
        self.last_response: FakeResponse | None = None

    def bucket_exists(self, bucket: str) -> bool:
        return self.bucket_always_exists or bool(self.made_buckets)

    def make_bucket(self, bucket: str) -> None:
        self.made_buckets.append(bucket)

    def stat_object(self, bucket: str, key: str) -> object:
        if key not in self.objects:
            raise _s3_error("NoSuchKey", key)
        return object()

    def put_object(
        self,
        bucket: str,
        key: str,
        data: Any,
        length: int,
        content_type: str = "application/octet-stream",
        **kwargs: Any,
    ) -> None:
        payload = data.read()
        assert len(payload) == length
        self.put_calls.append(
            {"bucket": bucket, "key": key, "length": length, "content_type": content_type}
        )
        self.objects[key] = payload

    def get_object(self, bucket: str, key: str) -> FakeResponse:
        if key not in self.objects:
            raise _s3_error("NoSuchKey", key)
        self.last_response = FakeResponse(self.objects[key])
        return self.last_response

    def remove_object(self, bucket: str, key: str) -> None:
        if key not in self.objects:
            raise _s3_error("NoSuchKey", key)
        del self.objects[key]

    def presigned_get_object(self, bucket: str, key: str, expires: timedelta) -> str:
        self.presign_calls.append((key, expires))
        seconds = int(expires.total_seconds())
        return f"http://fake-minio/{bucket}/{key}?expires={seconds}"


def make_store(client: FakeMinio | None = None, **env: str) -> MinioBlobStore:
    """Build a store over a fake client with optional S3 env overrides.

    Args:
        client: Fake SDK client; a fresh one when None.
        **env: Extra S3Settings alias kwargs, e.g. IPA_S3_PUBLIC_ENDPOINT.

    Returns:
        The store under test.
    """
    settings = S3Settings(_env_file=None, **env)
    return MinioBlobStore(settings, client=client or FakeMinio())


async def test_store_satisfies_blob_store_protocol() -> None:
    assert isinstance(make_store(), BlobStore)


async def test_put_get_round_trip() -> None:
    store = make_store()

    key = await store.put("originals/ab/abc123", b"hello", "text/plain")

    assert key == "originals/ab/abc123"
    assert await store.exists(key) is True
    assert await store.get(key) == b"hello"


async def test_put_skips_write_when_key_already_exists() -> None:
    fake = FakeMinio()
    store = make_store(fake)

    await store.put("originals/ab/abc", b"one", "text/plain")
    await store.put("originals/ab/abc", b"two", "text/plain")

    assert len(fake.put_calls) == 1
    assert await store.get("originals/ab/abc") == b"one"


async def test_stream_round_trip() -> None:
    fake = FakeMinio()
    store = make_store(fake)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"aaaa"
        yield b"bbbb"
        yield b"cc"

    key = await store.put_stream("pages/doc/00001.png", chunks(), "image/png", size=10)

    assert key == "pages/doc/00001.png"
    assert fake.put_calls[0]["length"] == 10
    assert await store.get(key) == b"aaaabbbbcc"
    assert fake.last_response is not None
    assert fake.last_response.closed and fake.last_response.released


async def test_get_stream_honours_chunk_size() -> None:
    store = make_store()
    await store.put("k", b"abcdefgh", "text/plain")

    received = [chunk async for chunk in store.get_stream("k", chunk_size=3)]

    assert received == [b"abc", b"def", b"gh"]


async def test_put_stream_rejects_size_mismatch() -> None:
    fake = FakeMinio()
    store = make_store(fake)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"abc"

    with pytest.raises(IpaError):
        await store.put_stream("k", chunks(), "text/plain", size=5)

    assert fake.put_calls == []


async def test_put_stream_skips_write_when_key_exists() -> None:
    fake = FakeMinio()
    fake.objects["k"] = b"original"
    store = make_store(fake)

    async def chunks() -> AsyncIterator[bytes]:
        yield b"never read"

    assert await store.put_stream("k", chunks(), "text/plain") == "k"
    assert fake.put_calls == []
    assert await store.get("k") == b"original"


async def test_get_missing_raises_not_found() -> None:
    store = make_store()

    with pytest.raises(NotFoundError):
        await store.get("missing")
    with pytest.raises(NotFoundError):
        async for _ in store.get_stream("missing"):
            pass


async def test_exists_false_for_missing_key() -> None:
    assert await make_store().exists("missing") is False


async def test_delete_is_idempotent() -> None:
    store = make_store()
    await store.put("k", b"x", "text/plain")

    await store.delete("k")
    await store.delete("k")  # already absent — must not raise

    assert await store.exists("k") is False


async def test_ensure_bucket_is_idempotent() -> None:
    fake = FakeMinio(bucket_exists=False)
    store = make_store(fake)

    await store.ensure_bucket()
    await store.ensure_bucket()

    assert fake.made_buckets == ["ipa-documents"]


async def test_presigned_url_uses_primary_client_by_default() -> None:
    fake = FakeMinio()
    store = make_store(fake)

    url = await store.presigned_url("some/key", 60)

    assert url.startswith("http://fake-minio/ipa-documents/some/key")
    assert fake.presign_calls == [("some/key", timedelta(seconds=60))]


async def test_presigned_url_uses_public_endpoint_override() -> None:
    store = make_store(IPA_S3_PUBLIC_ENDPOINT="http://localhost:9000")

    url = await store.presigned_url("some/key", 60)

    assert url.startswith("http://localhost:9000/ipa-documents/some/key")
