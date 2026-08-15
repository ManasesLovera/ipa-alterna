# T01 — Foundation: repo, compose, config, contracts, OTel

- **Wave:** 0 (must run alone; everything else depends on it)
- **Depends on:** nothing
- **Owns:** `pyproject.toml`, `Makefile`, `docker-compose.yml`, `.env.example`,
  `src/ipa/core/`, `src/ipa/contracts/`, `src/ipa/api/main.py` (skeleton only),
  `alembic.ini`

## Goal

Produce the skeleton every other task builds on: dependency management, local
infrastructure, typed configuration, structured logging, OpenTelemetry bootstrap,
shared enums, shared protocols, and shared DTOs.

**This task defines contracts. Get them right — 18 other tasks import them.**

## Deliverables

### 1. `pyproject.toml`

Python 3.12, managed with `uv` (or Poetry if you prefer — pick one and document it).

Runtime deps: `fastapi`, `uvicorn[standard]`, `pydantic>=2`, `pydantic-settings`,
`sqlalchemy>=2`, `asyncpg`, `psycopg[binary]`, `pgvector`, `alembic`, `celery[redis]`,
`redis`, `motor`, `minio` (or `aioboto3`), `openai`, `httpx`, `pymupdf`, `pillow`,
`pytesseract`, `python-multipart`, `structlog`, `mcp`,
`opentelemetry-sdk`, `opentelemetry-exporter-otlp`, plus the instrumentation
packages for fastapi, sqlalchemy, redis, celery, httpx.

Dev deps: `pytest`, `pytest-asyncio`, `pytest-cov`, `ruff`, `mypy`, `types-*`,
`httpx` (test client), `fakeredis`, `mongomock-motor`, `testcontainers` (optional,
used by T18).

Ruff line-length 100. Mypy: `disallow_untyped_defs = true`, ignore missing imports
for untyped third-party libs only.

### 2. `docker-compose.yml`

Services, all on one network with healthchecks:

| Service | Image | Notes |
| --- | --- | --- |
| `postgres` | `pgvector/pgvector:pg16` | db `ipa`, volume, healthcheck `pg_isready` |
| `mongo` | `mongo:7` | db `ipa`, volume |
| `redis` | `redis:7-alpine` | appendonly yes |
| `minio` | `minio/minio` | console on 9001, volume |
| `minio-init` | `minio/mc` | one-shot: create buckets `ipa-documents`, set policy |
| `otel-collector` | `otel/opentelemetry-collector-contrib` | config file in `ops/otel-collector.yaml`, exports to stdout by default |
| `jaeger` | `jaegertracing/all-in-one` | trace UI on 16686 |
| `api` | build `.` | uvicorn, depends_on healthy |
| `worker` | build `.` | celery worker |
| `beat` | build `.` | celery beat (retry sweeper) |
| `web` | build `apps/web` | Next.js, profile `full` |

Add a `Dockerfile` (multi-stage, non-root user) at repo root for the Python image.

### 3. `Makefile`

Targets: `up`, `down`, `logs`, `shell`, `migrate`, `revision`, `lint`, `format`,
`typecheck`, `test`, `test-integration`, `seed`, `worker`, `api`, `web`.

### 4. `.env.example`

```dotenv
# --- App ---
IPA_ENV=local
IPA_LOG_LEVEL=INFO
IPA_API_PORT=8000
IPA_SECRET_KEY=change-me
IPA_MAX_UPLOAD_MB=200
IPA_ALLOWED_MIME=application/pdf,image/png,image/jpeg,image/tiff,application/zip

# --- PostgreSQL ---
IPA_POSTGRES_DSN=postgresql+asyncpg://ipa:ipa@postgres:5432/ipa

# --- MongoDB ---
IPA_MONGO_URI=mongodb://mongo:27017
IPA_MONGO_DB=ipa

# --- Redis ---
IPA_REDIS_URL=redis://redis:6379/0
IPA_CELERY_BROKER_URL=redis://redis:6379/1
IPA_CELERY_RESULT_BACKEND=redis://redis:6379/2
IPA_CACHE_TTL_S=3600

# --- MinIO ---
IPA_S3_ENDPOINT=http://minio:9000
IPA_S3_ACCESS_KEY=minioadmin
IPA_S3_SECRET_KEY=minioadmin
IPA_S3_BUCKET=ipa-documents
IPA_S3_SECURE=false

# --- NVIDIA NIM (OpenAI-compatible) ---
NVIDIA_API_KEY=
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_LLM_MODEL=
NVIDIA_VLM_MODEL=
NVIDIA_EMBED_MODEL=
NVIDIA_EMBED_DIM=1024
NVIDIA_TIMEOUT_S=120
NVIDIA_MAX_RETRIES=3

# --- OCR ---
IPA_OCR_TEXT_LAYER_MIN_CHARS=120
IPA_OCR_PAGE_DPI=220
IPA_OCR_FALLBACK_ENABLED=true
IPA_TESSERACT_LANGS=eng+spa

# --- Pipeline ---
IPA_STEP_MAX_ATTEMPTS=3
IPA_STEP_RETRY_BACKOFF_S=15
IPA_CHUNK_TOKENS=512
IPA_CHUNK_OVERLAP=64

# --- Observability ---
OTEL_SERVICE_NAME=ipa-api
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4317
OTEL_TRACES_SAMPLER=parentbased_always_on
```

