"""Startup schema verification against a real PostgreSQL."""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncEngine

from ipa.core.errors import ConfigurationError
from ipa.db.models import VECTOR_CONFIG_KEY, SchemaConfig
from ipa.db.verify import _recorded_dim, verify_schema_config

pytestmark = pytest.mark.integration_light


async def test_verify_passes_when_dimension_matches(db_engine: AsyncEngine) -> None:
    await verify_schema_config(db_engine)


async def test_verify_rejects_dimension_mismatch(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            sa.update(SchemaConfig)
            .where(SchemaConfig.key == VECTOR_CONFIG_KEY)
            .values(value={"embed_dim": 768})
        )
    try:
        with pytest.raises(ConfigurationError):
            await verify_schema_config(db_engine)
    finally:
        async with db_engine.begin() as connection:
            await connection.execute(
                sa.update(SchemaConfig)
                .where(SchemaConfig.key == VECTOR_CONFIG_KEY)
                .values(value={"embed_dim": 1024})
            )


async def test_verify_rejects_missing_row(db_engine: AsyncEngine) -> None:
    async with db_engine.begin() as connection:
        await connection.execute(
            sa.delete(SchemaConfig).where(SchemaConfig.key == VECTOR_CONFIG_KEY)
        )
    try:
        with pytest.raises(ConfigurationError):
            await verify_schema_config(db_engine)
    finally:
        async with db_engine.begin() as connection:
            await connection.execute(
                sa.insert(SchemaConfig).values(key=VECTOR_CONFIG_KEY, value={"embed_dim": 1024})
            )


def test_recorded_dim_parses_payloads() -> None:
    assert _recorded_dim({"embed_dim": 1024}) == 1024
    assert _recorded_dim({"embed_dim": "768"}) == 768
    assert _recorded_dim({}) is None
    assert _recorded_dim({"embed_dim": None}) is None
    assert _recorded_dim({"embed_dim": "bogus"}) is None
