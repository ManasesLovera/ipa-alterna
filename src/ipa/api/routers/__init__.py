"""Router registry.

`create_app()` mounts every router in `ROUTERS` under `/v1`. Later tasks own one
router module each and **append** to this list — never reorder or edit another
task's entry, so parallel branches merge cleanly.

Example:
    from ipa.api.routers.documents import router as documents_router

    ROUTERS.append(documents_router)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from fastapi import APIRouter

from ipa.api.routers.documents import router as documents_router
from ipa.api.routers.tags import router as tags_router

ROUTERS: list[APIRouter] = [tags_router, documents_router]
