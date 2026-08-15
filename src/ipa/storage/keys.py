"""Blob key helpers for the storage layer.

The key *scheme* is a shared contract owned by `ipa.core.ids` (T01); these
aliases expose it under the names used in the T03 spec and the storage adapters
without duplicating the format strings. Always build keys through helpers,
never by formatting strings at call sites.
"""

from __future__ import annotations

from ipa.core.ids import original_key, page_image_key, thumbnail_key

page_key = page_image_key
"""Alias of :func:`ipa.core.ids.page_image_key`."""

thumb_key = thumbnail_key
"""Alias of :func:`ipa.core.ids.thumbnail_key`."""

__all__ = ["original_key", "page_key", "thumb_key"]
