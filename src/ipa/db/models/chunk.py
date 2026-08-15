"""pgvector chunk table.

Every chunk row records the embedding model and dimension that produced it so
models can be migrated side by side. The vector column dimension comes from
`NVIDIA_EMBED_DIM` at migration time and is recorded in `schema_config`;
startup refuses to run when the configured dimension no longer matches.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector
from sqlalchemy import Computed
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from ipa.core.config import get_settings
from ipa.db.base import Base, CreatedOnlyMixin, UuidPkMixin

EMBED_DIM: int = get_settings().nvidia.embed_dim
"""Vector dimensionality of the `embedding` column; must match the database."""


class Chunk(UuidPkMixin, CreatedOnlyMixin, Base):
    """One embedded text chunk of a document, indexed for vector and lexical search."""

    __tablename__ = "chunks"
    __table_args__ = (
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            "embed_model",
            name="uq_chunks_document_id_chunk_index_embed_model",
        ),
        sa.Index("ix_chunks_document_id", "document_id"),
        sa.Index("ix_chunks_tsv", "tsv", postgresql_using="gin"),
        sa.Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_with={"m": "16", "ef_construction": "64"},
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    document_id: Mapped[UUID] = mapped_column(
        sa.Uuid, sa.ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    text: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embedding: Mapped[Any] = mapped_column(Vector(EMBED_DIM), nullable=False)
    embed_model: Mapped[str] = mapped_column(sa.Text, nullable=False)
    embed_dim: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    page_from: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    page_to: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    token_count: Mapped[int | None] = mapped_column(sa.Integer, nullable=True)
    tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        Computed("to_tsvector('simple'::regconfig, text)", persisted=True),
        nullable=True,
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
    )
