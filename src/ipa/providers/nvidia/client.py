"""Shared NVIDIA client factory, retry wrapper and concurrency limiter.

NVIDIA NIM is OpenAI-compatible; every NVIDIA provider uses one `AsyncOpenAI`
client built from `NvidiaSettings`. Calls go through `run_with_retries`, which
applies exponential backoff with full jitter on transient failures (429, 5xx,
connection and timeout errors), honours `Retry-After` when present, and emits an
OpenTelemetry span per attempt. A shared `asyncio.Semaphore` caps concurrent
in-flight calls so a burst of pages does not blow the rate limit.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import structlog
from openai import AsyncOpenAI

from ipa.core.config import NvidiaSettings
from ipa.core.errors import ProviderError, RetryableProviderError
from ipa.core.otel import get_tracer

logger = structlog.get_logger(__name__)

# HTTP status codes that warrant a retry: 429 (rate limited) and every 5xx.
_RETRYABLE_STATUS = {429} | set(range(500, 600))

DEFAULT_CONCURRENCY = 4

_shared_client: AsyncOpenAI | None = None
_shared_semaphore: asyncio.Semaphore | None = None


@dataclass(frozen=True)
class CompletionUsage:
    """Token accounting returned by a model call."""

    prompt_tokens: int | None = None
    completion_tokens: int | None = None


@dataclass(frozen=True)
class RawCompletion:
    """The verbatim result of one model call, before any parsing."""

    text: str
    model: str
    usage: CompletionUsage


def build_client(settings: NvidiaSettings) -> AsyncOpenAI:
    """Return a configured `AsyncOpenAI` client for NVIDIA NIM.

    `max_retries=0` because the `openai` SDK's own retry would bypass our
    retry/backoff policy and the OTel per-attempt spans; retries are applied by
    `run_with_retries` instead.

    Args:
        settings: NVIDIA provider settings.

    Returns:
        An `AsyncOpenAI` client pointed at `NVIDIA_BASE_URL`.
    """
    return AsyncOpenAI(
        api_key=settings.api_key,
        base_url=settings.base_url,
        timeout=settings.timeout_s,
        max_retries=0,
    )


def get_client(settings: NvidiaSettings) -> AsyncOpenAI:
    """Return the process-wide NVIDIA client, creating it on first use.

    Args:
        settings: NVIDIA provider settings.

    Returns:
        The shared `AsyncOpenAI` client.
    """
    global _shared_client
    if _shared_client is None:
        _shared_client = build_client(settings)
    return _shared_client


def get_semaphore(max_concurrency: int = DEFAULT_CONCURRENCY) -> asyncio.Semaphore:
    """Return the process-wide concurrency limiter.

    Args:
        max_concurrency: Capacity of the semaphore when created.

    Returns:
        The shared `asyncio.Semaphore`.
    """
    global _shared_semaphore
    if _shared_semaphore is None:
        _shared_semaphore = asyncio.Semaphore(max_concurrency)
    return _shared_semaphore


def is_retryable_status(status: int) -> bool:
    """Report whether an HTTP status warrants a retry.

    Args:
        status: The HTTP status code.

    Returns:
        True for 429 and every 5xx.
    """
    return status in _RETRYABLE_STATUS


async def run_with_retries[T](
    operation: str,
    call: Callable[[], Awaitable[T]],
    *,
    settings: NvidiaSettings,
    model: str,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> T:
    """Run a provider call with retries, backoff, concurrency limiting and a span.

    Raises `RetryableProviderError` when attempts are exhausted on a retryable
    failure class, and `ProviderError` for permanent failures. The pipeline
    distinguishes the two to decide whether to retry a step.

    Args:
        operation: Short label for the operation, used in spans and messages.
        call: The async function performing the provider call.
        settings: NVIDIA settings carrying retry and timeout policy.
        model: Model ID, recorded as a span attribute.
        concurrency: Maximum in-flight calls allowed.

    Returns:
        The result of `call`.

    Raises:
        RetryableProviderError: If all attempts fail on retryable errors.
        ProviderError: If a non-retryable error occurs, or retries are exhausted
            on a retryable error after `NVIDIA_MAX_RETRIES` attempts.
    """
    tracer = get_tracer(__name__)
    max_attempts = max(settings.max_retries + 1, 1)
    semaphore = get_semaphore(concurrency)
    last_error: Exception | None = None

    for attempt in range(1, max_attempts + 1):
        latency_ms: int | None = None
        with tracer.start_as_current_span(f"nvidia.{operation}") as span:
            span.set_attribute("provider", "nvidia")
            span.set_attribute("model", model)
            span.set_attribute("operation", operation)
            span.set_attribute("attempt", attempt)
            try:
                async with semaphore:
                    import time

                    start = time.monotonic()
                    result = await call()
                    latency_ms = int((time.monotonic() - start) * 1000)
                span.set_attribute("latency_ms", latency_ms)
                return result
            except Exception as exc:
                retryable, retry_after = _classify(exc)
                span.set_attribute("retryable", retryable)
                last_error = exc
                if not retryable:
                    raise ProviderError(
                        f"{operation} failed permanently: {exc}",
                        code="provider_error",
                    ) from exc
                if attempt >= max_attempts:
                    break
                delay = _backoff(settings, attempt, retry_after)
                logger.warning(
                    "provider.retry",
                    operation=operation,
                    attempt=attempt,
                    max_attempts=max_attempts,
                    delay_s=delay,
                    error=str(exc),
                )
                await asyncio.sleep(delay)

    assert last_error is not None  # unreachable: loop either returns or breaks on last_error
    raise RetryableProviderError(
        f"{operation} failed after {max_attempts} attempts: {last_error}",
        code="provider_unavailable",
    ) from last_error


def _classify(exc: Exception) -> tuple[bool, float | None]:
    """Decide whether an exception is transient and extract a retry hint.

    Args:
        exc: The exception raised by the provider call.

    Returns:
        A tuple of `(retryable, retry_after_seconds)`.
    """
    import openai

    retry_after: float | None = None
    if isinstance(exc, openai.RateLimitError):
        retry_after = _retry_after_from(exc)
        return True, retry_after
    if isinstance(exc, openai.APIConnectionError):
        return True, None
    if isinstance(exc, openai.APIStatusError):
        return is_retryable_status(exc.status_code), retry_after
    if isinstance(exc, openai.APITimeoutError):
        return True, None
    return False, None


def _retry_after_from(exc: Exception) -> float | None:
    """Read the `Retry-After` header from a rate-limit exception.

    Args:
        exc: The exception, expected to expose a `headers` mapping.

    Returns:
        Seconds to wait, or None when the header is absent or malformed.
    """
    headers = getattr(exc, "headers", None) or {}
    value = headers.get("retry-after") or headers.get("Retry-After")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _backoff(settings: NvidiaSettings, attempt: int, retry_after: float | None) -> float:
    """Compute the sleep before the next attempt.

    Uses exponential backoff with full jitter, clamped to at most 15 minutes.
    A server-provided `Retry-After` overrides the computed value.

    Args:
        settings: NVIDIA settings carrying the base backoff.
        attempt: The attempt that just failed (1-based).
        retry_after: Server-provided retry hint in seconds, if any.

    Returns:
        Seconds to sleep.
    """
    if retry_after is not None:
        return retry_after
    base = 1.0 * (2 ** (attempt - 1))
    cap = 15 * 60
    bounded = min(base, cap)
    return random.uniform(0, bounded)
