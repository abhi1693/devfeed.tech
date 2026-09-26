#!/usr/bin/env bash
# CI owns these disposable services. The ordinary scripts/test.sh never starts any.
set -euo pipefail
cd "$(dirname "$0")/../.."
suite="${1:-all}"
shard_index="${2:-0}"
shard_count="${3:-1}"
case "$suite" in
  all|unit|integration) ;;
  *) echo 'Usage: python-tests.sh [all|unit|integration] [shard-index] [shard-count]' >&2; exit 2 ;;
esac
if ! [[ "$shard_index" =~ ^[0-9]+$ && "$shard_count" =~ ^[1-9][0-9]*$ ]] ||
   (( shard_index >= shard_count )); then
  echo 'Require 0 <= shard-index < shard-count' >&2
  exit 2
fi
mkdir -p reports
export DEVFEED_DATABASE_URL=postgresql+psycopg://ci@database.invalid/ci
export DEVFEED_REDIS_URL=redis://redis.invalid/15
if [ "$suite" != integration ]; then
  bash scripts/test.sh -q -m 'not integration' --junitxml=reports/python-unit.xml
  uv run --locked python scripts/ci/check_reports.py junit reports/python-unit.xml
fi
if [ "$suite" = unit ]; then exit 0; fi

ci_postgres=""
ci_redis=""
cleanup() {
  if [ -n "$ci_postgres" ]; then docker rm -f "$ci_postgres" >/dev/null; fi
  if [ -n "$ci_redis" ]; then docker rm -f "$ci_redis" >/dev/null; fi
}
trap cleanup EXIT
ci_postgres=$(docker run -d --rm -p 127.0.0.1::5432 \
  -e POSTGRES_USER=ci -e POSTGRES_PASSWORD=ci -e POSTGRES_DB=devfeed_test \
  postgres:18-alpine)
ci_redis=$(docker run -d --rm -p 127.0.0.1::6379 redis:8-alpine)
for attempt in $(seq 1 60); do
  if docker exec "$ci_postgres" pg_isready -U ci -d devfeed_test >/dev/null 2>&1 &&
     docker exec "$ci_redis" redis-cli ping | grep -qx PONG; then break; fi
  if [ "$attempt" -eq 60 ]; then
    echo 'Disposable test services did not become ready' >&2
    exit 1
  fi
  sleep 1
done
ci_pg_port=$(docker port "$ci_postgres" 5432/tcp | cut -d: -f2)
ci_redis_port=$(docker port "$ci_redis" 6379/tcp | cut -d: -f2)
export DEVFEED_TEST_DATABASE_URL="postgresql+psycopg://ci:ci@127.0.0.1:${ci_pg_port}/devfeed_test"
export DEVFEED_TEST_REDIS_URL="redis://127.0.0.1:${ci_redis_port}/15"
# CI has Linux Docker networking and explicitly owns the fault-test resources.
# Exercise recovery here; the report gate correctly rejects skipped integration tests.
report="reports/python-integration-${shard_index}.xml"
DEVFEED_TEST_DATABASE_FAILURES=1 uv run --locked python -m pytest \
  -p scripts.ci.pytest_shard --ci-shard-index "$shard_index" --ci-shard-count "$shard_count" \
  -q -m integration --junitxml="$report"
uv run --locked python scripts/ci/check_reports.py junit "$report"
