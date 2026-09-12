#!/usr/bin/env bash
set -euo pipefail
uv run --locked pre-commit validate-config
uv run --locked python scripts/version.py check
uv run --locked ruff check .
uv run --locked ruff format --check .
uv run --locked mypy
