"""Settings loading, aliases, derived values and startup validation."""

from __future__ import annotations

import pytest

from ipa.core.config import Settings, get_settings
from ipa.core.errors import ConfigurationError


def test_defaults_are_usable_without_any_environment() -> None:
    settings = Settings(_env_file=None)

    assert settings.env == "local"
    assert settings.api_port == 8000
    assert settings.postgres.dsn.startswith("postgresql+asyncpg://")
    assert settings.nvidia.llm_model == ""
    assert settings.otel.enabled is False


def test_env_prefix_and_explicit_aliases(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IPA_ENV", "test")
    monkeypatch.setenv("IPA_MAX_UPLOAD_MB", "5")
    monkeypatch.setenv("IPA_POSTGRES_DSN", "postgresql+asyncpg://u:p@db:5432/x")
    monkeypatch.setenv("IPA_MONGO_DB", "ipa_test")
    monkeypatch.setenv("IPA_CELERY_BROKER_URL", "redis://queue:6379/9")
    monkeypatch.setenv("IPA_S3_SECURE", "true")
    monkeypatch.setenv("NVIDIA_LLM_MODEL", "meta/llama-3.1-70b-instruct")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector:4317")

    settings = Settings(_env_file=None)

    assert settings.env == "test"
    assert settings.max_upload_mb == 5
    assert settings.postgres.dsn == "postgresql+asyncpg://u:p@db:5432/x"
    assert settings.mongo.database == "ipa_test"
    assert settings.redis.celery_broker_url == "redis://queue:6379/9"
    assert settings.s3.secure is True
    assert settings.nvidia.llm_model == "meta/llama-3.1-70b-instruct"
    assert settings.otel.enabled is True


def test_derived_values() -> None:
    settings = Settings(_env_file=None)

    assert settings.max_upload_bytes == 200 * 1024 * 1024
    assert "application/pdf" in settings.allowed_mime_types
    assert settings.allowed_mime_types[-1] == "application/zip"
    assert settings.s3.host == "minio:9000"
    assert settings.postgres.sync_dsn.startswith("postgresql+psycopg://")
    assert settings.is_production is False


def test_get_settings_is_cached() -> None:
    assert get_settings() is get_settings()


def test_startup_passes_when_features_are_disabled() -> None:
    Settings(_env_file=None).validate_startup()


def test_startup_fails_when_enabled_feature_has_no_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IPA_FEATURE_EXTRACTION_ENABLED", "true")

    with pytest.raises(ConfigurationError) as excinfo:
        Settings(_env_file=None).validate_startup()

    assert "NVIDIA_LLM_MODEL" in str(excinfo.value)


def test_startup_fails_when_model_is_set_without_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IPA_FEATURE_EMBEDDING_ENABLED", "true")
    monkeypatch.setenv("NVIDIA_EMBED_MODEL", "nvidia/nv-embed-v1")

    with pytest.raises(ConfigurationError, match="NVIDIA_API_KEY"):
        Settings(_env_file=None).validate_startup()


def test_startup_rejects_placeholder_secret_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("IPA_ENV", "production")

    with pytest.raises(ConfigurationError, match="IPA_SECRET_KEY"):
        Settings(_env_file=None).validate_startup()


def test_require_model_returns_configured_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    monkeypatch.setenv("NVIDIA_VLM_MODEL", "nvidia/neva-22b")

    assert Settings(_env_file=None).nvidia.require_model("vlm") == "nvidia/neva-22b"
