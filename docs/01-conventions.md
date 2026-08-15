# IPA — Conventions and Shared Contracts

**Every agent working on this repo must read this file before writing code.**
It exists so that parallel work merges cleanly.

## Repository layout

```text
ipa/
├── docker-compose.yml
├── Makefile
├── pyproject.toml
├── alembic.ini
├── .env.example
├── apps/
│   └── web/                    # Next.js frontend
├── src/ipa/
│   ├── core/                   # config, logging, otel, errors, enums, ids
│   ├── contracts/              # protocols + shared pydantic models (NO logic)
│   ├── db/                     # sqlalchemy models, session, repositories
│   ├── storage/                # blob (minio), mongo, redis, vector (pgvector)
│   ├── providers/              # nvidia llm/vlm/embeddings, local ocr
│   ├── processing/             # pure functions: pdf, zip, images, chunking
│   ├── domain/                 # tags, documents, extraction, validation services
│   ├── pipeline/               # celery app, orchestrator, steps/
│   ├── api/                    # fastapi app, routers/, schemas/, deps
│   └── mcp/                    # MCP server
├── migrations/versions/        # alembic
├── tests/
│   ├── unit/
│   └── integration/
└── docs/
```

## Ownership map — who may edit what

To avoid merge conflicts, **only the owning task edits these paths.** Other tasks
import from them.

| Path | Owning task |
| --- | --- |
| `src/ipa/core/`, `docker-compose.yml`, `pyproject.toml`, `Makefile` | T01 |
| `src/ipa/contracts/` | T01 (others may only *append* new files, never edit existing) |
| `src/ipa/db/`, `migrations/` | T02 |
| `src/ipa/storage/` | T03 |
| `src/ipa/providers/` | T04 |
| `src/ipa/processing/` | T05 |
| `src/ipa/domain/tags.py`, `src/ipa/api/routers/tags.py` | T06 |
| `src/ipa/domain/documents.py`, `src/ipa/api/routers/documents.py` | T07 |
| `src/ipa/pipeline/` (except `steps/`) | T08 |
| `src/ipa/pipeline/steps/decompose.py` | T09 |
| `src/ipa/pipeline/steps/ocr.py` | T10 |
| `src/ipa/pipeline/steps/extract.py` | T11 |
| `src/ipa/pipeline/steps/embed.py` | T12 |
| `src/ipa/domain/validation.py`, `src/ipa/api/routers/review.py` | T13 |
| `src/ipa/domain/search.py`, `src/ipa/api/routers/search.py` | T14 |
| `src/ipa/mcp/` | T15 |
| `src/ipa/domain/webhooks.py`, `src/ipa/api/routers/webhooks.py`, `src/ipa/api/auth.py` | T16 |
| `apps/web/` | T17 |
| `tests/integration/` | T18 |

Each task also owns `tests/unit/<its area>/`.

**Migrations:** any task needing a new table adds its **own** Alembic revision file
rather than editing T02's. Always set `down_revision` to the current head and note
it in your PR/commit message.

## Coding rules

- Python 3.12. Type hints everywhere. `from __future__ import annotations`.
- Pydantic v2 for all boundary models; SQLAlchemy 2.0 typed ORM for persistence.
  Never leak ORM objects out of `src/ipa/db/`.
- All I/O is `async` except Celery task bodies and CPU-bound processing.
- **Every module and public function needs a docstring** (project requirement).
  Module docstring states responsibility; function docstring states args, returns,
  raises.
- Never `print`. Use `structlog.get_logger(__name__)`.
- Never read `os.environ` directly outside `src/ipa/core/config.py`.
- Every step and adapter must be idempotent and safe to retry.
- Format/lint with `ruff` (line length 100). Type-check with `mypy` in strict-ish mode.
- Tests use `pytest` + `pytest-asyncio`. External services mocked in `tests/unit/`.

## Shared enums (`src/ipa/core/enums.py`, owned by T01)

```python
class PipelineStep(StrEnum):
    STORE = "store"
    DECOMPOSE = "decompose"
    OCR = "ocr"
    EXTRACT = "extract"
    EMBED = "embed"
    REVIEW = "review"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SKIPPED = "skipped"


class DocumentStatus(StrEnum):
    RECEIVED = "received"
    PROCESSING = "processing"
    PENDING_REVIEW = "pending_review"
    VALIDATED = "validated"
    COMPLETED = "completed"
    FAILED = "failed"
    QUARANTINED = "quarantined"


class FieldType(StrEnum):
    STRING = "string"
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    ENUM = "enum"
    ARRAY = "array"
    OBJECT = "object"


class ExtractionSource(StrEnum):
    MODEL = "model"
    HUMAN = "human"
```

