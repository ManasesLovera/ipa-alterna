"""NVIDIA NIM providers (LLM, VLM OCR, embeddings).

NVIDIA's hosted inference API is OpenAI-compatible, so these providers wrap the
`openai` Python SDK pointed at `NVIDIA_BASE_URL`. They share a single client
factory, a retry wrapper and a concurrency semaphore from `ipa.providers.nvidia.client`.
"""

from __future__ import annotations
