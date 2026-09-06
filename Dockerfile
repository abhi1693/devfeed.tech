FROM ghcr.io/astral-sh/uv:0.12.6 AS uv
FROM python:3.12-slim

COPY --from=uv /uv /uvx /usr/local/bin/
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY pyproject.toml uv.lock .python-version ./
COPY packages packages
COPY apps/api apps/api
COPY apps/aggregator apps/aggregator
COPY apps/cli apps/cli
RUN uv sync --locked --all-packages --no-dev --no-editable \
    && groupadd --gid 10001 devfeed \
    && useradd --uid 10001 --gid 10001 --no-create-home devfeed
COPY alembic.ini ./
COPY migrations migrations
USER 10001:10001
EXPOSE 8000
CMD ["uvicorn", "devfeed_api.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers"]
