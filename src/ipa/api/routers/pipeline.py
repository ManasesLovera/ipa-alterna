"""Pipeline operations REST router: reprocess, cancel, retry, status."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.api.auth import require_auth
from ipa.core.enums import PipelineStep
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.step import StepRepository
from ipa.db.session import get_session
from ipa.pipeline.orchestrator import Orchestrator
from ipa.pipeline.runner import _enqueue

router = APIRouter(tags=["pipeline"], prefix="/pipeline")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[object, Depends(require_auth)]


def _orchestrator(session: AsyncSession) -> Orchestrator:
    """Assemble an Orchestrator bound to a session.

    Args:
        session: The request session.

    Returns:
        An `Orchestrator` instance.
    """
    return Orchestrator(
        DocumentRepository(session), StepRepository(session), _enqueue
    )


@router.post("/documents/{document_id}/reprocess")
async def reprocess(
    document_id: UUID,
    session: SessionDep,
    _auth: AuthDep,
    from_step: PipelineStep = PipelineStep.DECOMPOSE,
) -> dict[str, str]:
    """Reset a step and everything after it, then re-enqueue.

    Args:
        document_id: Identifier of the document.
        session: The request session.
        _auth: The authenticated principal.
        from_step: The step to reprocess from.

    Returns:
        A status message.
    """
    await _orchestrator(session).reprocess(document_id, from_step)
    return {"status": "reprocessing", "from_step": from_step.value}
