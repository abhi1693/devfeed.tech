#!/usr/bin/env bash
set -euo pipefail
uv run --locked --no-sync --no-build pre-commit validate-config
uv run --locked --no-sync --no-build python scripts/version.py check
uv run --locked --no-sync --no-build ruff check .
# The finite cutover importer is marked REMOVE_AFTER_CLICKHOUSE_BACKFILL.
uv run --locked --no-sync --no-build ruff check --select C901 --force-exclude \
  --exclude analytics_upgrade.py packages/core/src/analytics*.py \
  apps/admin-api/src/analytics*.py apps/admin-api/src/{article_pipeline,source_pipeline,overview}.py
uv run --locked --no-sync --no-build ruff format --check .
uv run --locked --no-sync --no-build mypy
