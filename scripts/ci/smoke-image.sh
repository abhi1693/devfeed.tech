#!/usr/bin/env bash
set -euo pipefail
ci_image=$1
ci_component=$2
ci_arch=$3
ci_version=$4
actual_arch=$(docker image inspect "$ci_image" --format '{{.Architecture}}')
test "$actual_arch" = "$ci_arch"
ci_container=""
trap 'if [ -n "$ci_container" ]; then docker rm -f "$ci_container" >/dev/null; fi' EXIT
trap 'echo "$ci_component smoke failed at line $LINENO" >&2; if [ -n "$ci_container" ]; then docker logs "$ci_container" >&2; fi' ERR
if [ "$ci_component" = mcp ]; then
  ci_container=$(docker run -d --rm "$ci_image")
  for attempt in $(seq 1 30); do
    if docker exec "$ci_container" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/health/live', timeout=2).close()" 2>/dev/null; then break; fi
    if [ "$attempt" -eq 30 ]; then exit 1; fi
    sleep 1
  done
  docker exec -i "$ci_container" python - "$ci_version" <<'PY'
import asyncio
import importlib.util
import json
import os
import sys
import urllib.request

from mcp import Client

assert os.geteuid() != 0
assert importlib.util.find_spec("devfeed_core") is None
assert importlib.util.find_spec("sqlalchemy") is None
with urllib.request.urlopen("http://127.0.0.1:8003/version", timeout=3) as response:
    assert json.load(response)["version"] == sys.argv[1]

async def check():
    async with Client("http://127.0.0.1:8003/mcp") as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "search", "get_article", "get_feed", "list_topics", "list_sources", "get_source"
        }

asyncio.run(check())
PY
  echo "MCP on $ci_arch passed its runtime and protocol smoke tests"
  exit 0
fi
if [ "$ci_component" = codex ]; then
  expected_codex_version=$(python3 -c 'import json; print(json.load(open("infra/codex/package.json"))["dependencies"]["@openai/codex"])')
  actual_codex_version=$(docker run --rm "$ci_image" --version)
  test "$actual_codex_version" = "codex-cli $expected_codex_version"
  echo "$actual_codex_version"
  ci_container=$(docker run -d --rm "$ci_image")
  for attempt in $(seq 1 30); do
    if docker exec "$ci_container" node /opt/devfeed/health.cjs; then
      docker exec -i "$ci_container" node - "$ci_version" < scripts/ci/smoke-codex.cjs
      echo "Codex on $ci_arch passed its runtime smoke test"
      exit 0
    fi
    if [ "$attempt" -eq 30 ]; then docker logs "$ci_container"; exit 1; fi
    sleep 1
  done
fi
if [ "$ci_component" = article-enrichment-worker ] || [ "$ci_component" = images-worker ] || [ "$ci_component" = source-discovery-worker ]; then
  ci_command=devfeed-article-enrichment-worker
  case "$ci_component" in
    images-worker) ci_command=devfeed-images-worker ;;
    source-discovery-worker) ci_command=devfeed-source-discovery-worker ;;
  esac
  docker run --rm "$ci_image" "$ci_command" --help >/dev/null
  docker run --rm --entrypoint python "$ci_image" - "$ci_component" <<'PY'
import importlib.util
import sys

component = sys.argv[1]
required = {
    "article-enrichment-worker": ("lingua", "trafilatura"),
    "images-worker": ("boto3", "PIL"),
    "source-discovery-worker": (),
}[component]
forbidden = {
    "article-enrichment-worker": ("boto3", "PIL", "websockets"),
    "images-worker": ("lingua", "trafilatura", "websockets"),
    "source-discovery-worker": ("boto3", "PIL", "lingua", "trafilatura", "websockets"),
}[component]
assert all(importlib.util.find_spec(module) for module in required)
assert all(importlib.util.find_spec(module) is None for module in forbidden)
PY
  echo "$ci_component on $ci_arch passed its runtime smoke test"
  exit 0
fi
if [ "$ci_component" = admin ] || [ "$ci_component" = web ]; then
  ci_port=3000
  ci_path=/login
  if [ "$ci_component" = web ]; then ci_path=/; fi
else
  ci_port=8000
  ci_path=/version
  if [ "$ci_component" = admin-api ]; then
    ci_port=8001
    ci_path=/openapi.json
  fi
  if [ "$ci_component" = user-api ]; then
    ci_port=8002
    ci_path=/openapi.json
  fi
fi
ci_container=$(docker run -d --rm -p "127.0.0.1::${ci_port}" \
  -e DEVFEED_DATABASE_URL=postgresql+psycopg://ci@database.invalid/ci \
  -e DEVFEED_REDIS_URL=redis://redis.invalid/15 \
  -e DEVFEED_METRICS_ENABLED=true -e DEVFEED_METRICS_HOST=0.0.0.0 \
  -e DEVFEED_OTLP_ENDPOINT=http://127.0.0.1:1 \
  -e DEVFEED_PYROSCOPE_SERVER=http://127.0.0.1:1 \
  -e DEVFEED_VERSION="$ci_version" -p 127.0.0.1::9100 "$ci_image")
