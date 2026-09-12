#!/usr/bin/env bash
# Own disposable services so profiling can never truncate the development database.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p reports
profile_postgres=""
profile_redis=""
profile_search=""
cleanup() {
  if [ -n "$profile_postgres" ]; then docker rm -f "$profile_postgres" >/dev/null; fi
  if [ -n "$profile_redis" ]; then docker rm -f "$profile_redis" >/dev/null; fi
  if [ -n "$profile_search" ]; then docker rm -f "$profile_search" >/dev/null; fi
}
trap cleanup EXIT
profile_postgres=$(docker run -d --rm -p 127.0.0.1::5432 \
  -e POSTGRES_USER=profile -e POSTGRES_PASSWORD=profile -e POSTGRES_DB=devfeed_profile_test \
  postgres:18.6-alpine3.24)
profile_redis=$(docker run -d --rm -p 127.0.0.1::6379 redis:8.2-alpine)
for attempt in $(seq 1 60); do
  if docker exec "$profile_postgres" pg_isready -U profile -d devfeed_profile_test >/dev/null 2>&1 &&
     [ "$(docker exec "$profile_redis" redis-cli ping)" = PONG ]; then break; fi
  if [ "$attempt" -eq 60 ]; then
    echo 'Disposable profiling services did not become ready' >&2
    exit 1
  fi
  sleep 1
done
profile_pg_port=$(docker port "$profile_postgres" 5432/tcp | cut -d: -f2)
profile_redis_port=$(docker port "$profile_redis" 6379/tcp | cut -d: -f2)
export DEVFEED_TEST_DATABASE_URL="postgresql+psycopg://profile:profile@127.0.0.1:${profile_pg_port}/devfeed_profile_test"
export DEVFEED_TEST_REDIS_URL="redis://127.0.0.1:${profile_redis_port}/15"
export DEVFEED_DATABASE_URL="$DEVFEED_TEST_DATABASE_URL"
export DEVFEED_REDIS_URL="$DEVFEED_TEST_REDIS_URL"
export DEVFEED_PROFILE_ROWS="${DEVFEED_PROFILE_ROWS:-1000}"
export DEVFEED_PROFILE_REPEATS="${DEVFEED_PROFILE_REPEATS:-10}"
export DEVFEED_PROFILE_CONCURRENCY="${DEVFEED_PROFILE_CONCURRENCY:-8}"
export DEVFEED_PROFILE_REPORT="${1:-reports/api-profile.json}"
if [ "${DEVFEED_PROFILE_SUITE:-all}" = search ]; then
  profile_search=$(docker run -d --rm -p 127.0.0.1::8108 --tmpfs /data \
    typesense/typesense:30.2@sha256:610f2d34b1f93d00762869da2c67736775e5798d19a2c8b91b014b8a0cc1e110 \
    --data-dir=/data --api-key=devfeed-disposable-test-key)
  profile_search_port=$(docker port "$profile_search" 8108/tcp | cut -d: -f2)
  export DEVFEED_TEST_SEARCH_URL="http://127.0.0.1:${profile_search_port}"
  export DEVFEED_SEARCH_PROFILE_REPORT="$DEVFEED_PROFILE_REPORT"
  for attempt in $(seq 1 60); do
    if curl -fsS "$DEVFEED_TEST_SEARCH_URL/health" >/dev/null 2>&1; then break; fi
    if [ "$attempt" -eq 60 ]; then
      echo 'Disposable search service did not become ready' >&2
      exit 1
    fi
    sleep 1
  done
fi
case "${DEVFEED_PROFILE_SUITE:-all}" in
  all) profile_tests=(tests/test_api_query_budgets.py tests/test_table_query_budgets.py) ;;
  core) profile_tests=(tests/test_api_query_budgets.py) ;;
  tables) profile_tests=(tests/test_table_query_budgets.py) ;;
  discovery) profile_tests=(tests/test_discovery_query_budgets.py) ;;
  search) profile_tests=(tests/test_search_query_budgets.py) ;;
  *) echo 'DEVFEED_PROFILE_SUITE must be all, core, tables, discovery or search' >&2; exit 1 ;;
esac
uv run --locked pytest -q "${profile_tests[@]}"
uv run --locked python - "$DEVFEED_PROFILE_REPORT" "${DEVFEED_PROFILE_SUITE:-all}" <<'PY'
import sys
from pathlib import Path

target = Path(sys.argv[1])
if sys.argv[2] == "search":
    print(f"Search API profile: {target}")
elif sys.argv[2] == "discovery":
    print(f"Discovery API profile: {target}")
elif sys.argv[2] != "tables":
    print(f"Core API profile: {target}")
if sys.argv[2] in {"all", "tables"}:
    print(f"Table API profile: {target.with_suffix('.tables.json')}")
PY
