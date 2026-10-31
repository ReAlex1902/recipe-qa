# syntax=docker/dockerfile:1.7
# Reproducible build: base images pinned by digest, Python deps from uv.lock,
# JS deps from package-lock.json. One image serves the API and the static UI.

FROM node:22-alpine@sha256:0a7108bf6c7bf5de370ffb1a3ed6be93d405b43ff159f681a8d18c0e2bc2e402 AS ui
WORKDIR /ui
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

FROM python:3.15-slim-bookworm@sha256:4e03260adeeebafccd084f3584e6b84823aba5651cfb53ef8a078ed441ddf9cd AS deps
COPY --from=ghcr.io/astral-sh/uv:0.11.3@sha256:90bbb3c16635e9627f49eec6539f956d70746c409209041800a0280b93152823 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app/backend
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/src ./src
RUN uv sync --frozen --no-dev

FROM python:3.15-slim-bookworm@sha256:4e03260adeeebafccd084f3584e6b84823aba5651cfb53ef8a078ed441ddf9cd
ARG GIT_SHA=dev
# Unprivileged runtime user.
RUN useradd --create-home --uid 1000 app
WORKDIR /app
COPY --from=deps --chown=app:app /app/backend /app/backend
COPY --chown=app:app backend/data /app/backend/data
COPY --from=ui --chown=app:app /ui/dist /app/frontend/dist
ENV PATH="/app/backend/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    CORPUS_PATH=/app/backend/data/corpus.json \
    STATIC_DIR=/app/frontend/dist \
    GIT_SHA=${GIT_SHA} \
    PORT=7860
USER app
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/healthz', timeout=4)"
CMD ["sh", "-c", "exec uvicorn recipe_qa.main:app --host 0.0.0.0 --port ${PORT} --no-server-header"]
