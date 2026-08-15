# Multi-stage image for the IPA Python services (api, worker, beat).
FROM python:3.12-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app

# Install dependencies first so the layer caches across source edits.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY src ./src
RUN uv sync --frozen --no-dev


FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

# tesseract is the local OCR fallback; libgl/libglib back pillow + pymupdf.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        tesseract-ocr \
        tesseract-ocr-spa \
        libglib2.0-0 \
        curl \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 ipa \
    && useradd --uid 1000 --gid 1000 --create-home ipa

WORKDIR /app

COPY --from=builder --chown=ipa:ipa /app/.venv /app/.venv
COPY --chown=ipa:ipa src ./src
COPY --chown=ipa:ipa alembic.ini ./alembic.ini
COPY --chown=ipa:ipa migrations ./migrations

USER ipa

EXPOSE 8000

CMD ["uvicorn", "ipa.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
