#!/usr/bin/env bash
# CI owns these disposable services. The ordinary scripts/test.sh never starts any.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p reports
export DEVFEED_DATABASE_URL=postgresql+psycopg://ci@database.invalid/ci
export DEVFEED_REDIS_URL=redis://redis.invalid/15
bash scripts/test.sh -q -m 'not integration' --junitxml=reports/python-unit.xml
uv run --locked python scripts/ci/check_reports.py junit reports/python-unit.xml

ci_postgres=""
ci_redis=""
ci_search=""
cleanup() {
  if [ -n "$ci_postgres" ]; then docker rm -f "$ci_postgres" >/dev/null; fi
  if [ -n "$ci_redis" ]; then docker rm -f "$ci_redis" >/dev/null; fi
  if [ -n "$ci_search" ]; then docker rm -f "$ci_search" >/dev/null; fi
}
trap cleanup EXIT
ci_postgres=$(docker run -d --rm -p 127.0.0.1::5432 \
  -e POSTGRES_USER=ci -e POSTGRES_PASSWORD=ci -e POSTGRES_DB=devfeed_test \
  postgres:18-alpine)
ci_redis=$(docker run -d --rm -p 127.0.0.1::6379 redis:8-alpine)
ci_search=$(docker run -d --rm -p 127.0.0.1::8108 --tmpfs /data \
  typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110 \
  --data-dir=/data --api-key=devfeed-disposable-test-key)
ci_search_port=$(docker port "$ci_search" 8108/tcp | cut -d: -f2)
export DEVFEED_TEST_SEARCH_URL="http://127.0.0.1:${ci_search_port}"
for attempt in $(seq 1 60); do
  if docker exec "$ci_postgres" pg_isready -U ci -d devfeed_test >/dev/null 2>&1 &&
     docker exec "$ci_redis" redis-cli ping | grep -qx PONG &&
     curl -fsS "$DEVFEED_TEST_SEARCH_URL/health" >/dev/null; then break; fi
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
bash scripts/test.sh -q -m integration --junitxml=reports/python-integration.xml
uv run --locked python scripts/ci/check_reports.py junit reports/python-integration.xml
