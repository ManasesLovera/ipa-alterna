"""Startup verification of build-time schema configuration.

The vector column dimension is chosen at migration time from settings and
recorded in `schema_config`. When an operator changes `NVIDIA_EMBED_DIM`
without resizing the index, every embedding write would fail — so startup
checks the recorded value against configuration and refuses to run.
"""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from ipa.core.errors import ConfigurationError
from ipa.db.models import VECTOR_CONFIG_KEY, SchemaConfig
from ipa.db.models.chunk import EMBED_DIM


async def verify_schema_config(engine: AsyncEngine) -> None:
    """Verify the configured embedding dimension matches the deployed schema.

    Args:
        engine: Engine bound to the database the process will use.

    Raises:
        ConfigurationError: When the `schema_config` row is missing, or the
            recorded dimension differs from `NVIDIA_EMBED_DIM`.
    """
    async with engine.connect() as connection:
        value = (
            await connection.execute(
                sa.select(SchemaConfig.value).where(SchemaConfig.key == VECTOR_CONFIG_KEY)
            )
        ).scalar_one_or_none()
    if value is None:
        raise ConfigurationError(
            "schema_config is missing the vector dimension row; run `make migrate`."
        )
    recorded = _recorded_dim(value)
    if recorded != EMBED_DIM:
        raise ConfigurationError(
            f"NVIDIA_EMBED_DIM={EMBED_DIM} but the database vector column was "
            f"built with dimension {recorded}. Revert the setting or migrate "
            "the chunks table to the new dimension."
        )


def _recorded_dim(value: dict[str, Any]) -> int | None:
    """Extract the recorded embedding dimension from a config row value.

    Args:
        value: The jsonb payload of the `schema_config.vector` row.

    Returns:
        The recorded dimension, or None when the payload is malformed.
    """
    raw = value.get("embed_dim")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None
