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
  rm -rf reports/coverage
  rm -f reports/python-unit.xml
  mkdir -p reports/coverage/python-unit
  unit_coverage=reports/coverage/python-unit/coverage.db
  backend_include='apps/*/src/**,packages/*/src/**'
  backend_sources=$(uv run --locked --no-build python -c \
    'import tomllib; print(",".join(tomllib.load(open("pyproject.toml", "rb"))["tool"]["coverage"]["run"]["source"]))')
  unit_status=0
  # Keep CI utility measurements for Sonar; the unit floor/report covers every backend file.
  COVERAGE_FILE="$unit_coverage" uv run --locked --no-build coverage run \
    --source="$backend_sources,scripts" -m pytest -q -m 'not integration' \
    --junitxml=reports/python-unit.xml || unit_status=$?
  report_status=0
  uv run --locked --no-build coverage report --data-file="$unit_coverage" \
    --include="$backend_include" --skip-covered || report_status=$?
  uv run --locked --no-build coverage json --data-file="$unit_coverage" \
    --include="$backend_include" -o reports/coverage/unit.json || report_status=$?
  uv run --locked --no-build coverage xml --data-file="$unit_coverage" \
    --include="$backend_include" -o reports/coverage/unit.xml || report_status=$?
  uv run --locked --no-build coverage html --data-file="$unit_coverage" \
    --include="$backend_include" -d reports/coverage/html || report_status=$?
  uv run --locked --no-build python scripts/ci/coverage_report.py --test-exit-code "$unit_status" \
    || report_status=$?
  if [ -n "${GITHUB_STEP_SUMMARY:-}" ]; then
    cat reports/coverage/summary.md >> "$GITHUB_STEP_SUMMARY"
  fi
  if [ "$unit_status" -ne 0 ]; then exit "$unit_status"; fi
  if [ "$report_status" -ne 0 ]; then exit "$report_status"; fi
  uv run --locked python scripts/ci/check_reports.py junit reports/python-unit.xml
fi
if [ "$suite" = unit ]; then exit 0; fi

ci_postgres=""
ci_redis=""
ci_imgproxy=""
cleanup() {
  if [ -n "$ci_postgres" ]; then docker rm -f "$ci_postgres" >/dev/null; fi
  if [ -n "$ci_redis" ]; then docker rm -f "$ci_redis" >/dev/null; fi
  if [ -n "$ci_imgproxy" ]; then docker rm -f "$ci_imgproxy" >/dev/null; fi
}
trap cleanup EXIT
ci_postgres=$(docker run -d --rm -p 127.0.0.1::5432 \
  -e POSTGRES_USER=ci -e POSTGRES_PASSWORD=ci -e POSTGRES_DB=devfeed_test \
  postgres:18-alpine)
ci_redis=$(docker run -d --rm -p 127.0.0.1::6379 redis:8-alpine)
# Contract fixtures serve originals on loopback. Host networking is confined to
# this disposable renderer, bound to loopback with test-only signing credentials.
ci_imgproxy_port=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1]); s.close()')
ci_imgproxy=$(docker run -d --rm --network host --memory 512m \
  -e "IMGPROXY_BIND=127.0.0.1:${ci_imgproxy_port}" \
  -e IMGPROXY_KEY=abababababababababababababababababababababababababababababababab \
  -e IMGPROXY_SALT=cdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcdcd \
  -e IMGPROXY_ALLOW_LOOPBACK_SOURCE_ADDRESSES=true \
  -e IMGPROXY_ALLOWED_SOURCES=http://127.0.0.1: \
  -e IMGPROXY_LOG_LEVEL=error \
  ghcr.io/imgproxy/imgproxy:v4.0.15@sha256:4ec770c72bffea108ba404dc86d3be88e6bf4fd58958782770ff890cca4c7a82)
export DEVFEED_TEST_IMGPROXY_URL="http://127.0.0.1:${ci_imgproxy_port}"
for attempt in $(seq 1 60); do
  if docker exec "$ci_postgres" pg_isready -U ci -d devfeed_test >/dev/null 2>&1 &&
     docker exec "$ci_redis" redis-cli ping | grep -qx PONG &&
     curl --fail --silent --max-time 2 "$DEVFEED_TEST_IMGPROXY_URL/health" >/dev/null; then break; fi
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
mkdir -p "reports/coverage/python-integration-${shard_index}"
COVERAGE_FILE="reports/coverage/python-integration-${shard_index}/coverage.db" \
  DEVFEED_TEST_DATABASE_FAILURES=1 uv run --locked --no-build coverage run -m pytest \
  -p scripts.ci.pytest_shard --ci-shard-index "$shard_index" --ci-shard-count "$shard_count" \
  -q -m integration --junitxml="$report"
uv run --locked python scripts/ci/check_reports.py junit "$report"
