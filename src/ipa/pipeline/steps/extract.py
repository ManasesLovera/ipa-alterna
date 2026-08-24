"""Extract step: structured extraction + confidence.

Registered as the `extract` step handler. Turns page text into validated JSON
matching the document's tag schema, computing per-field and document confidence.
Extractions are append-only: each run writes a new version to MongoDB and an
`extraction_versions` row, flipping `is_current` in one transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC
from typing import Any
from uuid import UUID

import structlog

from ipa.contracts.models import (
    ExtractedField,
    ExtractionRecord,
    PageText,
    StepContext,
    StepResult,
)
from ipa.core.enums import ExtractionSource, PipelineStep, StepStatus
from ipa.pipeline.registry import register

logger = structlog.get_logger(__name__)


@dataclass
class _TagSpec:
    """The compiled schema and prompt for a document's tag."""

    tag_id: UUID
    json_schema: dict[str, Any]
    prompt: str
    tag_version: int
    required_keys: set[str]
    field_keys: list[str]


@register(PipelineStep.EXTRACT)
async def extract_handler(context: StepContext) -> StepResult:
    """Extract structured data from a document's page text.

    Args:
        context: The step context.

    Returns:
        A step result with extraction metrics, or a skipped status when the
        document has no resolvable tag.
    """
    from ipa.db.repositories.document import DocumentRepository
    from ipa.db.repositories.event import EventRepository
    from ipa.db.repositories.extraction import ExtractionRepository
    from ipa.db.session import session_scope
    from ipa.storage.factory import get_cache_store, get_content_store

    content = get_content_store()
    cache = get_cache_store()

    async with session_scope() as session:
        documents = DocumentRepository(session)
        extractions = ExtractionRepository(session)
        events = EventRepository(session)
        document = await documents.get(context.document_id)
        if document is None:
            return StepResult(status=StepStatus.FAILED, detail="document not found")

        spec = await _resolve_tag(context, document)
        if spec is None:
            from ipa.core.enums import DocumentStatus
            from ipa.db.dtos import DocumentPatch

            await documents.patch(
                context.document_id,
                DocumentPatch(status=DocumentStatus.PENDING_REVIEW, meta={"needs_tag": True}),
            )
            return StepResult(
                status=StepStatus.SKIPPED,
                detail="no tag assigned; parked for review",
                metrics={"needs_tag": 1.0},
            )

        pages = await content.get_pages(context.document_id)
        user_message = _build_user_message(document, pages)

        provider = _get_llm()
        completion = await provider.complete_json(
            system=spec.prompt,
            user=user_message,
            json_schema=spec.json_schema,
        )

        fields = _extract_fields(spec, completion.data)
        document_confidence = score_document_pure(fields, spec.required_keys)

        record = ExtractionRecord(
            document_id=context.document_id,
            version=1,
            tag_id=spec.tag_id,
            tag_version=spec.tag_version,
            source=ExtractionSource.MODEL,
            model=completion.model,
            prompt_hash=_prompt_hash(spec, completion.raw_text),
            fields=fields,
            document_confidence=document_confidence,
            created_at=_now(),
        )
        mongo_id = await content.put_extraction(record)

        version_dto = await extractions.add(
            document_id=context.document_id,
            tag_id=spec.tag_id,
            tag_version=spec.tag_version,
            source=ExtractionSource.MODEL,
            document_confidence=document_confidence,
            mongo_id=mongo_id,
            model=completion.model,
            prompt_hash=record.prompt_hash,
        )
        from ipa.db.dtos import DocumentPatch

        await documents.patch(
            context.document_id,
            DocumentPatch(
                document_confidence=document_confidence,
                tag_id=spec.tag_id,
                tag_version=spec.tag_version,
            ),
        )
        await cache.delete(cache_key_extraction(context.document_id))
        await content.put_raw_response(
            context.document_id, "extract", completion.model, completion.raw_text
        )
        await events.append(
            context.document_id,
            "extracted",
            payload={"version": version_dto.version, "confidence": document_confidence},
        )

        metrics = {
            "fields_total": float(len(spec.field_keys)),
            "fields_populated": float(sum(1 for f in fields if f.value is not None)),
            "fields_invalid": float(sum(1 for f in fields if not f.valid)),
            "document_confidence": float(document_confidence),
            "input_tokens": float(completion.prompt_tokens or 0),
            "output_tokens": float(completion.completion_tokens or 0),
            "repairs": 1.0 if completion.repaired else 0.0,
            "pages_sent": float(len(pages)),
            "images_sent": 0.0,
            "latency_ms": float(completion.latency_ms),
        }
        return StepResult(status=StepStatus.SUCCEEDED, metrics=metrics)


