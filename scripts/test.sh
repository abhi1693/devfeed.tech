#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
# Never provision or start services. Integration tests use only explicitly supplied
# DEVFEED_TEST_DATABASE_URL and DEVFEED_TEST_REDIS_URL and otherwise skip themselves.
exec uv run --locked pytest "$@"
