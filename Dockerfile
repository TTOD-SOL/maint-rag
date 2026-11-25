# syntax=docker/dockerfile:1.7
# maint-rag: multi-stage build. The index and embedding model are baked in at build
# time, so the container starts fast and needs no volume or network for retrieval.

# ---------------------------------------------------------------- build stage
FROM python:3.11-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed
WORKDIR /app

# deps first (cached layer)
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project \
    # chromadb drags in the kubernetes client (~125 MB) for its server mode; the embedded
    # persistent client used here never imports it (verified by tests/smoke in CI).
    && rm -rf .venv/lib/python3.11/site-packages/kubernetes* \
    && find .venv -name tests -type d -prune -exec rm -rf {} +

# code + data, then build the index (PDF text comes from data/processed/pages.jsonl)
COPY rag ./rag
COPY data/processed ./data/processed
COPY data/synthetic ./data/synthetic
RUN uv run --no-sync python -m rag.ingest --rebuild --no-smoke \
    && rm -rf /app/.cache/fastembed/*.lock

# ---------------------------------------------------------------- runtime stage
FROM python:3.11-slim AS runtime

# HF Spaces runs containers as uid 1000; match it locally too.
RUN useradd --create-home --uid 1000 app
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 \
    FASTEMBED_CACHE_PATH=/app/.cache/fastembed \
    HF_HUB_OFFLINE=1 ANONYMIZED_TELEMETRY=False \
    PORT=7860
WORKDIR /app

COPY --from=build --chown=app:app /app/.venv ./.venv
COPY --from=build --chown=app:app /app/.cache/fastembed ./.cache/fastembed
COPY --from=build --chown=app:app /app/index ./index
COPY --chown=app:app rag ./rag
COPY --chown=app:app app ./app

ARG APP_VERSION=dev
ENV APP_VERSION=$APP_VERSION

USER app
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import os,urllib.request;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"7860\")}/health',timeout=4)"
CMD ["python", "-m", "app.main"]
