"""T16 auth and webhook primitives."""

from __future__ import annotations

import hashlib
import hmac

from ipa.domain.users import (
    generate_api_key,
    hash_password,
    sign_webhook,
    verify_password,
)
from ipa.domain.webhooks import validate_webhook_url


def test_password_hash_uses_argon2id() -> None:
    hashed = hash_password("hunter2")

    assert hashed.startswith("$argon2id")
    assert verify_password("hunter2", hashed) is True
    assert verify_password("wrong", hashed) is False


def test_generate_api_key_returns_once_and_digest() -> None:
    raw, digest = generate_api_key(env="test")

    assert raw.startswith("ipa_test_")
    assert len(raw) > 20
    assert digest == hashlib.sha256(raw.encode()).hexdigest()
    assert raw not in digest


def test_sign_webhook_matches_hand_computed_hmac() -> None:
    secret = "sekret"
    timestamp = "1700000000"
    body = b'{"event":"ping"}'

    signature = sign_webhook(secret, timestamp, body)

    expected = "sha256=" + hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    assert signature == expected


def test_validate_webhook_url_rejects_metadata_address() -> None:
    assert validate_webhook_url("http://169.254.169.254/latest", is_local=False) is False
    assert validate_webhook_url("http://169.254.169.254/latest", is_local=True) is False


def test_validate_webhook_url_requires_https_outside_local() -> None:
    assert validate_webhook_url("http://example.com/hook", is_local=False) is False
    assert validate_webhook_url("https://example.com/hook", is_local=False) is True


def test_validate_webhook_url_rejects_literal_private_ip() -> None:
    assert validate_webhook_url("https://10.0.0.5/hook", is_local=False) is False
