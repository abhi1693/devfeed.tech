#!/usr/bin/env bash
set -euo pipefail
uv run --locked --no-sync --no-build pre-commit validate-config
uv run --locked --no-sync --no-build python scripts/version.py check
uv run --locked --no-sync --no-build ruff check .
uv run --locked --no-sync --no-build ruff format --check .
uv run --locked --no-sync --no-build mypy
