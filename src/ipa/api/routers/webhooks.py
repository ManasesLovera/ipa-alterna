"""Webhook REST router: subscriptions and delivery history."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from ipa.api.auth import require_auth
from ipa.core.config import get_settings
from ipa.db.repositories.webhook import WebhookRepository
from ipa.db.session import get_session
from ipa.domain.webhooks import validate_webhook_url

router = APIRouter(tags=["webhooks"], prefix="/webhooks")

SessionDep = Annotated[AsyncSession, Depends(get_session)]
AuthDep = Annotated[object, Depends(require_auth)]


class WebhookCreate(BaseModel):
    """Body for creating a webhook."""

    model_config = ConfigDict(extra="forbid")

    url: str
    events: list[str]


class WebhookRead(BaseModel):
    """A webhook as returned by the API."""

    model_config = ConfigDict(extra="forbid")

    id: str
    url: str
    events: list[str]
    is_active: bool


@router.post("", status_code=201)
async def create_webhook(
    body: WebhookCreate,
    session: SessionDep,
    _auth: AuthDep,
) -> WebhookRead:
    """Register a webhook subscription.

    Args:
        body: The webhook URL and events.
        session: The request session.
        _auth: The authenticated principal.

    Returns:
        The created webhook.
    """
    import secrets

    settings = get_settings()
    if not validate_webhook_url(body.url, is_local=settings.env == "local"):
        raise HTTPException(status_code=422, detail="unsafe webhook URL")
    webhook = await WebhookRepository(session).create(
        url=body.url, secret=secrets.token_hex(16), events=body.events
    )
    return WebhookRead(
        id=str(webhook.id), url=webhook.url, events=webhook.events, is_active=webhook.is_active
    )


@router.get("")
async def list_webhooks(
    session: SessionDep,
    _auth: AuthDep,
) -> list[WebhookRead]:
    """List webhooks.

    Args:
        session: The request session.
        _auth: The authenticated principal.

    Returns:
        The webhooks.
    """
    webhooks = await WebhookRepository(session).list_active()
    return [
        WebhookRead(id=str(w.id), url=w.url, events=w.events, is_active=w.is_active)
        for w in webhooks
    ]
