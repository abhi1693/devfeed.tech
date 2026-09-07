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
if [ "$ci_component" = admin ]; then
  ci_port=3000
  ci_path=/login
else
  ci_port=8000
  ci_path=/version
  if [ "$ci_component" = admin-api ]; then
    ci_port=8001
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
elif [ "$ci_component" = admin-api ]; then
  jq -e --arg version "$ci_version" '.info.version == $version' "$ci_response"
else
  jq -e --arg version "$ci_version" '.version == $version' "$ci_response"
fi
rm -f "$ci_response"
echo "$ci_component on $ci_arch passed its runtime smoke test"
