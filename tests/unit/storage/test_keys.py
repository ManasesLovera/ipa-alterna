"""Blob and cache key helper contracts."""

from __future__ import annotations

from uuid import UUID

from ipa.storage.cache_keys import (
    build_key,
    document_status,
    extraction,
    search,
    tag_schema,
)
from ipa.storage.keys import original_key, page_key, thumb_key

DOCUMENT_ID = UUID("11111111-1111-1111-1111-111111111111")
TAG_ID = UUID("22222222-2222-2222-2222-222222222222")


def test_blob_keys_follow_the_documented_scheme() -> None:
    digest = "ab" * 32

    assert original_key(digest) == f"originals/ab/{digest}"
    assert page_key(DOCUMENT_ID, 7) == f"pages/{DOCUMENT_ID}/00007.png"
    assert thumb_key(DOCUMENT_ID, 12) == f"thumbs/{DOCUMENT_ID}/00012.jpg"


def test_build_key_is_pure() -> None:
    assert build_key("test", "kind", "id-1") == "ipa:test:kind:id-1"
    assert build_key("local", "a", "b:c") == "ipa:local:a:b:c"


def test_cache_keys_are_namespaced_per_environment() -> None:
    # The conftest clears IPA_* variables, so the default env is "local".
    assert extraction(DOCUMENT_ID, 3) == f"ipa:local:extraction:{DOCUMENT_ID}:v3"
    assert tag_schema(TAG_ID, 5) == f"ipa:local:tag_schema:{TAG_ID}:v5"
    assert search("deadbeef") == "ipa:local:search:deadbeef"
    assert document_status(DOCUMENT_ID) == f"ipa:local:document_status:{DOCUMENT_ID}"
