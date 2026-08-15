"""MinIO / S3 blob adapter.

Implementation choice: the **sync `minio` SDK run through `asyncio.to_thread`**.
`aioboto3` was considered and rejected — it drags in a second S3 client stack
(boto3) next to `minio`, while the thread-offload keeps one dependency and the
SDK's blocking calls off the event loop. Thread-pool usage is bounded by the
executor default and calls are short-lived network round trips.

Large uploads spool to a temporary file (`SpooledTemporaryFile`) rather than
RAM, so a 200 MB original is never fully resident in this process on top of
the caller's own copy.
"""

from __future__ import annotations

import asyncio
import io
import tempfile
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import BinaryIO, cast

import structlog
from minio import Minio
from minio.error import S3Error

from ipa.core.config import S3Settings
from ipa.core.errors import IpaError, NotFoundError

logger = structlog.get_logger(__name__)

SPOOL_MAX_BYTES = 8 * 1024 * 1024
"""Bytes buffered in memory before a streamed upload spills to disk."""

RETRYABLE_S3_CODES = frozenset(
    {"InternalError", "ServiceUnavailable", "SlowDown", "RequestTimeout"}
)
"""S3 error codes worth retrying; surfaced via `IpaError.retryable`."""


def _blob_error(operation: str, exc: Exception) -> IpaError:
    """Translate a MinIO SDK failure into an `IpaError`.

    Args:
        operation: Name of the failing operation, included in the message.
        exc: The original SDK exception.

    Returns:
        The error to raise; `retryable` is set for transient S3 codes.
    """
    error = IpaError(f"MinIO {operation} failed: {exc}", code="blob_store_error")
    if isinstance(exc, S3Error) and exc.code in RETRYABLE_S3_CODES:
        error.retryable = True
    return error


def _host_of(endpoint: str) -> str:
    """Strip the scheme from an endpoint URL.

    Args:
        endpoint: Endpoint URL such as `http://minio:9000`.

    Returns:
        The `host:port` string the MinIO SDK expects.
    """
    return endpoint.removeprefix("https://").removeprefix("http://")


