# Build from the repository root with BuildKit.
ARG DOCKERHUB_PREFIX=
ARG REGISTRY_PREFIX=
FROM ${REGISTRY_PREFIX}ghcr.io/astral-sh/uv:0.13.5@sha256:32a7bb39b496babbd997b0a39a4d66c126bdaf5661b302bf6f06abda8deca6ff AS uv
FROM ${DOCKERHUB_PREFIX}python:3.12.15-alpine3.24@sha256:b658b05ce9c4eb97ec6fc19424818a1f31993659c2b505a532f470e83807815d AS python-base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app

FROM python-base AS builder
COPY --from=uv /uv /usr/local/bin/uv
# Select the cached native lz4 wheel; --no-build still prohibits builds during sync.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_NO_BINARY_PACKAGE=lz4
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/http/pyproject.toml packages/http/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/mcp/pyproject.toml apps/mcp/pyproject.toml
COPY apps/admin-api/pyproject.toml apps/admin-api/pyproject.toml
COPY apps/partner-api/pyproject.toml apps/partner-api/pyproject.toml
COPY apps/user-api/pyproject.toml apps/user-api/pyproject.toml
COPY apps/aggregator/pyproject.toml apps/aggregator/pyproject.toml
COPY apps/notifications/pyproject.toml apps/notifications/pyproject.toml
COPY apps/cli/pyproject.toml apps/cli/pyproject.toml
COPY apps/search-indexer/pyproject.toml apps/search-indexer/pyproject.toml
COPY apps/article-enrichment-worker/pyproject.toml apps/article-enrichment-worker/pyproject.toml
COPY apps/images-worker/pyproject.toml apps/images-worker/pyproject.toml
COPY apps/source-discovery-worker/pyproject.toml apps/source-discovery-worker/pyproject.toml
COPY scripts/ci/cache-musl-lz4.sh /usr/local/bin/devfeed-cache-musl-lz4
# This dependency layer survives application-source changes. The cache mount
# accelerates local rebuilds; the shared workflow exports layers to GHCR/GHA.
# PyPI has no native musllinux lz4 wheel. Build this one hash-locked extension
# into uv's native cache; all dependency syncs remain source-build prohibited.
RUN --mount=type=cache,target=/root/.cache/uv \
    apk add --no-cache --virtual .devfeed-lz4-build build-base \
    && sh /usr/local/bin/devfeed-cache-musl-lz4 uv.lock \
    && apk del .devfeed-lz4-build
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-workspace --no-build --package devfeed-mcp
COPY apps/mcp apps/mcp
COPY scripts/ci/python-bootstrap.sh /usr/local/bin/devfeed-python-bootstrap
RUN --mount=type=cache,target=/root/.cache/uv \
    sh /usr/local/bin/devfeed-python-bootstrap . --no-dev --package devfeed-mcp

FROM python-base AS runtime
# Apply published fixes newer than the pinned Python image's OS packages.
RUN apk upgrade --no-cache \
    && addgroup -S -g 10001 devfeed \
    && adduser -S -D -H -u 10001 -G devfeed devfeed
COPY --from=builder /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8003
CMD ["uvicorn", "devfeed_mcp.main:app", "--host", "0.0.0.0", "--port", "8003", "--no-proxy-headers", "--no-access-log", "--limit-concurrency", "64"]