async def _resolve_tag(context: StepContext, document: object) -> _TagSpec | None:
    """Resolve the document's tag and compile its schema.

    Args:
        context: The step context.
        document: The document DTO.

    Returns:
        The compiled tag spec, or None when no tag can be resolved.
    """
    from ipa.db.repositories.tag import TagRepository
    from ipa.db.session import session_scope
    from ipa.domain.tags import TagService
    from ipa.storage.factory import get_cache_store

    tag_id = getattr(document, "tag_id", None)
    if tag_id is None:
        async with session_scope() as session:
            repo = TagRepository(session)
            tags = await repo.list_tags()
            if len(tags) == 1:
                tag_id = tags[0].id
            elif len(tags) == 0:
                return None
            else:
                return None  # classification picks a tag (future)

    async with session_scope() as session:
        service = TagService(TagRepository(session), get_cache_store())
        compiled = await service.compiled_schema(tag_id)

    fields = compiled.json_schema.get("properties", {}).get("fields", {}).get("properties", {})
    field_keys = list(fields.keys())
    required_keys = {
        key
        for key, spec in fields.items()
        if key in compiled.json_schema.get("properties", {}).get("fields", {}).get("required", [])
    }
    return _TagSpec(
        tag_id=tag_id,
        json_schema=compiled.json_schema,
        prompt=compiled.prompt,
        tag_version=compiled.tag_version,
        required_keys=required_keys,
        field_keys=field_keys,
    )


def _build_user_message(document: object, pages: list[PageText]) -> str:
    """Assemble the user message with page markers.

    Args:
        document: The document DTO.
        pages: The document's page texts.

    Returns:
        The user message.
    """
    filename = getattr(document, "original_filename", "document")
    page_count = getattr(document, "page_count", None)
    parts = [f"Document: {filename}", f"Pages: {page_count or len(pages)}", ""]
    for page in pages:
        parts.append(f"<page n=\"{page.page}\">\n{page.text}\n</page>")
    return "\n".join(parts)


def _extract_fields(spec: _TagSpec, data: dict[str, Any]) -> list[ExtractedField]:
    """Coerce, validate and score each field returned by the model.

    Args:
        spec: The tag spec.
        data: The model's parsed JSON object.

    Returns:
        A list of validated extracted fields.
    """
    from ipa.domain.extraction import (
        adjust_confidence,
        coerce_value,
        validate_field,
    )

    raw_fields = (data.get("fields") or {}) if isinstance(data, dict) else {}
    spec_fields = spec.json_schema.get("properties", {}).get("fields", {}).get("properties", {})
    result: list[ExtractedField] = []
    for key in spec.field_keys:
        envelope = raw_fields.get(key, {}) if isinstance(raw_fields, dict) else {}
        if not isinstance(envelope, dict):
            envelope = {}
        value = envelope.get("value")
        confidence = float(envelope.get("confidence", 0.5))
        page = envelope.get("page")
        evidence = envelope.get("evidence")

        field_schema = spec_fields.get(key, {})
        value_schema = field_schema.get("properties", {}).get("value", {})
        field_spec = _to_field_spec(key, value_schema, spec)

        coerced, coercion_errors = coerce_value(field_spec, value)
        validation_errors = coercion_errors + validate_field(
            field_spec, coerced, confidence
        )
        valid = not validation_errors
        confidence = adjust_confidence(field_spec, coerced, confidence, valid)

        result.append(
            ExtractedField(
                key=key,
                value=coerced,
                confidence=confidence,
                page=page,
                evidence=evidence,
                valid=valid,
                validation_errors=validation_errors,
            )
        )
    return result


def _to_field_spec(key: str, value_schema: dict[str, Any], spec: _TagSpec) -> Any:
    """Build a FieldSpec from a JSON Schema value definition.

    Args:
        key: The field key.
        value_schema: The field's value schema.
        spec: The tag spec (for required set).

    Returns:
        A `FieldSpec`.
    """
    from ipa.core.enums import FieldType
    from ipa.domain.extraction import FieldSpec

    type_list = value_schema.get("type")
    if isinstance(type_list, list):
        type_list = [t for t in type_list if t != "null"]
    type_name = type_list[0] if isinstance(type_list, list) else type_list
    try:
        field_type = FieldType(str(type_name))
    except (ValueError, TypeError):
        field_type = FieldType.STRING
    enum_values = value_schema.get("enum")
    return FieldSpec(
        key=key,
        field_type=field_type,
        required=key in spec.required_keys,
        enum_values=list(enum_values) if enum_values else [],
    )


def score_document_pure(fields: list[ExtractedField], required_keys: set[str]) -> float:
    """Compute the document confidence from validated fields.

    Args:
        fields: The validated fields.
        required_keys: The required field keys.

    Returns:
        The document confidence in [0, 1].
    """
    from ipa.domain.extraction import score_document

    return score_document(fields, required_keys)


def _prompt_hash(spec: _TagSpec, raw_text: str) -> str:
    """Return a prompt hash from the schema and raw output.

    Args:
        spec: The tag spec.
        raw_text: The model's raw output.

    Returns:
        A hex digest.
    """
    import hashlib

    return hashlib.sha256(spec.json_schema.__repr__().encode()).hexdigest()[:16]


def _now() -> Any:
    """Return the current UTC datetime.

    Returns:
        The current UTC datetime.
    """
    from datetime import datetime

    return datetime.now(UTC)


def _get_llm() -> Any:
    """Return the configured LLM provider.

    Returns:
        An `LlmProvider`.
    """
    from ipa.providers.factory import get_llm_provider

    return get_llm_provider()


def cache_key_extraction(document_id: Any) -> str:
    """Build the extraction cache key for a document.

    Args:
        document_id: The document id.

    Returns:
        The cache key prefix.
    """
    from ipa.storage import cache_keys

    return cache_keys.cache_key("extraction", str(document_id))