class MinioBlobStore:
    """`BlobStore` over MinIO / any S3-compatible object store."""

    def __init__(
        self,
        settings: S3Settings,
        *,
        client: Minio | None = None,
        presign_client: Minio | None = None,
        region: str = "us-east-1",
    ) -> None:
        """Initialise the store.

        Args:
            settings: S3 connection settings.
            client: Pre-built SDK client; constructed from `settings` when None
                (tests inject a double here).
            presign_client: Client used to presign download URLs; defaults to a
                client built from `IPA_S3_PUBLIC_ENDPOINT` when set, otherwise
                the primary client.
            region: Bucket region pinned on the clients. minio-py resolves the
                region over HTTP on first presign when it is not pinned, which
                would make `presigned_url` a network call.
        """
        self._settings = settings
        self._bucket = settings.bucket
        self._client = client or Minio(
            settings.host,
            access_key=settings.access_key,
            secret_key=settings.secret_key,
            secure=settings.secure,
            region=region,
        )
        if presign_client is not None:
            self._presign_client = presign_client
        elif settings.public_endpoint:
            self._presign_client = Minio(
                _host_of(settings.public_endpoint),
                access_key=settings.access_key,
                secret_key=settings.secret_key,
                secure=settings.public_endpoint.startswith("https://"),
                region=region,
            )
        else:
            self._presign_client = self._client

    @property
    def bucket_name(self) -> str:
        """Return the bucket this store reads and writes."""
        return self._bucket

    async def ensure_bucket(self) -> None:
        """Create the configured bucket if it does not exist. Idempotent.

        Returns:
            None.

        Raises:
            IpaError: If the bucket cannot be checked or created.
        """
        try:
            exists = await asyncio.to_thread(self._client.bucket_exists, self._bucket)
            if not exists:
                await asyncio.to_thread(self._client.make_bucket, self._bucket)
                logger.info("blob.bucket_created", bucket=self._bucket)
        except Exception as exc:
            raise _blob_error("ensure_bucket", exc) from exc

    async def bucket_exists(self) -> bool:
        """Report whether the configured bucket exists.

        Returns:
            True when the bucket is present and the service answers.

        Raises:
            IpaError: If the check itself fails.
        """
        try:
            return bool(await asyncio.to_thread(self._client.bucket_exists, self._bucket))
        except Exception as exc:
            raise _blob_error("bucket_exists", exc) from exc

    async def put(self, key: str, data: bytes, content_type: str) -> str:
        """Store bytes under `key`, skipping the write when the key exists.

        Keys for originals are content-addressed (see `ipa.storage.keys`), so an
        existing key already holds identical content and rewriting it only
        costs bandwidth; treating it as success is what makes re-upload cheap.

        Args:
            key: Blob key, built with `ipa.storage.keys`.
            data: Raw object bytes.
            content_type: MIME type recorded on the object.

        Returns:
            The key the object was stored under.

        Raises:
            IpaError: If the object store rejects the write.
        """
        if await self.exists(key):
            return key
        try:
            await asyncio.to_thread(
                self._client.put_object,
                self._bucket,
                key,
                io.BytesIO(data),
                length=len(data),
                content_type=content_type,
            )
        except Exception as exc:
            raise _blob_error("put", exc) from exc
        return key

    async def put_stream(
        self, key: str, reader: AsyncIterator[bytes], content_type: str, size: int | None = None
    ) -> str:
        """Store a streamed object without materialising it in memory.

        Same skip-if-exists semantics as `put`; when the key already exists the
        reader is not consumed. Chunks spool to memory up to `SPOOL_MAX_BYTES`
        and then to a temporary file, so huge uploads never fully occupy RSS.

        Args:
            key: Blob key.
            reader: Async iterator yielding chunks of object bytes.
            content_type: MIME type recorded on the object.
            size: Total size in bytes when known; a mismatch fails the upload.

        Returns:
            The key the object was stored under.

        Raises:
            IpaError: If the stream is shorter/longer than `size` or the store
                rejects the write.
        """
        if await self.exists(key):
            return key
        with tempfile.SpooledTemporaryFile(max_size=SPOOL_MAX_BYTES) as spool:
            try:
                written = 0
                async for chunk in reader:
                    spool.write(chunk)
                    written += len(chunk)
                if size is not None and size != written:
                    raise IpaError(
                        f"Streamed {written} bytes for {key} but caller declared {size}.",
                        code="blob_store_error",
                    )
                spool.seek(0)
                await asyncio.to_thread(
                    self._client.put_object,
                    self._bucket,
                    key,
                    cast("BinaryIO", spool),
                    length=written,
                    content_type=content_type,
                )
            except IpaError:
                raise
            except Exception as exc:
                raise _blob_error("put_stream", exc) from exc
        return key

    async def get_stream(self, key: str, chunk_size: int = 1024 * 1024) -> AsyncIterator[bytes]:
        """Stream an object's bytes.

        Args:
            key: Blob key.
            chunk_size: Bytes per yielded chunk.

        Yields:
            Chunks of the object's bytes.

        Raises:
            NotFoundError: If the key does not exist.
            IpaError: If the read fails.
        """
        response = None
        try:
            response = await asyncio.to_thread(self._client.get_object, self._bucket, key)
            while True:
                chunk = await asyncio.to_thread(response.read, chunk_size)
                if not chunk:
                    break
                yield chunk
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                raise NotFoundError(f"Blob {key} does not exist.") from exc
            raise _blob_error("get_stream", exc) from exc
        except Exception as exc:
            raise _blob_error("get_stream", exc) from exc
        finally:
            if response is not None:
                response.close()
                response.release_conn()

    async def get(self, key: str) -> bytes:
        """Fetch an object's bytes.

        Args:
            key: Blob key.

        Returns:
            The object bytes.

        Raises:
            NotFoundError: If no object exists under `key`.
            IpaError: If the read fails.
        """
        chunks: list[bytes] = []
        async for chunk in self.get_stream(key):
            chunks.append(chunk)
        return b"".join(chunks)

    async def exists(self, key: str) -> bool:
        """Report whether an object exists.

        Args:
            key: Blob key.

        Returns:
            True when the object is present.

        Raises:
            IpaError: If the stat call fails for a reason other than absence.
        """
        try:
            await asyncio.to_thread(self._client.stat_object, self._bucket, key)
            return True
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                return False
            raise _blob_error("exists", exc) from exc
        except Exception as exc:
            raise _blob_error("exists", exc) from exc

    async def presigned_url(self, key: str, expires_s: int = 900) -> str:
        """Return a time-limited download URL for an object.

        Signs with `IPA_S3_PUBLIC_ENDPOINT` when set, because the internal
        endpoint (e.g. `http://minio:9000`) is not resolvable from a browser.

        Args:
            key: Blob key.
            expires_s: Lifetime of the URL in seconds.

        Returns:
            A presigned HTTP URL.

        Raises:
            IpaError: If signing fails.
        """
        try:
            return str(
                await asyncio.to_thread(
                    self._presign_client.presigned_get_object,
                    self._bucket,
                    key,
                    timedelta(seconds=expires_s),
                )
            )
        except Exception as exc:
            raise _blob_error("presigned_url", exc) from exc

    async def delete(self, key: str) -> None:
        """Delete an object, succeeding if it is already absent.

        Args:
            key: Blob key.

        Returns:
            None.

        Raises:
            IpaError: If the delete fails for a reason other than absence.
        """
        try:
            await asyncio.to_thread(self._client.remove_object, self._bucket, key)
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                return
            raise _blob_error("delete", exc) from exc
        except Exception as exc:
            raise _blob_error("delete", exc) from exc
