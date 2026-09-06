# Build from the repository root: docker build -f apps/admin-api/Dockerfile .
FROM ghcr.io/astral-sh/uv:0.12.6 AS uv
FROM python:3.12-slim
COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages/core packages/core
COPY apps/admin-api apps/admin-api
# Workspace resolution needs manifests, not other services' code or dependencies.
COPY apps/api/pyproject.toml apps/api/pyproject.toml
COPY apps/aggregator/pyproject.toml apps/aggregator/pyproject.toml
COPY apps/notifications/pyproject.toml apps/notifications/pyproject.toml
COPY apps/cli/pyproject.toml apps/cli/pyproject.toml
RUN uv sync --locked --package devfeed-admin-api --no-dev --no-editable \
    && groupadd --gid 10001 devfeed \
    && useradd --uid 10001 --gid 10001 --no-create-home devfeed
USER 10001:10001
EXPOSE 8001
CMD ["uvicorn", "devfeed_admin_api.main:app", "--host", "0.0.0.0", "--port", "8001", "--no-proxy-headers", "--no-access-log"]
