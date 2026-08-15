"""Error hierarchy and the RFC 7807 mapping, both directly and through FastAPI."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import BaseModel

from ipa.core.errors import (
    PROBLEM_CONTENT_TYPE,
    ConflictError,
    IpaError,
    NotFoundError,
    ProviderError,
    RetryableProviderError,
    StepFailedError,
    UnsupportedMediaError,
    install_exception_handlers,
    problem_from_exception,
)


def test_problem_document_shape() -> None:
    problem = NotFoundError("document 42 does not exist").to_problem("/v1/documents/42")

    assert problem == {
        "type": "urn:ipa:error:not_found",
        "title": "Not Found",
        "status": 404,
        "detail": "document 42 does not exist",
        "instance": "/v1/documents/42",
        "code": "not_found",
    }


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (NotFoundError(), 404, "not_found"),
        (ConflictError(), 409, "conflict"),
        (UnsupportedMediaError(), 415, "unsupported_media"),
        (ProviderError(), 502, "provider_error"),
        (RetryableProviderError(), 503, "provider_unavailable"),
        (StepFailedError(), 500, "step_failed"),
    ],
)
def test_status_and_code_per_error(error: IpaError, status: int, code: str) -> None:
    problem = error.to_problem()

    assert problem["status"] == status
    assert problem["code"] == code


def test_only_retryable_provider_error_is_retryable() -> None:
    assert RetryableProviderError().retryable is True
    assert ProviderError().retryable is False


def test_extra_members_are_merged() -> None:
    problem = ConflictError("duplicate", extra={"document_id": "abc"}).to_problem()

    assert problem["document_id"] == "abc"


def test_unknown_exception_does_not_leak_internals() -> None:
    problem = problem_from_exception(RuntimeError("connection string user:password"))

    assert problem["status"] == 500
    assert problem["code"] == "internal_error"
    assert "password" not in problem["detail"]


class _Body(BaseModel):
    """Request body used to trigger FastAPI's validation error path."""

    page: int


def _app() -> FastAPI:
    """Build a tiny app wired with the shared exception handlers."""
    app = FastAPI()
    install_exception_handlers(app)

    @app.get("/missing")
    async def missing() -> None:
        raise NotFoundError("no such document")

    @app.post("/body")
    async def body(payload: _Body) -> dict[str, int]:
        return {"page": payload.page}

    return app


def test_ipa_error_becomes_problem_json() -> None:
    with TestClient(_app()) as client:
        response = client.get("/missing")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert response.json()["code"] == "not_found"


def test_router_404_becomes_problem_json() -> None:
    with TestClient(_app()) as client:
        response = client.get("/nowhere")

    assert response.status_code == 404
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert response.json()["code"] == "http_404"


def test_request_validation_becomes_problem_json() -> None:
    with TestClient(_app()) as client:
        response = client.post("/body", json={"page": "not-an-int"})

    body = response.json()
    assert response.status_code == 422
    assert response.headers["content-type"].startswith(PROBLEM_CONTENT_TYPE)
    assert body["code"] == "validation_error"
    assert body["errors"]