ci_host_port=$(docker port "$ci_container" "${ci_port}/tcp" | cut -d: -f2)
ci_response=$(mktemp)
for attempt in $(seq 1 30); do
  if curl --fail --silent --max-time 15 "http://127.0.0.1:${ci_host_port}${ci_path}" > "$ci_response"; then break; fi
  if [ "$attempt" -eq 30 ]; then
    docker logs "$ci_container"
    rm -f "$ci_response"
    exit 1
  fi
  sleep 1
done
ci_metrics_port=$(docker port "$ci_container" 9100/tcp | cut -d: -f2)
if [ "$ci_component" = admin ] || [ "$ci_component" = web ]; then
  curl --fail --silent --max-time 5 "http://127.0.0.1:${ci_metrics_port}/metrics" > "$ci_response.metrics"
  grep -q 'devfeed_build_info' "$ci_response.metrics"
  grep -q 'component="profiling".* 1' "$ci_response.metrics"
  rm -f "$ci_response.metrics"
else
  # Health/version probes are excluded from native FastAPI request metrics.
  curl --fail --silent --max-time 15 "http://127.0.0.1:${ci_host_port}/openapi.json" >/dev/null
  curl --fail --silent --max-time 5 "http://127.0.0.1:${ci_metrics_port}/metrics" >/dev/null
  docker exec -i "$ci_container" python - "$ci_version" <<'PY'
import sys
import urllib.request

from prometheus_client.parser import text_string_to_metric_families

with urllib.request.urlopen("http://127.0.0.1:9100/metrics", timeout=5) as response:
    samples = [
        sample
        for family in text_string_to_metric_families(response.read().decode())
        for sample in family.samples
    ]
assert any(
    sample.name == "target_info" and sample.labels.get("service_version") == sys.argv[1]
    for sample in samples
), "Missing OpenTelemetry service version"
assert any(
    sample.name == "http_server_request_duration_seconds_count"
    and sample.labels.get("http_route") == "/openapi.json"
    and sample.labels.get("http_request_method") == "GET"
    and sample.labels.get("http_response_status_code") == "200"
    and sample.value >= 1
    for sample in samples
), "Missing successful FastAPI request measurement"
PY
fi
test "$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${ci_host_port}/metrics")" = 404
if [ "$ci_component" = admin ]; then
  grep -q 'Sign in to DevFeed Admin' "$ci_response"
elif [ "$ci_component" = web ]; then
  grep -q "DevFeed" "$ci_response"
elif [ "$ci_component" = admin-api ] || [ "$ci_component" = user-api ]; then
  jq -e --arg version "$ci_version" '.info.version == $version' "$ci_response"
else
  jq -e --arg version "$ci_version" '.version == $version' "$ci_response"
fi
if [ "$ci_component" != admin ] && [ "$ci_component" != web ]; then
  # Exercise native wheels under the image's libc, including Alpine's musl.
  docker exec -i "$ci_container" python - "$ci_component" <<'PY'
import os
import ssl
import sys

import psycopg
import uvloop
from pydantic_core import SchemaValidator

assert os.geteuid() != 0
assert ssl.create_default_context().get_ca_certs()
assert psycopg.pq.__impl__ == "binary"
assert psycopg.pq.version() > 0
assert SchemaValidator({"type": "int"}).validate_python("42") == 42
uvloop.new_event_loop().close()
if sys.argv[1] == "backend":
    import trafilatura
    from lingua import Language, LanguageDetectorBuilder
    from lxml import etree

    assert etree.fromstring(b"<root>ok</root>").text == "ok"
    detector = LanguageDetectorBuilder.from_languages(Language.ENGLISH, Language.FRENCH).build()
    assert detector.detect_language_of("This is a clearly written English sentence.") == Language.ENGLISH
    paragraph = "This article explains how software developers build and test reliable applications. " * 20
    assert trafilatura.extract(f"<html><body><article><p>{paragraph}</p></article></body></html>")
else:
    import jwt
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode({"sub": "runtime-smoke"}, key, algorithm="RS256")
    assert jwt.decode(token, key.public_key(), algorithms=["RS256"])["sub"] == "runtime-smoke"
PY
fi
if [ "$ci_component" = backend ]; then
  docker exec -i "$ci_container" python < scripts/ci/profile-smoke.py
  docker exec "$ci_container" devfeed --version
  docker exec "$ci_container" devfeed-worker --help >/dev/null
  docker exec "$ci_container" devfeed-scheduler --help >/dev/null
fi
rm -f "$ci_response"
echo "$ci_component on $ci_arch passed its runtime smoke test"
