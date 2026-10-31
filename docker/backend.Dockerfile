# syntax=docker/dockerfile:1.7
#
# ImportDoc AI — backend API + job worker image.
# Build context is the repository root (the backend resolves REPO_ROOT as /app):
#   docker build -f docker/backend.Dockerfile -t importdoc-ai-backend .

ARG PYTHON_VERSION=3.12

FROM python:${PYTHON_VERSION}-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# libgl1 / libglib2.0-0 are required by PyMuPDF's raster path; curl is only used for healthchecks.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Dependency layer first: only the dependency manifest invalidates it.
COPY src/backend/pyproject.toml /app/src/backend/pyproject.toml
RUN pip install --upgrade pip && pip install /app/src/backend

# Application code. Templates and their mappings ship inside the package.
COPY src/backend/app /app/src/backend/app
COPY src/backend/scripts /app/src/backend/scripts
COPY config.example.yml /app/config.example.yml

# Runtime data (SQLite DB, uploaded files, generated exports). Mount a volume here.
RUN mkdir -p /app/data/storage/files /app/data/exports
VOLUME ["/app/data"]

# Run unprivileged; the data volume inherits ownership on first creation.
RUN useradd --system --uid 10001 --create-home --shell /usr/sbin/nologin importdoc \
 && chown -R importdoc:importdoc /app/data
USER importdoc

WORKDIR /app/src/backend
ENV IMPORTDOC_CONFIG=/app/config.yml \
    DATA_DIR=/app/data/storage \
    DATABASE_URL=sqlite+aiosqlite:////app/data/app.db

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/health || exit 1

# API by default; compose overrides `command` with `python -m app.worker` for the worker.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
