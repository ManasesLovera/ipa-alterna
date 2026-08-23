"""Validation decision service (T13 owns the full implementation).

T08's review step delegates here. Until T13 lands the decision defaults to
"needs review" for every document, which is the project's default posture anyway
(no tag auto-approves by default).
"""

from __future__ import annotations

from uuid import UUID

from ipa.contracts.models import StepResult
from ipa.core.enums import StepStatus


async def review_decision(document_id: UUID) -> StepResult:
    """Decide whether a document auto-approves or needs human review.

    This is the T13-owned decision point; the stub returns "needs review" so the
    pipeline reaches a terminal state before T13 replaces it.

    Args:
        document_id: Identifier of the document.

    Returns:
        A `StepResult` marking the review step succeeded with a needs-review
        outcome.
    """
    return StepResult(status=StepStatus.SUCCEEDED, detail="needs review")