Model IDs are intentionally blank — the operator fills them from the NVIDIA catalog.
Config must fail fast at startup with a clear message if a required model ID is empty
while the corresponding feature is enabled.

### 5. `src/ipa/core/config.py`

A single `Settings(BaseSettings)` with nested sub-models (`PostgresSettings`,
`MongoSettings`, `RedisSettings`, `S3Settings`, `NvidiaSettings`, `OcrSettings`,
`PipelineSettings`, `OtelSettings`). `env_prefix="IPA_"` for app settings, explicit
aliases for `NVIDIA_*` and `OTEL_*`. Expose a cached `get_settings()`.

**No other module may read `os.environ`.**

### 6. `src/ipa/core/logging.py`

`structlog` configured for JSON output, bound `trace_id`/`span_id` from the active
OTel context, `document_id` and `step` bound via context vars.

### 7. `src/ipa/core/otel.py`

`setup_telemetry(service_name: str)` — configures tracer + meter providers, OTLP
exporter, and instruments FastAPI, SQLAlchemy, Redis, httpx, Celery. Safe to call
from both the API and worker entrypoints. No-op cleanly when the endpoint is unset.

### 8. `src/ipa/core/errors.py`

Base `IpaError` with `code`, `http_status`, `detail`. Subclasses:
`NotFoundError`, `ConflictError`, `ValidationError`, `UnsupportedMediaError`,
`QuarantineError`, `ProviderError`, `RetryableProviderError`, `StepFailedError`.
Plus an RFC 7807 exception handler factory for FastAPI.

### 9. `src/ipa/core/enums.py`

Exactly as specified in `docs/01-conventions.md`, plus:

```python
STEP_ORDER: tuple[PipelineStep, ...] = (
    PipelineStep.STORE, PipelineStep.DECOMPOSE, PipelineStep.OCR,
    PipelineStep.EXTRACT, PipelineStep.EMBED, PipelineStep.REVIEW,
)

def steps_from(step: PipelineStep) -> tuple[PipelineStep, ...]:
    """Return `step` and every step after it, in execution order."""
```

### 10. `src/ipa/contracts/`

- `protocols.py` — the `Protocol` classes listed in `docs/01-conventions.md`.
- `models.py` — the DTOs. Define at minimum:

```python
class ImageRef(BaseModel):
    page: int
    blob_key: str
    mime: str

class PageText(BaseModel):
    page: int
    text: str
    source: Literal["text_layer", "vlm", "tesseract"]
    confidence: float | None = None
    char_count: int

class OcrResult(BaseModel):
    text: str
    confidence: float | None = None
    source: Literal["vlm", "tesseract"]

class LlmJsonResult(BaseModel):
    data: dict[str, Any]
    raw_text: str
    model: str
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_ms: int

class ExtractedField(BaseModel):        # see conventions
    ...

class ExtractionRecord(BaseModel):
    document_id: UUID
    version: int
    tag_id: UUID
    tag_version: int
    source: ExtractionSource
    model: str | None
    prompt_hash: str | None
    fields: list[ExtractedField]
    document_confidence: float
    created_at: datetime
    created_by: str | None = None

class ChunkVector(BaseModel):
    document_id: UUID
    chunk_index: int
    text: str
    embedding: list[float]
    embed_model: str
    page_from: int | None
    page_to: int | None
    metadata: dict[str, Any] = {}

class SearchFilters(BaseModel):
    tag_ids: list[UUID] | None = None
    document_ids: list[UUID] | None = None
    statuses: list[DocumentStatus] | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None

class ChunkHit(BaseModel):
    document_id: UUID
    chunk_index: int
    text: str
    score: float
    page_from: int | None
    page_to: int | None
    document_title: str | None = None

class StepContext(BaseModel):
    document_id: UUID
    step: PipelineStep
    attempt: int
    trace_id: str | None = None

class StepResult(BaseModel):
    status: StepStatus
    detail: str | None = None
    metrics: dict[str, float] = {}
    next_step_override: PipelineStep | None = None
```

### 11. `src/ipa/api/main.py` (skeleton)

`create_app()` factory that wires settings, logging, telemetry, the RFC 7807 handler,
CORS, and `/healthz` + `/readyz`. Routers are registered by later tasks via a list in
`src/ipa/api/routers/__init__.py` — create that file with an empty `ROUTERS: list` so
later tasks only append.

### 12. `alembic.ini` + `migrations/env.py`

Configured for async SQLAlchemy; `target_metadata` imported from
`ipa.db.base:Base` (T02 creates that module — import lazily / tolerate absence with a
clear TODO so T01 can be verified standalone).

## Acceptance criteria

- `docker compose up -d` brings all infra services to healthy.
- `curl localhost:8000/healthz` returns 200; `/readyz` checks Postgres, Mongo, Redis,
  MinIO reachability.
- `make lint typecheck test` green.
- `from ipa.contracts.protocols import BlobStore` etc. all import cleanly.
- A trace for a `/healthz` request is visible in Jaeger at `localhost:16686`.
- Unit tests: settings load from env, `steps_from()` ordering, error → problem+json
  mapping.
