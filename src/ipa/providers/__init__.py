"""Provider implementations for LLM, OCR and embedding capabilities.

The provider layer sits between the pipeline and NVIDIA NIM (and the local
Tesseract fallback). Every provider satisfies the protocol in
`ipa.contracts.protocols` structurally — there is no base class. Consumers code
against the protocol, never against a concrete provider, so swapping a backend
never touches pipeline code.

`ipa.providers.fake` holds deterministic fakes used by every other task's unit
tests; it is part of the public surface even though it is never wired into
production.
"""
