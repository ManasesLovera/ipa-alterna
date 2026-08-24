"""Webhook service: SSRF-safe emission, signing and delivery records."""

from __future__ import annotations

import ipaddress
import json
import socket
from datetime import UTC, datetime
from ipaddress import IPv4Address, IPv6Address
from uuid import UUID, uuid4

import httpx
import structlog

from ipa.db.dtos import WebhookDto
from ipa.db.enums import WebhookDeliveryStatus
from ipa.db.repositories.webhook import WebhookRepository
from ipa.domain.users import sign_webhook

logger = structlog.get_logger(__name__)

_MAX_RETRIES = 6
_CONNECT_TIMEOUT = 5.0
_TOTAL_TIMEOUT = 10.0

IpAddress = IPv4Address | IPv6Address

# Blocked address families: private, loopback, link-local, and cloud metadata.
_PRIVATE = ipaddress.ip_network("10.0.0.0/8")
_LOOPBACK = ipaddress.ip_network("127.0.0.0/8")
_LINK_LOCAL = ipaddress.ip_network("169.254.0.0/16")


def _is_private_ip(host: str) -> bool:
    """Report whether a hostname resolves to a private/blocked address.

    Args:
        host: The hostname to resolve.

    Returns:
        True when every resolved address is private or blocked.
    """
    try:
        addresses = [ipaddress.ip_address(item[4][0]) for item in socket.getaddrinfo(host, 80)]
    except OSError:
        return True
    return all(_is_blocked(address) for address in addresses)


def _is_blocked(address: IpAddress) -> bool:
    """Report whether an address is blocked for SSRF protection.

    Args:
        address: An IP address.

    Returns:
        True when the address is private, loopback or link-local.
    """
    return address.is_private or address.is_loopback or address.is_link_local


def validate_webhook_url(url: str, *, is_local: bool) -> bool:
    """Check a webhook URL for SSRF safety.

    Args:
        url: The target URL.
        is_local: Whether the platform runs in a `local` environment.

    Returns:
        True when the URL is safe to register.

    Raises:
        None; the caller surfaces rejection.
    """
    if not is_local and not url.startswith("https://"):
        return False
    if not url.startswith(("http://", "https://")):
        return False
    host = url.split("://")[1].split("/")[0].split(":")[0]
    try:
        ipaddress.ip_address(host)
        return False  # literal IPs are disallowed
    except ValueError:
        pass
    return not _is_private_ip(host)


class WebhookService:
    """Emits webhook deliveries and records their outcomes."""

    def __init__(self, repo: WebhookRepository) -> None:
        """Initialise the service.

        Args:
            repo: The webhook repository.
        """
        self._repo = repo

    async def emit(self, event: str, document_id: UUID, status: str) -> None:
        """Emit an event to every subscribed active webhook.

        Args:
            event: The event name.
            document_id: The document id.
            status: The document status.
        """
        webhooks = await self._repo.list_active()
        payload = {
            "event": event,
            "document_id": str(document_id),
            "status": status,
            "occurred_at": datetime.now(UTC).isoformat(),
            "self": f"/v1/documents/{document_id}",
        }
        for webhook in webhooks:
            if event not in webhook.events:
                continue
            await self._deliver(webhook, event, payload)

    async def _deliver(self, webhook: WebhookDto, event: str, payload: dict) -> None:
        """Deliver one event to one webhook with retries.

        Args:
            webhook: The webhook DTO.
            event: The event name.
            payload: The thin event body.
        """
        body = json.dumps(payload).encode()
        timestamp = str(int(datetime.now(UTC).timestamp()))
        signature = sign_webhook(webhook.secret, timestamp, body)

        async with httpx.AsyncClient(
            timeout=httpx.Timeout(_TOTAL_TIMEOUT, connect=_CONNECT_TIMEOUT),
            follow_redirects=False,
        ) as client:
            for attempt in range(1, _MAX_RETRIES + 1):
                delivery = await self._repo.record_delivery(
                    webhook_id=webhook.id,
                    event_type=event,
                    payload=payload,
                    attempt=attempt,
                )
                try:
                    response = await client.post(
                        webhook.url,
                        content=body,
                        headers={
                            "Content-Type": "application/json",
                            "X-IPA-Event": event,
                            "X-IPA-Delivery": str(uuid4()),
                            "X-IPA-Timestamp": timestamp,
                            "X-IPA-Signature": signature,
                        },
                    )
                    response.raise_for_status()
                    await self._repo.complete_delivery(
                        delivery.id,
                        status=WebhookDeliveryStatus.DELIVERED,
                        response_status=response.status_code,
                    )
                    return
                except Exception as exc:
                    await self._repo.complete_delivery(
                        delivery.id,
                        status=WebhookDeliveryStatus.FAILED,
                        error=str(exc),
                    )
                    logger.warning(
                        "webhook.delivery_failed",
                        webhook_id=webhook.id,
                        attempt=attempt,
                        error=str(exc),
                    )
        logger.error("webhook.delivery_exhausted", webhook_id=webhook.id)
