"""Application factory, liveness and readiness behaviour."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ipa.api import main
from ipa.api.main import create_app
from ipa.api.routers import ROUTERS
from ipa.core.config import Settings


@pytest.fixture
def client() -> TestClient:
    """Return a test client over a freshly built application."""
    return TestClient(create_app())


def test_healthz_is_ok_without_any_dependency(client: TestClient) -> None:
    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_readyz_reports_every_dependency(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok(settings: Settings) -> None:
        return None

    for name in ("_check_postgres", "_check_mongo", "_check_redis", "_check_s3"):
        monkeypatch.setattr(main, name, ok)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "dependencies": {"postgres": "ok", "mongo": "ok", "redis": "ok", "s3": "ok"},
    }


def test_readyz_is_503_when_a_dependency_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def ok(settings: Settings) -> None:
        return None

    async def down(settings: Settings) -> None:
        raise ConnectionError("refused")

    for name in ("_check_postgres", "_check_mongo", "_check_s3"):
        monkeypatch.setattr(main, name, ok)
    monkeypatch.setattr(main, "_check_redis", down)

    response = client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["status"] == "degraded"
    assert response.json()["dependencies"]["redis"] == "unavailable"


def test_router_registry_is_appendable() -> None:
    assert isinstance(ROUTERS, list)
    assert len(ROUTERS) >= 1


def test_auth_requires_credentials() -> None:
    import asyncio

    from fastapi import Request

    from ipa.api.auth import require_auth
    from ipa.domain.users import AuthError

    request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})

    try:
        asyncio.run(require_auth(request))
    except AuthError:
        pass
    else:
        raise AssertionError("expected AuthError without credentials")
