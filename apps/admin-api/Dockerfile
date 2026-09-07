# Build from the repository root with BuildKit.
FROM ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d AS uv
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea AS python-base
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app

FROM python-base AS builder
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core/pyproject.toml packages/core/pyproject.toml
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/admin-api/pyproject.toml apps/admin-api/pyproject.toml
COPY apps/aggregator/pyproject.toml apps/aggregator/pyproject.toml
COPY apps/notifications/pyproject.toml apps/notifications/pyproject.toml
COPY apps/cli/pyproject.toml apps/cli/pyproject.toml
# This dependency layer survives application-source changes. The cache mount
# accelerates local rebuilds; the shared workflow exports layers to GHCR/GHA.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --no-install-workspace --package devfeed-admin-api
COPY packages/core packages/core
COPY apps/admin-api apps/admin-api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable --package devfeed-admin-api

FROM python-base AS runtime
RUN groupadd --gid 10001 devfeed \
    && useradd --uid 10001 --gid 10001 --no-create-home --no-log-init devfeed
COPY --from=builder /app/.venv /app/.venv
USER 10001:10001
EXPOSE 8001
CMD ["uvicorn", "devfeed_admin_api.main:app", "--host", "0.0.0.0", "--port", "8001", "--no-proxy-headers", "--no-access-log"]
