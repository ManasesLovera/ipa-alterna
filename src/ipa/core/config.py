"""Typed application configuration.

This is the **only** module in the codebase allowed to read the process
environment. Everything else calls `get_settings()` and receives an immutable,
validated `Settings` object.

Environment variable names are declared explicitly as aliases so that renaming a
Python attribute can never silently change the operator-facing contract.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

from ipa.core.errors import ConfigurationError

EnvName = Literal["local", "test", "staging", "production"]

_BASE_CONFIG = SettingsConfigDict(
    env_file=".env",
    env_file_encoding="utf-8",
    case_sensitive=False,
    extra="ignore",
)


class PostgresSettings(BaseSettings):
    """PostgreSQL connection settings. Source of truth for all platform state."""

    model_config = _BASE_CONFIG

    dsn: str = Field(
        default="postgresql+asyncpg://ipa:ipa@postgres:5432/ipa",
        validation_alias="IPA_POSTGRES_DSN",
    )
    pool_size: int = Field(default=10, ge=1, validation_alias="IPA_POSTGRES_POOL_SIZE")
    echo: bool = Field(default=False, validation_alias="IPA_POSTGRES_ECHO")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def sync_dsn(self) -> str:
        """Return the DSN with a synchronous driver, as required by Alembic."""
        return self.dsn.replace("+asyncpg", "+psycopg").replace(
            "postgresql://", "postgresql+psycopg://"
        )


class MongoSettings(BaseSettings):
    """MongoDB settings. Stores OCR page text and versioned extraction bodies."""

    model_config = _BASE_CONFIG

    uri: str = Field(default="mongodb://mongo:27017", validation_alias="IPA_MONGO_URI")
    database: str = Field(default="ipa", validation_alias="IPA_MONGO_DB")


class RedisSettings(BaseSettings):
    """Redis settings: cache, idempotency keys, locks and the Celery transport."""

    model_config = _BASE_CONFIG

    url: str = Field(default="redis://redis:6379/0", validation_alias="IPA_REDIS_URL")
    celery_broker_url: str = Field(
        default="redis://redis:6379/1", validation_alias="IPA_CELERY_BROKER_URL"
    )
    celery_result_backend: str = Field(
        default="redis://redis:6379/2", validation_alias="IPA_CELERY_RESULT_BACKEND"
    )
    cache_ttl_s: int = Field(default=3600, ge=0, validation_alias="IPA_CACHE_TTL_S")


class S3Settings(BaseSettings):
    """MinIO / S3 settings for original uploads, page images and thumbnails."""

    model_config = _BASE_CONFIG

    endpoint: str = Field(default="http://minio:9000", validation_alias="IPA_S3_ENDPOINT")
    public_endpoint: str = Field(
        default="",
        validation_alias="IPA_S3_PUBLIC_ENDPOINT",
        description=(
            "Browser-reachable endpoint used to presign download URLs; empty uses "
            "IPA_S3_ENDPOINT. Set when the endpoint is a docker-internal hostname."
        ),
    )
    access_key: str = Field(default="minioadmin", validation_alias="IPA_S3_ACCESS_KEY")
    secret_key: str = Field(default="minioadmin", validation_alias="IPA_S3_SECRET_KEY")
    bucket: str = Field(default="ipa-documents", validation_alias="IPA_S3_BUCKET")
    secure: bool = Field(default=False, validation_alias="IPA_S3_SECURE")
    presign_expiry_s: int = Field(default=900, ge=1, validation_alias="IPA_S3_PRESIGN_EXPIRY_S")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def host(self) -> str:
        """Return the endpoint without its scheme, as the MinIO SDK expects."""
        return self.endpoint.removeprefix("https://").removeprefix("http://")


class NvidiaSettings(BaseSettings):
    """NVIDIA NIM provider settings (OpenAI-compatible API).

    Model IDs ship blank on purpose: the operator picks them from the NVIDIA
    catalogue. `require_model()` turns a blank ID into a clear startup failure
    instead of an opaque 400 from the provider at run time.
    """

    model_config = _BASE_CONFIG

    api_key: str = Field(default="", validation_alias="NVIDIA_API_KEY")
    base_url: str = Field(
        default="https://integrate.api.nvidia.com/v1", validation_alias="NVIDIA_BASE_URL"
    )
    llm_model: str = Field(default="", validation_alias="NVIDIA_LLM_MODEL")
    vlm_model: str = Field(default="", validation_alias="NVIDIA_VLM_MODEL")
    embed_model: str = Field(default="", validation_alias="NVIDIA_EMBED_MODEL")
    embed_dim: int = Field(default=1024, ge=1, validation_alias="NVIDIA_EMBED_DIM")
    timeout_s: int = Field(default=120, ge=1, validation_alias="NVIDIA_TIMEOUT_S")
    max_retries: int = Field(default=3, ge=0, validation_alias="NVIDIA_MAX_RETRIES")

    def require_model(self, capability: Literal["llm", "vlm", "embed"]) -> str:
        """Return the configured model ID for a capability.

        Args:
            capability: Which model to resolve.

        Returns:
            The non-empty model identifier.

        Raises:
            ConfigurationError: If the model ID or the API key is blank.
        """
        env_var = f"NVIDIA_{capability.upper()}_MODEL"
        model = {"llm": self.llm_model, "vlm": self.vlm_model, "embed": self.embed_model}[
            capability
        ]
        if not model:
            raise ConfigurationError(
                f"{env_var} is empty. Set it to a model ID from the NVIDIA catalogue, "
                f"or disable the feature that needs it."
            )
        if not self.api_key:
            raise ConfigurationError("NVIDIA_API_KEY is empty but an NVIDIA model is in use.")
        return model


class OcrSettings(BaseSettings):
    """OCR resolution policy: native text layer, then VLM, then Tesseract."""

    model_config = _BASE_CONFIG

    text_layer_min_chars: int = Field(
        default=120, ge=0, validation_alias="IPA_OCR_TEXT_LAYER_MIN_CHARS"
    )
    page_dpi: int = Field(default=220, ge=36, validation_alias="IPA_OCR_PAGE_DPI")
    fallback_enabled: bool = Field(default=True, validation_alias="IPA_OCR_FALLBACK_ENABLED")
    tesseract_langs: str = Field(default="eng+spa", validation_alias="IPA_TESSERACT_LANGS")


class PipelineSettings(BaseSettings):
    """Retry policy and chunking parameters for the processing pipeline."""

    model_config = _BASE_CONFIG

    step_max_attempts: int = Field(default=3, ge=1, validation_alias="IPA_STEP_MAX_ATTEMPTS")
    step_retry_backoff_s: int = Field(default=15, ge=0, validation_alias="IPA_STEP_RETRY_BACKOFF_S")
    chunk_tokens: int = Field(default=512, ge=1, validation_alias="IPA_CHUNK_TOKENS")
    chunk_overlap: int = Field(default=64, ge=0, validation_alias="IPA_CHUNK_OVERLAP")
    max_pages: int = Field(default=1000, ge=1, validation_alias="IPA_MAX_PAGES")


class FeatureSettings(BaseSettings):
    """Feature switches that decide which NVIDIA models are mandatory.

    They default to off so a freshly cloned repo boots with blank model IDs.
    Turn one on and the matching model ID becomes required at startup.
    """

    model_config = _BASE_CONFIG

    extraction_enabled: bool = Field(
        default=False, validation_alias="IPA_FEATURE_EXTRACTION_ENABLED"
    )
    embedding_enabled: bool = Field(default=False, validation_alias="IPA_FEATURE_EMBEDDING_ENABLED")
    vlm_ocr_enabled: bool = Field(default=False, validation_alias="IPA_FEATURE_VLM_OCR_ENABLED")


class OtelSettings(BaseSettings):
    """OpenTelemetry export settings. Telemetry is a no-op when unset."""

    model_config = _BASE_CONFIG

    service_name: str = Field(default="ipa-api", validation_alias="OTEL_SERVICE_NAME")
    exporter_endpoint: str = Field(default="", validation_alias="OTEL_EXPORTER_OTLP_ENDPOINT")
    traces_sampler: str = Field(
        default="parentbased_always_on", validation_alias="OTEL_TRACES_SAMPLER"
    )
    console_export: bool = Field(default=False, validation_alias="IPA_OTEL_CONSOLE_EXPORT")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def enabled(self) -> bool:
        """Return True when an OTLP endpoint is configured."""
        return bool(self.exporter_endpoint)


class Settings(BaseSettings):
    """Root settings object; the single entry point to all configuration."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="IPA_",
        case_sensitive=False,
        extra="ignore",
    )

    env: EnvName = "local"
    log_level: str = Field(default="INFO")
    log_json: bool = Field(default=True, description="False renders human-readable logs.")
    api_port: int = Field(default=8000, ge=1, le=65535)
    secret_key: str = Field(default="change-me")
    max_upload_mb: int = Field(default=200, ge=1)
    allowed_mime: str = Field(
        default="application/pdf,image/png,image/jpeg,image/tiff,application/zip"
    )
    cors_origins: str = Field(default="http://localhost:3000")

    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    mongo: MongoSettings = Field(default_factory=MongoSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    s3: S3Settings = Field(default_factory=S3Settings)
    nvidia: NvidiaSettings = Field(default_factory=NvidiaSettings)
    ocr: OcrSettings = Field(default_factory=OcrSettings)
    pipeline: PipelineSettings = Field(default_factory=PipelineSettings)
    features: FeatureSettings = Field(default_factory=FeatureSettings)
    otel: OtelSettings = Field(default_factory=OtelSettings)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def allowed_mime_types(self) -> tuple[str, ...]:
        """Return the accepted upload MIME types as a tuple."""
        return tuple(item.strip() for item in self.allowed_mime.split(",") if item.strip())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def cors_origin_list(self) -> tuple[str, ...]:
        """Return the allowed CORS origins as a tuple."""
        return tuple(item.strip() for item in self.cors_origins.split(",") if item.strip())

    @computed_field  # type: ignore[prop-decorator]
    @property
    def max_upload_bytes(self) -> int:
        """Return the maximum accepted upload size in bytes."""
        return self.max_upload_mb * 1024 * 1024

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_production(self) -> bool:
        """Return True when running in a deployed, non-local environment."""
        return self.env in ("staging", "production")

    def validate_startup(self) -> None:
        """Fail fast on configuration that would break at run time.

        Checks that every enabled feature has its NVIDIA model ID set, and that
        production is not running with the placeholder secret key.

        Returns:
            None.

        Raises:
            ConfigurationError: If any required setting is missing or unsafe.
        """
        problems: list[str] = []
        required: list[tuple[bool, Literal["llm", "vlm", "embed"]]] = [
            (self.features.extraction_enabled, "llm"),
            (self.features.vlm_ocr_enabled, "vlm"),
            (self.features.embedding_enabled, "embed"),
        ]
        for enabled, capability in required:
            if not enabled:
                continue
            try:
                self.nvidia.require_model(capability)
            except ConfigurationError as exc:
                problems.append(exc.detail)

        if self.is_production and self.secret_key == "change-me":
            problems.append("IPA_SECRET_KEY still holds the placeholder value.")

        if problems:
            raise ConfigurationError("Invalid configuration: " + " ".join(problems))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton.

    The result is cached; tests must call `get_settings.cache_clear()` after
    mutating the environment.

    Returns:
        The validated `Settings` instance.
    """
    return Settings()
