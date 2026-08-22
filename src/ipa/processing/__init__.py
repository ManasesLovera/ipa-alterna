"""Pure, dependency-light document processing utilities.

This package turns raw file bytes into the intermediate products the pipeline
needs: detected MIME types, page images, expanded archives, text chunks and JSON
Schemas. It is deliberately side-effect free — no database, no network, no
settings singletons — so every function is trivially testable and reusable by
both the pipeline and the API.

Everything here is synchronous and CPU-bound; callers run it in thread executors
where it would otherwise block the event loop.
"""
