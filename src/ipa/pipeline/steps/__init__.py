"""Pipeline step handlers.

T09-T12 register concrete handlers here with `@register(...)`. The module is
imported at worker boot so all handlers are registered before any task runs;
step modules that have not landed yet are tolerated rather than failing the
worker.
"""

from __future__ import annotations

import contextlib
import importlib

from ipa.contracts.models import StepContext, StepResult
from ipa.core.enums import PipelineStep, StepStatus
from ipa.pipeline.registry import register

with contextlib.suppress(ImportError, ModuleNotFoundError):  # pragma: no cover - wave 3
    importlib.import_module("ipa.pipeline.steps.decompose")
    importlib.import_module("ipa.pipeline.steps.ocr")
    importlib.import_module("ipa.pipeline.steps.extract")
    importlib.import_module("ipa.pipeline.steps.embed")


@register(PipelineStep.STORE)
async def _store_handler(context: StepContext) -> StepResult:
    """Handle the `store` step: already performed at ingest time.

    Args:
        context: The step context.

    Returns:
        A succeeded result.
    """
    return StepResult(status=StepStatus.SUCCEEDED, detail="stored at ingest")


@register(PipelineStep.REVIEW)
async def _review_handler(context: StepContext) -> StepResult:
    """Handle the `review` step: delegate to the review decision.

    Defaults to "needs review" when the validation service (T13) has not landed.

    Args:
        context: The step context.

    Returns:
        The review decision result.
    """
    with contextlib.suppress(ImportError):
        from ipa.domain.validation import review_decision

        return await review_decision(context.document_id)
    return StepResult(status=StepStatus.SUCCEEDED, detail="needs review")
