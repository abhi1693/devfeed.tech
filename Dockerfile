# Build from the repository root with BuildKit.
FROM ghcr.io/astral-sh/uv:0.12.10@sha256:2bb3ebca0a796a155094a27773d290c4b074572e6107f171d88d086682fd2500 AS uv
FROM python:3.12-alpine3.24@sha256:b64631e04e4920160c50fbe8d8df828f7f35f06f425cb44aa09bca53e708a35a AS python-base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app

FROM python-base AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY packages/http/pyproject.toml packages/http/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/admin-api/pyproject.toml apps/admin-api/pyproject.toml
COPY apps/user-api/pyproject.toml apps/user-api/pyproject.toml
COPY apps/aggregator/pyproject.toml apps/aggregator/pyproject.toml
COPY apps/notifications/pyproject.toml apps/notifications/pyproject.toml
COPY apps/cli/pyproject.toml apps/cli/pyproject.toml
# This dependency layer survives application-source changes. The cache mount
# accelerates local rebuilds; the shared workflow exports layers to GHCR/GHA.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-workspace --package devfeed-api --package devfeed-aggregator --package devfeed-cli
COPY packages/core packages/core
COPY packages/http packages/http
COPY apps/api apps/api
COPY apps/aggregator apps/aggregator
COPY apps/notifications apps/notifications
COPY apps/cli apps/cli
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package devfeed-api --package devfeed-aggregator --package devfeed-cli

FROM python-base AS runtime
# Apply published fixes newer than the pinned Python image's OS packages.
RUN apk upgrade --no-cache \
    && addgroup -S -g 10001 devfeed \
    && adduser -S -D -H -u 10001 -G devfeed devfeed
COPY --from=builder /app/.venv /app/.venv
COPY alembic.ini ./
COPY migrations migrations
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "devfeed_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
