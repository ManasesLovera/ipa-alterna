"""Tags and schema-registry REST router."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.api.auth import require_auth
from ipa.api.schemas.tags import (
    CompiledSchema,
    ReorderFields,
    TagCreate,
    TagFieldCreate,
    TagFieldRead,
    TagFieldUpdate,
    TagRead,
    TagUpdate,
    TagVersionRead,
)
from ipa.contracts.protocols import CacheStore
from ipa.db.repositories.document import DocumentRepository
from ipa.db.repositories.tag import TagRepository
from ipa.db.session import get_session
from ipa.domain.tags import TagService
from ipa.storage.factory import cache_store_dep

router = APIRouter(tags=["tags"], prefix="/tags")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[object, Depends(require_auth)]


def _service(session: AsyncSession, cache: CacheStore) -> TagService:
    """Assemble a TagService bound to a session and cache.

    Args:
        session: The request session.
        cache: The cache store.

    Returns:
        A `TagService` instance.
    """
    return TagService(TagRepository(session), cache)


@router.post("", status_code=status.HTTP_201_CREATED, response_model=TagRead)
async def create_tag(
    spec: TagCreate,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> TagRead:
    """Create a tag, optionally with its initial fields.

    Args:
        spec: The tag specification.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The created tag.
    """
    return await _service(session, cache).create_tag(spec)


@router.get("", response_model=list[TagRead])
async def list_tags(
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
    include_inactive: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[TagRead]:
    """List tags.

    Args:
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.
        include_inactive: Include deactivated tags.
        limit: Maximum results.

    Returns:
        The tags.
    """
    return await _service(session, cache).list_tags(
        include_inactive=include_inactive, limit=limit
    )


@router.get("/{tag_id}", response_model=TagRead)
async def get_tag(
    tag_id: UUID,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
    version: int | None = Query(default=None),
) -> TagRead:
    """Fetch a tag, or a historical snapshot by version.

    Args:
        tag_id: Identifier of the tag.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.
        version: Snapshot version to return.

    Returns:
        The tag.
    """
    return await _service(session, cache).get_tag(tag_id, version=version)


@router.patch("/{tag_id}", response_model=TagRead)
async def update_tag(
    tag_id: UUID,
    spec: TagUpdate,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> TagRead:
    """Patch a tag's non-schema attributes.

    Args:
        tag_id: Identifier of the tag.
        spec: The attributes to update.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The updated tag.
    """
    return await _service(session, cache).update_tag(tag_id, spec)


@router.delete("/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag(
    tag_id: UUID,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> None:
    """Delete a tag, refusing when documents reference it.

    Args:
        tag_id: Identifier of the tag.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        None.
    """
    has_documents = await DocumentRepository(session).count_by_tag(tag_id) > 0
    await _service(session, cache).delete_tag(tag_id, has_documents=has_documents)


@router.post("/{tag_id}/deactivate", response_model=TagRead)
async def deactivate_tag(
    tag_id: UUID,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> TagRead:
    """Deactivate a tag, hiding it from pickers.

    Args:
        tag_id: Identifier of the tag.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The deactivated tag.
    """
    return await _service(session, cache).deactivate_tag(tag_id)


@router.get("/{tag_id}/versions", response_model=list[TagVersionRead])
async def list_versions(
    tag_id: UUID,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> list[TagVersionRead]:
    """List a tag's immutable snapshots.

    Args:
        tag_id: Identifier of the tag.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The snapshots, newest first.
    """
    return await _service(session, cache).list_versions(tag_id)


@router.get("/{tag_id}/schema", response_model=CompiledSchema)
async def get_schema(
    tag_id: UUID,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
    version: int | None = Query(default=None),
) -> CompiledSchema:
    """Return the compiled JSON Schema and prompt for a tag.

    Args:
        tag_id: Identifier of the tag.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.
        version: Version to compile; current when None.

    Returns:
        The compiled schema.
    """
    return await _service(session, cache).compiled_schema(tag_id, version=version)


@router.post("/{tag_id}/fields", response_model=TagRead)
async def add_field(
    tag_id: UUID,
    spec: TagFieldCreate,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> TagRead:
    """Add a field, bumping the tag version.

    Args:
        tag_id: Identifier of the tag.
        spec: The field to add.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The updated tag.
    """
    return await _service(session, cache).add_field(tag_id, spec)


@router.put("/{tag_id}/fields", response_model=TagRead)
async def replace_fields(
    tag_id: UUID,
    specs: list[TagFieldCreate],
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> TagRead:
    """Replace all fields in one version bump.

    Args:
        tag_id: Identifier of the tag.
        specs: The new ordered fields.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The updated tag.
    """
    return await _service(session, cache).replace_fields(tag_id, specs)


@router.patch("/{tag_id}/fields/{field_id}", response_model=TagRead)
async def update_field(
    tag_id: UUID,
    field_id: UUID,
    spec: TagFieldUpdate,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> TagRead:
    """Patch a field, bumping the tag version.

    Args:
        tag_id: Identifier of the tag.
        field_id: Identifier of the field.
        spec: The attributes to change.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The updated tag.
    """
    return await _service(session, cache).update_field(tag_id, field_id, spec)


@router.delete("/{tag_id}/fields/{field_id}", response_model=TagRead)
async def delete_field(
    tag_id: UUID,
    field_id: UUID,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> TagRead:
    """Delete a field, bumping the tag version.

    Args:
        tag_id: Identifier of the tag.
        field_id: Identifier of the field.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The updated tag.
    """
    return await _service(session, cache).delete_field(tag_id, field_id)


@router.post("/{tag_id}/fields/reorder", response_model=list[TagFieldRead])
async def reorder_fields(
    tag_id: UUID,
    body: ReorderFields,
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> list[TagFieldRead]:
    """Reorder a tag's fields without a version bump.

    Args:
        tag_id: Identifier of the tag.
        body: The ordered field ids.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The reordered fields.
    """
    ordered = [UUID(value) for value in body.field_ids]
    return await _service(session, cache).reorder_fields(tag_id, ordered)


@router.post("/schema/preview", response_model=dict)
async def preview_schema(
    specs: list[TagFieldCreate],
    session: SessionDep,
    cache: Annotated[CacheStore, Depends(cache_store_dep)],
    _auth: AuthDep,
) -> dict:
    """Compile a candidate field list without saving anything.

    Args:
        specs: The candidate fields.
        session: The request session.
        cache: The cache store.
        _auth: The authenticated principal.

    Returns:
        The compiled JSON Schema.
    """
    return await _service(session, cache).preview_schema(specs)
