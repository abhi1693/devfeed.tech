FROM ghcr.io/astral-sh/uv:0.12.6@sha256:88bc6eb1ccd4b82efd0e1b530caffabddf50dc2bf612e66c14ea25b8ee8a4d3d AS uv
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages packages
COPY apps/api apps/api
COPY apps/aggregator apps/aggregator
COPY apps/notifications apps/notifications
COPY apps/cli apps/cli
COPY apps/admin-api/pyproject.toml apps/admin-api/pyproject.toml
RUN uv sync --locked --no-dev --no-editable \
    --package devfeed-api --package devfeed-aggregator --package devfeed-cli \
    && groupadd --gid 10001 devfeed \
    && useradd --uid 10001 --gid 10001 --no-create-home devfeed
COPY alembic.ini ./
COPY migrations migrations
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "devfeed_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