`PipelineStep.ORDER` (a module-level tuple) defines execution order and is the basis
for "reprocess from step N onward".

## Shared protocols (`src/ipa/contracts/`, owned by T01)

Agents code **against these**, not against concrete classes.

```python
class BlobStore(Protocol):
    async def put(self, key: str, data: bytes, content_type: str) -> str: ...
    async def get(self, key: str) -> bytes: ...
    async def exists(self, key: str) -> bool: ...
    async def presigned_url(self, key: str, expires_s: int = 900) -> str: ...
    async def delete(self, key: str) -> None: ...


class ContentStore(Protocol):
    """MongoDB-backed store for OCR text and extracted JSON."""
    async def put_pages(self, document_id: UUID, pages: list[PageText]) -> None: ...
    async def get_pages(self, document_id: UUID) -> list[PageText]: ...
    async def put_extraction(self, record: ExtractionRecord) -> str: ...
    async def get_extraction(self, document_id: UUID, version: int | None = None) -> ExtractionRecord | None: ...
    async def list_extractions(self, document_id: UUID) -> list[ExtractionRecord]: ...
    async def delete_document(self, document_id: UUID) -> None: ...


class CacheStore(Protocol):
    async def get_json(self, key: str) -> dict | None: ...
    async def set_json(self, key: str, value: dict, ttl_s: int) -> None: ...
    async def delete(self, key: str) -> None: ...
    async def claim_idempotency(self, key: str, ttl_s: int) -> bool: ...
    async def lock(self, key: str, ttl_s: int) -> AsyncContextManager[bool]: ...


class LlmProvider(Protocol):
    async def complete_json(
        self, *, system: str, user: str, json_schema: dict,
        images: list[ImageRef] | None = None, model: str | None = None,
    ) -> LlmJsonResult: ...


class OcrProvider(Protocol):
    async def ocr_image(self, image: bytes, mime: str) -> OcrResult: ...


class EmbeddingProvider(Protocol):
    @property
    def model(self) -> str: ...
    @property
    def dimension(self) -> int: ...
    async def embed(self, texts: list[str], *, kind: Literal["query", "passage"]) -> list[list[float]]: ...


class VectorStore(Protocol):
    async def upsert(self, chunks: list[ChunkVector]) -> None: ...
    async def delete_document(self, document_id: UUID) -> None: ...
    async def search(self, embedding: list[float], *, query_text: str | None,
                     top_k: int, filters: SearchFilters | None) -> list[ChunkHit]: ...
```

## Shared DTOs (`src/ipa/contracts/models.py`)

`PageText`, `OcrResult`, `ImageRef`, `LlmJsonResult`, `ExtractionRecord`,
`ExtractedField`, `ChunkVector`, `ChunkHit`, `SearchFilters`, `StepContext`,
`StepResult`. Exact shapes are defined in T01 — read the file, do not re-invent.

The important one:

```python
class ExtractedField(BaseModel):
    key: str
    value: Any
    confidence: float = Field(ge=0.0, le=1.0)
    page: int | None = None
    evidence: str | None = None       # verbatim snippet supporting the value
    valid: bool = True
    validation_errors: list[str] = []
```

## Blob key scheme

```text
originals/{sha256[:2]}/{sha256}                     # content-addressed original
pages/{document_id}/{page:05d}.png                  # rendered page image
thumbs/{document_id}/{page:05d}.jpg                 # thumbnail
```

## API conventions

- Base path `/v1`. JSON only, `snake_case` fields.
- Errors: RFC 7807 `application/problem+json` with `type`, `title`, `status`,
  `detail`, `instance`, and a `code` string.
- Long-running work: `202 Accepted` + resource with `status`.
- Pagination: `?limit=&cursor=`, response `{ "items": [...], "next_cursor": null }`.
- `Idempotency-Key` header supported on all `POST` that create resources.
- Auth: `X-API-Key` header for machine clients, session JWT for the UI. Enforced by
  a single dependency in `src/ipa/api/auth.py` (T16). Until T16 lands, other tasks
  import a stub dependency `require_auth` from that module.

## Environment variables

Defined once in `.env.example` (T01). Naming: `IPA_*` for app config,
`NVIDIA_*` for the provider, plus service URLs. Never commit a real `.env`.

## Definition of done for every task

1. Code written with docstrings on all modules and public functions.
2. Unit tests in `tests/unit/<area>/` pass; external services mocked.
3. `make lint typecheck test` is green.
4. New env vars added to `.env.example` and documented.
5. `docs/` updated only if the task changes a documented contract.
