#!/usr/bin/env bash
set -euo pipefail
ci_image=$1
ci_component=$2
ci_arch=$3
ci_version=$4
actual_arch=$(docker image inspect "$ci_image" --format '{{.Architecture}}')
[[ "$actual_arch" = "$ci_arch" ]]
ci_container=""
trap 'if [[ -n "$ci_container" ]]; then docker rm -f "$ci_container" >/dev/null; fi' EXIT
trap 'echo "$ci_component smoke failed at line $LINENO" >&2; if [[ -n "$ci_container" ]]; then docker logs "$ci_container" >&2; fi' ERR
if [[ "$ci_component" = mcp ]]; then
  # Fleet uses the image's default command with UID/GID10001, a read-only
  # root, no capabilities and only /tmp writable. External APIs stay isolated.
  ci_container=$(docker run -d --rm --network none --read-only --cap-drop ALL \
    --security-opt no-new-privileges --user 10001:10001 \
    --tmpfs /tmp:rw,nosuid,nodev,size=64m,mode=1777 \
    -e DEVFEED_MCP_API_URL=http://127.0.0.1:8000 "$ci_image")
  for attempt in $(seq 1 30); do
    if docker exec "$ci_container" python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8003/health/live', timeout=2).close()" 2>/dev/null; then break; fi
    if [[ "$attempt" -eq 30 ]]; then exit 1; fi
    sleep 1
  done
  docker exec -i "$ci_container" python - "$ci_version" <<'PY'
import asyncio
import errno
import importlib.util
import json
import os
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from mcp import Client

assert os.geteuid() == os.getegid() == 10001
with open("/proc/self/status") as status:
    assert next(line.split()[1] for line in status if line.startswith("CapEff:")) == "0000000000000000"
try:
    with open("/app/mcp-smoke-must-not-write", "w"):
        pass
except OSError as error:
    assert error.errno == errno.EROFS
else:
    raise AssertionError("MCP root filesystem must be read-only")
with tempfile.TemporaryFile(dir="/tmp") as writable:
    writable.write(b"disposable MCP tmp fixture")
    writable.seek(0)
    assert writable.read() == b"disposable MCP tmp fixture"
assert importlib.util.find_spec("devfeed_core") is None
assert importlib.util.find_spec("sqlalchemy") is None
with urllib.request.urlopen("http://127.0.0.1:8003/version", timeout=3) as response:
    assert json.load(response)["version"] == sys.argv[1]

def health(path):
    try:
        with urllib.request.urlopen("http://127.0.0.1:8003" + path, timeout=3) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        with error:
            return error.code, json.load(error)

assert health("/health/live") == (200, {"status": "ok"})
assert health("/health/ready") == (503, {"status": "unavailable"})

class PublicFixture(BaseHTTPRequestHandler):
    calls = []

    def do_GET(self):
        self.calls.append(self.path)
        if self.path != "/v1/topics?limit=1":
            self.send_error(404)
            return
        body = b'{"items":[],"next_cursor":null}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass

# Probe the actual running MCP process using only a same-container public stub.
# This is not proof of production API, personal OAuth or Redis availability.
fixture = ThreadingHTTPServer(("127.0.0.1", 8000), PublicFixture)
thread = threading.Thread(target=fixture.serve_forever, daemon=True)
thread.start()
try:
    assert health("/health/ready") == (200, {"status": "ok"})
    assert PublicFixture.calls == ["/v1/topics?limit=1"]
finally:
    fixture.shutdown()
    fixture.server_close()
    thread.join(timeout=2)
assert health("/health/ready") == (503, {"status": "unavailable"})
assert health("/health/live") == (200, {"status": "ok"})

async def check():
    async with Client("http://127.0.0.1:8003/mcp") as client:
        tools = await client.list_tools()
        assert {tool.name for tool in tools.tools} == {
            "search", "get_article", "get_feed", "list_topics", "list_sources", "get_source"
        }

asyncio.run(check())
PY
  echo "MCP on $ci_arch passed Fleet filesystem/capability, local public-readiness and protocol smoke tests"
  exit 0
fi
if [[ "$ci_component" = codex ]]; then
  bash scripts/ci/smoke-codex.sh "$ci_image" "$ci_arch" "$ci_version"
  exit 0
fi
if [[ "$ci_component" = article-enrichment-worker ]] || [[ "$ci_component" = images-worker ]] || [[ "$ci_component" = source-discovery-worker ]]; then
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
  python3 scripts/ci/smoke_fleet_contracts.py "$ci_image" "$ci_component"
  echo "$ci_component on $ci_arch passed its runtime smoke test"
  exit 0
fi
if [[ "$ci_component" = admin ]] || [[ "$ci_component" = web ]] || [[ "$ci_component" = partner ]]; then
  ci_port=3000
  ci_path=/login
  if [[ "$ci_component" = web ]]; then ci_path=/; fi
else
  ci_port=8000
  ci_path=/version
  if [[ "$ci_component" = admin-api ]]; then
    ci_port=8001
    ci_path=/openapi.json
  fi
  if [[ "$ci_component" = partner-api ]]; then
    ci_port=8004
    ci_path=/openapi.json
  fi
  if [[ "$ci_component" = user-api ]]; then
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
  if [[ "$attempt" -eq 30 ]]; then
    docker logs "$ci_container"
    rm -f "$ci_response"
    exit 1
  fi
  sleep 1
done
ci_metrics_port=$(docker port "$ci_container" 9100/tcp | cut -d: -f2)
if [[ "$ci_component" = admin ]] || [[ "$ci_component" = web ]] || [[ "$ci_component" = partner ]]; then
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
[[ "$(curl --silent --output /dev/null --write-out '%{http_code}' "http://127.0.0.1:${ci_host_port}/metrics")" = 404 ]]
if [[ "$ci_component" = admin ]]; then
  grep -q 'Sign in to DevFeed Admin' "$ci_response"
elif [[ "$ci_component" = partner ]]; then
  grep -q "DevFeed" "$ci_response"
elif [[ "$ci_component" = web ]]; then
  grep -q "DevFeed" "$ci_response"
elif [[ "$ci_component" = admin-api ]] || [[ "$ci_component" = user-api ]] || [[ "$ci_component" = partner-api ]]; then
  jq -e --arg version "$ci_version" '.info.version == $version' "$ci_response"
else
  jq -e --arg version "$ci_version" '.version == $version' "$ci_response"
fi
if [[ "$ci_component" != admin ]] && [[ "$ci_component" != web ]] && [[ "$ci_component" != partner ]]; then
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
if [[ "$ci_component" = backend ]]; then
  docker exec -i "$ci_container" python < scripts/ci/profile-smoke.py
  docker exec "$ci_container" devfeed --version
  docker exec "$ci_container" devfeed-worker --help >/dev/null
  docker exec "$ci_container" devfeed-scheduler --help >/dev/null
fi
rm -f "$ci_response"
python3 scripts/ci/smoke_fleet_contracts.py "$ci_image" "$ci_component"
echo "$ci_component on $ci_arch passed its runtime smoke test"
