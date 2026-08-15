"""Pure metadata tests: run everywhere, no database required."""

from __future__ import annotations

import sqlalchemy as sa

import ipa.db.models  # noqa: F401
from ipa.core.enums import DocumentStatus, FieldType, PipelineStep, StepStatus
from ipa.db.base import Base


def _column(table_name: str, column_name: str) -> sa.Column:
    return Base.metadata.tables[table_name].columns[column_name]


def test_every_documented_table_exists() -> None:
    assert set(Base.metadata.tables) == {
        "users",
        "api_keys",
        "tags",
        "tag_fields",
        "tag_versions",
        "documents",
        "document_steps",
        "document_events",
        "document_pages",
        "extraction_versions",
        "validations",
        "chunks",
        "webhooks",
        "webhook_deliveries",
        "idempotency_keys",
        "schema_config",
    }


def test_enum_columns_persist_member_values() -> None:
    cases: list[tuple[str, str, set[object]]] = [
        ("users", "role", {"admin", "reviewer", "viewer"}),
        ("documents", "status", set(DocumentStatus)),
        ("documents", "current_step", set(PipelineStep)),
        ("documents", "source", {"ui", "api"}),
        ("document_steps", "step", set(PipelineStep)),
        ("document_steps", "status", set(StepStatus)),
        ("tag_fields", "field_type", set(FieldType)),
    ]
    for table_name, column_name, expected in cases:
        enum_type = _column(table_name, column_name).type
        assert isinstance(enum_type, sa.Enum)
        expected_values = {value.value if hasattr(value, "value") else value for value in expected}
        assert set(enum_type.enums) == expected_values, (
            f"{table_name}.{column_name} must persist enum values"
        )


def test_naming_convention_pins_constraint_names() -> None:
    tags = Base.metadata.tables["tags"]
    check_names = {
        constraint.name
        for constraint in tags.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }
    assert "ck_tags_auto_approve_threshold_range" in check_names

    tag_fields = Base.metadata.tables["tag_fields"]
    unique = {
        constraint.name: constraint
        for constraint in tag_fields.constraints
        if isinstance(constraint, sa.UniqueConstraint)
    }
    assert unique["uq_tag_fields_tag_id_position"].deferrable is True


def test_documents_indexes_match_spec() -> None:
    documents = Base.metadata.tables["documents"]
    indexes = {index.name: index for index in documents.indexes}
    assert set(indexes) == {
        "ix_documents_status",
        "ix_documents_tag_id",
        "ix_documents_created_at",
        "ix_documents_needs_review",
        "ix_documents_metadata",
    }
    assert indexes["ix_documents_metadata"].dialect_options["postgresql"]["using"] == "gin"
    assert indexes["ix_documents_needs_review"].dialect_options["postgresql"]["where"] is not None


def test_chunks_partial_unique_and_vector_index() -> None:
    chunks = Base.metadata.tables["chunks"]
    indexes = {index.name: index for index in chunks.indexes}
    assert indexes["ix_chunks_tsv"].dialect_options["postgresql"]["using"] == "gin"

    hnsw = indexes["ix_chunks_embedding_hnsw"]
    assert hnsw.dialect_options["postgresql"]["using"] == "hnsw"
    assert hnsw.dialect_options["postgresql"]["ops"] == {"embedding": "vector_cosine_ops"}

    extraction_versions = Base.metadata.tables["extraction_versions"]
    current = {
        index.name: index
        for index in extraction_versions.indexes
        if index.name == "uq_extraction_versions_document_id_current"
    }
    assert current["uq_extraction_versions_document_id_current"].unique is True


def test_chunks_tsv_is_generated_column() -> None:
    tsv = _column("chunks", "tsv")
    assert tsv.computed is not None
    assert tsv.computed.sqltext.text == "to_tsvector('simple'::regconfig, text)"
    assert tsv.computed.persisted is True
