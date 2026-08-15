"""Initial schema: extensions, every table, and their indexes.

The pgvector embedding column dimension is generated from `NVIDIA_EMBED_DIM`
at migration time and recorded in `schema_config` so startup can reject a
configuration that no longer matches the deployed schema.

Revision ID: 0001
Revises:
Create Date: 2026-08-15
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from ipa.core.config import get_settings

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None

EMBED_DIM = get_settings().nvidia.embed_dim

def _ts(name: str) -> sa.Column[sa.DateTime]:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    )

_ENUMS: dict[str, list[str]] = {
    "document_source": ["ui", "api"],
    "document_status": [
        "received",
        "processing",
        "pending_review",
        "validated",
        "completed",
        "failed",
        "quarantined",
    ],
    "extraction_source": ["model", "human"],
    "field_type": [
        "string",
        "number",
        "integer",
        "boolean",
        "date",
        "datetime",
        "enum",
        "array",
        "object",
    ],
    "pipeline_step": ["store", "decompose", "ocr", "extract", "embed", "review"],
    "step_status": ["pending", "running", "succeeded", "failed", "skipped"],
    "user_role": ["admin", "reviewer", "viewer"],
    "validation_decision": ["approved", "rejected", "corrected"],
    "webhook_delivery_status": ["pending", "delivered", "failed"],
}


def _enum(name: str) -> sa.Enum:
    return sa.Enum(*_ENUMS[name], name=name)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS citext")

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("email", postgresql.CITEXT(), nullable=False),
        sa.Column("full_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("role", _enum("user_role"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("prefix", sa.String(length=8), nullable=False),
        sa.Column(
            "scopes",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
        sa.UniqueConstraint("key_hash", name=op.f("uq_api_keys_key_hash")),
    )
    op.create_table(
        "tags",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", postgresql.CITEXT(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("auto_approve_threshold", sa.Numeric(4, 3), nullable=True),
        sa.Column("prompt_template", sa.Text(), nullable=True),
        sa.Column("llm_model", sa.Text(), nullable=True),
        sa.Column("classification_hints", sa.Text(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("slug", name=op.f("uq_tags_slug")),
        sa.CheckConstraint(
            "auto_approve_threshold IS NULL OR "
            "(auto_approve_threshold >= 0 AND auto_approve_threshold <= 1)",
            name=op.f("ck_tags_auto_approve_threshold_range"),
        ),
    )
    op.create_table(
        "tag_fields",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "tag_id",
            sa.Uuid(),
            sa.ForeignKey("tags.id", ondelete="CASCADE", name=op.f("fk_tag_fields_tag_id_tags")),
            nullable=False,
        ),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("field_type", _enum("field_type"), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enum_values", postgresql.ARRAY(sa.Text()), nullable=True),
        sa.Column("regex", sa.Text(), nullable=True),
        sa.Column("min_value", sa.Numeric(), nullable=True),
        sa.Column("max_value", sa.Numeric(), nullable=True),
        sa.Column("min_length", sa.Integer(), nullable=True),
        sa.Column("max_length", sa.Integer(), nullable=True),
        sa.Column("item_type", sa.Text(), nullable=True),
        sa.Column("object_schema", postgresql.JSONB(), nullable=True),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tag_fields")),
        sa.UniqueConstraint("tag_id", "key", name=op.f("uq_tag_fields_tag_id_key")),
        sa.UniqueConstraint(
            "tag_id", "position", name=op.f("uq_tag_fields_tag_id_position"), deferrable=True
        ),
    )
    op.create_table(
        "tag_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "tag_id",
            sa.Uuid(),
            sa.ForeignKey("tags.id", name=op.f("fk_tag_versions_tag_id_tags")),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tag_versions")),
        sa.UniqueConstraint("tag_id", "version", name=op.f("uq_tag_versions_tag_id_version")),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("original_filename", sa.Text(), nullable=False),
        sa.Column("mime_type", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("blob_key", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column(
            "tag_id",
            sa.Uuid(),
            sa.ForeignKey("tags.id", name=op.f("fk_documents_tag_id_tags")),
            nullable=True,
        ),
        sa.Column("tag_version", sa.Integer(), nullable=True),
        sa.Column(
            "status",
            _enum("document_status"),
            server_default=sa.text("'received'"),
            nullable=False,
        ),
        sa.Column("current_step", _enum("pipeline_step"), nullable=True),
        sa.Column("document_confidence", sa.Numeric(4, 3), nullable=True),
        sa.Column("needs_review", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("source", _enum("document_source"), nullable=False),
        sa.Column("uploaded_by", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _ts("created_at"),
        _ts("updated_at"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("sha256", name=op.f("uq_documents_sha256")),
    )
    op.create_index("ix_documents_status", "documents", ["status"], unique=False)
    op.create_index("ix_documents_tag_id", "documents", ["tag_id"], unique=False)
    op.create_index(
        "ix_documents_created_at", "documents", [sa.text("created_at DESC")], unique=False
    )
    op.create_index(
        "ix_documents_needs_review",
        "documents",
        ["needs_review"],
        unique=False,
        postgresql_where=sa.text("needs_review"),
    )
    op.create_index(
        "ix_documents_metadata", "documents", ["metadata"], unique=False, postgresql_using="gin"
    )
    op.create_table(
        "document_steps",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id", ondelete="CASCADE", name=op.f("fk_document_steps_document_id_documents")
            ),
            nullable=False,
        ),
        sa.Column("step", _enum("pipeline_step"), nullable=False),
        sa.Column(
            "status",
            _enum("step_status"),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("3"), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("output_ref", sa.Text(), nullable=True),
        sa.Column(
            "metrics",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_steps")),
        sa.UniqueConstraint("document_id", "step", name=op.f("uq_document_steps_document_id_step")),
    )
    op.create_index(
        "ix_document_steps_status_step", "document_steps", ["status", "step"], unique=False
    )
    op.create_table(
        "document_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id", name=op.f("fk_document_events_document_id_documents")
            ),
            nullable=False,
        ),
        sa.Column("step", _enum("pipeline_step"), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column("actor", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_events")),
    )
    op.create_index(
        "ix_document_events_document_id_created_at",
        "document_events",
        ["document_id", "created_at"],
        unique=False,
    )
    op.create_table(
        "document_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id", ondelete="CASCADE", name=op.f("fk_document_pages_document_id_documents")
            ),
            nullable=False,
        ),
        sa.Column("page", sa.Integer(), nullable=False),
        sa.Column("blob_key", sa.Text(), nullable=False),
        sa.Column("thumb_key", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("text_source", sa.Text(), nullable=True),
        sa.Column("char_count", sa.Integer(), nullable=True),
        sa.Column("ocr_confidence", sa.Numeric(4, 3), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_pages")),
        sa.UniqueConstraint("document_id", "page", name=op.f("uq_document_pages_document_id_page")),
    )
    op.create_table(
        "extraction_versions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id", ondelete="CASCADE", name=op.f("fk_extraction_versions_document_id_documents")
            ),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "tag_id",
            sa.Uuid(),
            sa.ForeignKey("tags.id", name=op.f("fk_extraction_versions_tag_id_tags")),
            nullable=False,
        ),
        sa.Column("tag_version", sa.Integer(), nullable=False),
        sa.Column("source", _enum("extraction_source"), nullable=False),
        sa.Column("model", sa.Text(), nullable=True),
        sa.Column("prompt_hash", sa.Text(), nullable=True),
        sa.Column("document_confidence", sa.Numeric(4, 3), nullable=False),
        sa.Column("mongo_id", sa.Text(), nullable=False),
        sa.Column("is_current", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_by", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_extraction_versions")),
        sa.UniqueConstraint(
            "document_id", "version", name=op.f("uq_extraction_versions_document_id_version")
        ),
    )
    op.create_index(
        "uq_extraction_versions_document_id_current",
        "extraction_versions",
        ["document_id"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )
    op.create_table(
        "validations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey("documents.id", name=op.f("fk_validations_document_id_documents")),
            nullable=False,
        ),
        sa.Column(
            "extraction_version_id",
            sa.Uuid(),
            sa.ForeignKey(
                "extraction_versions.id",
                name=op.f("fk_validations_extraction_version_id_extraction_versions"),
            ),
            nullable=False,
        ),
        sa.Column("decision", _enum("validation_decision"), nullable=False),
        sa.Column(
            "reviewer_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", name=op.f("fk_validations_reviewer_id_users")),
            nullable=True,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("corrected_fields", postgresql.JSONB(), nullable=True),
        sa.Column("auto", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_validations")),
    )
    op.create_table(
        "chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "document_id",
            sa.Uuid(),
            sa.ForeignKey(
                "documents.id", ondelete="CASCADE", name=op.f("fk_chunks_document_id_documents")
            ),
            nullable=False,
        ),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(EMBED_DIM), nullable=False),
        sa.Column("embed_model", sa.Text(), nullable=False),
        sa.Column("embed_dim", sa.Integer(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=True),
        sa.Column("page_to", sa.Integer(), nullable=True),
        sa.Column("token_count", sa.Integer(), nullable=True),
        sa.Column(
            "tsv",
            postgresql.TSVECTOR(),
            sa.Computed("to_tsvector('simple'::regconfig, text)", persisted=True),
            nullable=True,
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        _ts("created_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chunks")),
        sa.UniqueConstraint(
            "document_id",
            "chunk_index",
            "embed_model",
            name=op.f("uq_chunks_document_id_chunk_index_embed_model"),
        ),
    )
    op.create_index("ix_chunks_document_id", "chunks", ["document_id"], unique=False)
    op.create_index(
        "ix_chunks_tsv", "chunks", ["tsv"], unique=False, postgresql_using="gin"
    )
    op.create_index(
        "ix_chunks_embedding_hnsw",
        "chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_with={"m": "16", "ef_construction": "64"},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_table(
        "webhooks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column(
            "events",
            postgresql.ARRAY(sa.Text()),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhooks")),
    )
    op.create_table(
        "webhook_deliveries",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column(
            "webhook_id",
            sa.Uuid(),
            sa.ForeignKey("webhooks.id", name=op.f("fk_webhook_deliveries_webhook_id_webhooks")),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=True),
        sa.Column(
            "status",
            _enum("webhook_delivery_status"),
            server_default=sa.text("'pending'"),
            nullable=False,
        ),
        sa.Column("attempt", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        _ts("created_at"),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
    )
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("endpoint", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(), nullable=True),
        _ts("created_at"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
        sa.UniqueConstraint("key", name=op.f("uq_idempotency_keys_key")),
    )
    op.create_table(
        "schema_config",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("value", postgresql.JSONB(), nullable=False),
        _ts("created_at"),
        _ts("updated_at"),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_schema_config")),
    )

    op.get_bind().execute(
        sa.text(
            "INSERT INTO schema_config (key, value) "
            "VALUES ('vector', CAST(:value AS jsonb))"
        ),
        {"value": json.dumps({"embed_dim": EMBED_DIM})},
    )


def downgrade() -> None:
    op.drop_table("schema_config")
    op.drop_table("idempotency_keys")
    op.drop_table("webhook_deliveries")
    op.drop_table("webhooks")
    op.drop_index("ix_chunks_embedding_hnsw", table_name="chunks")
    op.drop_index("ix_chunks_tsv", table_name="chunks")
    op.drop_index("ix_chunks_document_id", table_name="chunks")
    op.drop_table("chunks")
    op.drop_table("validations")
    op.drop_index(
        "uq_extraction_versions_document_id_current", table_name="extraction_versions"
    )
    op.drop_table("extraction_versions")
    op.drop_table("document_pages")
    op.drop_index(
        "ix_document_events_document_id_created_at", table_name="document_events"
    )
    op.drop_table("document_events")
    op.drop_index("ix_document_steps_status_step", table_name="document_steps")
    op.drop_table("document_steps")
    op.drop_index("ix_documents_metadata", table_name="documents")
    op.drop_index("ix_documents_needs_review", table_name="documents")
    op.drop_index("ix_documents_created_at", table_name="documents")
    op.drop_index("ix_documents_tag_id", table_name="documents")
    op.drop_index("ix_documents_status", table_name="documents")
    op.drop_table("documents")
    op.drop_table("tag_versions")
    op.drop_table("tag_fields")
    op.drop_table("tags")
    op.drop_table("api_keys")
    op.drop_table("users")
    for type_name in _ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {type_name}")
    op.execute("DROP EXTENSION IF EXISTS citext")
    op.execute("DROP EXTENSION IF EXISTS vector")
