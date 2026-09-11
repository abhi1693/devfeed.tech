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
  -e DEVFEED_REDIS_URL=redis://redis.invalid/15 "$ci_image")
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
if [ "$ci_component" = admin ]; then
  grep -q 'Admin sign-in' "$ci_response"
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
  docker exec "$ci_container" devfeed --version
  docker exec "$ci_container" devfeed-worker --help >/dev/null
  docker exec "$ci_container" devfeed-scheduler --help >/dev/null
fi
rm -f "$ci_response"
echo "$ci_component on $ci_arch passed its runtime smoke test"
