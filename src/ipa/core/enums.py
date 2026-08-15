"""Shared enumerations and the canonical pipeline step order.

Every module that reasons about pipeline progress, document state or field types
imports from here. The values are persisted in PostgreSQL and returned over the
API, so they are part of the public contract: never rename a value.
"""

from __future__ import annotations

from enum import StrEnum


class PipelineStep(StrEnum):
    """A single stage of the document processing pipeline."""

    STORE = "store"
    DECOMPOSE = "decompose"
    OCR = "ocr"
    EXTRACT = "extract"
    EMBED = "embed"
    REVIEW = "review"


class StepStatus(StrEnum):
    """Execution state of one pipeline step for one document."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class DocumentStatus(StrEnum):
    """Coarse, derived state of a document as shown to API clients."""

    RECEIVED = "received"
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    VALIDATED = "validated"
    COMPLETED = "completed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class FieldType(StrEnum):
    """Type of a tag field, used to generate the JSON Schema for extraction."""

    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"
    ARRAY = "array"
    OBJECT = "object"


class ExtractionSource(StrEnum):
    """Origin of an extraction version."""

    MODEL = "model"
    HUMAN = "human"


STEP_ORDER: tuple[PipelineStep, ...] = (
    PipelineStep.STORE,
    PipelineStep.DECOMPOSE,
    PipelineStep.OCR,
    PipelineStep.EXTRACT,
    PipelineStep.EMBED,
    PipelineStep.REVIEW,
)
"""Execution order of the pipeline. Basis for "reprocess from step N onward"."""


def steps_from(step: PipelineStep) -> tuple[PipelineStep, ...]:
    """Return `step` and every step after it, in execution order.

    Args:
        step: The step to start from, inclusive.

    Returns:
        A tuple of steps from `step` to the end of `STEP_ORDER`.

    Raises:
        ValueError: If `step` is not part of `STEP_ORDER`.
    """
    try:
        index = STEP_ORDER.index(step)
    except ValueError as exc:  # pragma: no cover - guards a corrupted enum
        raise ValueError(f"unknown pipeline step: {step}") from exc
    return STEP_ORDER[index:]


def next_step(step: PipelineStep) -> PipelineStep | None:
    """Return the step executed after `step`, or None if it is the last one.

    Args:
        step: The current step.

    Returns:
        The following step, or None when `step` is the final step.

    Raises:
        ValueError: If `step` is not part of `STEP_ORDER`.
    """
    remaining = steps_from(step)[1:]
    return remaining[0] if remaining else None
