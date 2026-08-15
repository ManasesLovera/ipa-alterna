"""Domain error hierarchy and the RFC 7807 problem+json mapping.

Every error raised across layer boundaries is an `IpaError`. The API turns those
into `application/problem+json` responses; workers use `retryable` to decide
whether a step should be retried.
"""

from __future__ import annotations

from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import structlog

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse

logger = structlog.get_logger(__name__)

PROBLEM_CONTENT_TYPE = "application/problem+json"
PROBLEM_TYPE_PREFIX = "urn:ipa:error:"


class IpaError(Exception):
    """Base class for every error the platform raises deliberately.

    Attributes:
        code: Stable machine-readable error code returned to clients.
        http_status: HTTP status used when the error surfaces through the API.
        detail: Human-readable explanation of this particular occurrence.
        retryable: Whether retrying the same operation may succeed.
        extra: Additional members merged into the problem+json body.
    """

    code: str = "internal_error"
    http_status: int = 500
    title: str = "Internal Server Error"
    retryable: bool = False

    def __init__(
        self,
        detail: str | None = None,
        *,
        code: str | None = None,
        http_status: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """Initialise the error.

        Args:
            detail: Human-readable explanation; defaults to the class title.
            code: Overrides the class-level error code.
            http_status: Overrides the class-level HTTP status.
            extra: Extra members to include in the problem+json body.
        """
        self.detail = detail or self.title
        self.code = code or type(self).code
        self.http_status = http_status or type(self).http_status
        self.extra: dict[str, Any] = extra or {}
        super().__init__(self.detail)

    def to_problem(self, instance: str | None = None) -> dict[str, Any]:
        """Render this error as an RFC 7807 problem document.

        Args:
            instance: URI reference of the request that produced the error.

        Returns:
            A JSON-serialisable problem+json body.
        """
        problem: dict[str, Any] = {
            "type": f"{PROBLEM_TYPE_PREFIX}{self.code}",
            "title": self.title,
            "status": self.http_status,
            "detail": self.detail,
            "instance": instance,
            "code": self.code,
        }
        problem.update(self.extra)
        return problem


class NotFoundError(IpaError):
    """A requested resource does not exist."""

    code = "not_found"
    http_status = 404
    title = "Not Found"


class ConflictError(IpaError):
    """The request conflicts with the current state of the resource."""

    code = "conflict"
    http_status = 409
    title = "Conflict"


class ValidationError(IpaError):
    """Input failed domain or schema validation."""

    code = "validation_error"
    http_status = 422
    title = "Validation Error"


class UnsupportedMediaError(IpaError):
    """The uploaded media type or size is not accepted."""

    code = "unsupported_media"
    http_status = 415
    title = "Unsupported Media Type"


class QuarantineError(IpaError):
    """A document is corrupt, encrypted or otherwise unprocessable."""

    code = "quarantined"
    http_status = 422
    title = "Document Quarantined"


class ProviderError(IpaError):
    """An upstream model or service call failed permanently."""

    code = "provider_error"
    http_status = 502
    title = "Provider Error"


class RetryableProviderError(ProviderError):
    """An upstream call failed transiently and should be retried."""

    code = "provider_unavailable"
    http_status = 503
    title = "Provider Temporarily Unavailable"
    retryable = True


class StepFailedError(IpaError):
    """A pipeline step could not complete."""

    code = "step_failed"
    http_status = 500
    title = "Pipeline Step Failed"


class ConfigurationError(IpaError):
    """Configuration is missing or inconsistent; the process cannot start."""

    code = "configuration_error"
    http_status = 500
    title = "Configuration Error"


def problem_from_exception(exc: Exception, instance: str | None = None) -> dict[str, Any]:
    """Map any exception to an RFC 7807 problem document.

    Unknown exceptions collapse to a generic 500 so internals never leak.

    Args:
        exc: The exception to map.
        instance: URI reference of the request that produced the error.

    Returns:
        A JSON-serialisable problem+json body.
    """
    if isinstance(exc, IpaError):
        return exc.to_problem(instance)
    return IpaError("An unexpected error occurred.").to_problem(instance)


def install_exception_handlers(app: FastAPI) -> None:
    """Register the problem+json exception handlers on a FastAPI app.

    Handles `IpaError`, FastAPI's `HTTPException` and request validation errors,
    and any unhandled exception, so every error response has the same shape.

    Args:
        app: The FastAPI application to register handlers on.

    Returns:
        None.
    """
    from fastapi.encoders import jsonable_encoder
    from fastapi.exceptions import RequestValidationError
    from fastapi.responses import JSONResponse

    # Starlette's HTTPException is the base of FastAPI's and is what the router
    # itself raises (404, 405), so registering on it covers both.
    from starlette.exceptions import HTTPException

    def _respond(problem: dict[str, Any]) -> JSONResponse:
        return JSONResponse(
            status_code=int(problem["status"]),
            content=jsonable_encoder(problem),
            media_type=PROBLEM_CONTENT_TYPE,
        )

    async def _handle_ipa_error(request: Request, exc: Exception) -> JSONResponse:
        error = cast(IpaError, exc)
        logger.warning(
            "request.error", code=error.code, status=error.http_status, detail=error.detail
        )
        return _respond(error.to_problem(str(request.url)))

    async def _handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
        http_exc = cast(HTTPException, exc)
        mapped = IpaError(
            detail=str(http_exc.detail),
            code=f"http_{http_exc.status_code}",
            http_status=http_exc.status_code,
        )
        mapped.title = HTTPStatus(http_exc.status_code).phrase
        return _respond(mapped.to_problem(str(request.url)))

    async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
        validation_exc = cast(RequestValidationError, exc)
        mapped = ValidationError(
            "The request body or parameters failed validation.",
            extra={"errors": validation_exc.errors()},
        )
        return _respond(mapped.to_problem(str(request.url)))

    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("request.unhandled_error", error=type(exc).__name__)
        return _respond(problem_from_exception(exc, str(request.url)))

    app.add_exception_handler(IpaError, _handle_ipa_error)
    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(Exception, _handle_unexpected)
