"""Domain services: tags, documents, extraction, validation, search.

Domain services orchestrate repositories and stores to implement business rules.
They depend on protocols and repositories injected at construction — never on
global lookups — so tests can substitute fakes. FastAPI routers and Celery step
handlers call these services rather than touching repositories directly.
"""
