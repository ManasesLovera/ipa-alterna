"""FastAPI application factory.

Wires configuration, structured logging, telemetry, the RFC 7807 error handlers,
CORS and the liveness/readiness probes. Feature routers are mounted from
`ipa.api.routers.ROUTERS`, which later tasks append to.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Response, status
from fastapi.middleware.cors import CORSMiddleware

from ipa.api.routers import ROUTERS
from ipa.core.config import Settings, get_settings
from ipa.core.errors import install_exception_handlers
from ipa.core.logging import configure_logging
from ipa.core.otel import setup_telemetry, shutdown_telemetry

logger = structlog.get_logger(__name__)

API_PREFIX = "/v1"
PROBE_TIMEOUT_S = 3.0


async def _check_postgres(settings: Settings) -> None:
    """Open and close a PostgreSQL connection.

    Args:
        settings: Application settings.

    Returns:
        None.

    Raises:
        Exception: Any driver error, surfaced by the caller as "unavailable".
    """
    import asyncpg

    dsn = settings.postgres.dsn.replace("+asyncpg", "").replace("+psycopg", "")
    connection = await asyncpg.connect(dsn)
    try:
        await connection.execute("SELECT 1")
    finally:
        await connection.close()


async def _check_mongo(settings: Settings) -> None:
    """Ping MongoDB.

    Args:
        settings: Application settings.

    Returns:
        None.

    Raises:
        Exception: Any driver error, surfaced by the caller as "unavailable".
    """
    from motor.motor_asyncio import AsyncIOMotorClient

    client: AsyncIOMotorClient[dict[str, Any]] = AsyncIOMotorClient(
        settings.mongo.uri, serverSelectionTimeoutMS=int(PROBE_TIMEOUT_S * 1000)
    )
    try:
        await client.admin.command("ping")
    finally:
        client.close()


async def _check_redis(settings: Settings) -> None:
    """Ping Redis.

    Args:
        settings: Application settings.

    Returns:
        None.

    Raises:
        Exception: Any driver error, surfaced by the caller as "unavailable".
    """
    from redis.asyncio import Redis

    client = Redis.from_url(settings.redis.url)
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _check_s3(settings: Settings) -> None:
    """Call the MinIO liveness endpoint.

    Args:
        settings: Application settings.

    Returns:
        None.

    Raises:
        Exception: Any transport error, surfaced by the caller as "unavailable".
    """
    import httpx

    url = f"{settings.s3.endpoint.rstrip('/')}/minio/health/live"
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_S) as client:
        response = await client.get(url)
        response.raise_for_status()


async def _probe(
    name: str, check: Callable[[Settings], Awaitable[None]], settings: Settings
) -> str:
    """Run one dependency probe and translate the outcome to a status string.

    Args:
        name: Dependency name, used in the log record.
        check: Coroutine function performing the probe.
        settings: Application settings.

    Returns:
        "ok" when the dependency answered, "unavailable" otherwise.
    """
    try:
        await asyncio.wait_for(check(settings), timeout=PROBE_TIMEOUT_S)
    except Exception as exc:
        logger.warning("readyz.dependency_unavailable", dependency=name, error=str(exc))
        return "unavailable"
    return "ok"


def create_app() -> FastAPI:
    """Build and configure the FastAPI application.

    Returns:
        A fully wired application instance.

    Raises:
        ConfigurationError: If the environment is misconfigured.
    """
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)
    settings.validate_startup()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Log the application lifecycle and flush telemetry on shutdown."""
        logger.info("api.started", env=settings.env, routers=len(ROUTERS))
        yield
        shutdown_telemetry()
        logger.info("api.stopped")

    app = FastAPI(
        title="IPA — Intelligent Process Automation",
        version="0.1.0",
        summary="Document ingestion, extraction, validation and retrieval.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origin_list),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    install_exception_handlers(app)

    # Instrument before the ASGI stack is frozen at startup: middleware added
    # from the lifespan handler would never be applied.
    setup_telemetry(settings.otel.service_name, app=app)

    @app.get("/healthz", tags=["ops"], summary="Liveness probe")
    async def healthz() -> dict[str, str]:
        """Report that the process is up. Never touches a dependency.

        Returns:
            A payload with the service status and version.
        """
        return {"status": "ok", "version": app.version, "env": settings.env}

    @app.get("/readyz", tags=["ops"], summary="Readiness probe")
    async def readyz(response: Response) -> dict[str, Any]:
        """Report whether every backing service is reachable.

        Args:
            response: Injected so the status code can be set to 503.

        Returns:
            Per-dependency status plus an overall verdict.
        """
        names = ("postgres", "mongo", "redis", "s3")
        checks = (_check_postgres, _check_mongo, _check_redis, _check_s3)
        results = await asyncio.gather(
            *(_probe(name, check, settings) for name, check in zip(names, checks, strict=True))
        )
        dependencies = dict(zip(names, results, strict=True))
        ready = all(value == "ok" for value in dependencies.values())
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "ready" if ready else "degraded", "dependencies": dependencies}

    for router in ROUTERS:
        app.include_router(router, prefix=API_PREFIX)

    return app


app = create_app()
