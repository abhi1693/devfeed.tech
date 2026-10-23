#!/usr/bin/env bash
set -euo pipefail
image=$1
architecture=$2
version=$3
container=""
fixture_directory=$(mktemp -d "${TMPDIR:-/tmp}/devfeed-codex-smoke.XXXXXX")
cleanup() {
  if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null; fi
  rm -rf "$fixture_directory"
}
trap cleanup EXIT
trap 'echo "Codex native smoke failed at line $LINENO" >&2; if [[ -n "$container" ]]; then docker logs "$container" >&2; fi' ERR
[[ $(docker image inspect "$image" --format '{{.Architecture}}') = "$architecture" ]]
# The harness invokes this same entrypoint inside the isolated native fixture.
[[ $(docker image inspect "$image" --format '{{json .Config.Entrypoint}}') = '["sh","/opt/devfeed/entrypoint.sh"]' ]]
[[ $(docker image inspect "$image" --format '{{.Config.User}}') = '10001:10001' ]]
expected_version=$(python3 -c 'import json; print(json.load(open("infra/codex/package.json"))["dependencies"]["@openai/codex"])')
actual_version=$(docker run --rm --network none "$image" --version)
[[ "$actual_version" = "codex-cli $expected_version" ]]
# Synthetic, short-lived TLS material only. No operator credentials or home mount.
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=localhost \
  -addext subjectAltName=DNS:localhost -keyout "$fixture_directory/key.pem" \
  -out "$fixture_directory/cert.pem" >/dev/null 2>&1
cp scripts/ci/fixtures/codex/retention.cjs.fixture "$fixture_directory/retention.cjs"
chmod 755 "$fixture_directory"
chmod 644 "$fixture_directory/key.pem" "$fixture_directory/cert.pem" "$fixture_directory/retention.cjs"
container=$(docker run -d --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 10001:10001 --memory 1g \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 \
  --tmpfs /var/lib/codex:rw,nosuid,nodev,size=128m,mode=0700,uid=10001,gid=10001 \
  --tmpfs /var/lib/codex-runtime:rw,nosuid,nodev,size=64m,mode=0700,uid=10001,gid=10001 \
  --tmpfs /run/codex:rw,nosuid,nodev,size=1m,mode=0700,uid=10001,gid=10001 \
  -e CODEX_SQLITE_HOME=/var/lib/codex-runtime \
  --mount "type=bind,src=$PWD/scripts/ci,dst=/opt/devfeed-smoke,readonly" \
  --mount "type=bind,src=$fixture_directory,dst=/run/smoke-tls,readonly" \
  --mount "type=bind,src=$fixture_directory,dst=/opt/codex-runtime,readonly" \
  --entrypoint node "$image" /opt/devfeed-smoke/smoke-codex.cjs "$version")
# The in-container harness launches the normal shipped entrypoint with Fleet flags.
# Wait is bounded even if a child fails to exit or its protocol stops responding.
if ! timeout 75 docker wait "$container" > "$fixture_directory/status"; then
  echo "Codex native smoke exceeded 75 seconds" >&2
  exit 1
fi
docker logs "$container"
[[ $(cat "$fixture_directory/status") = 0 ]]
echo "Codex $actual_version on $architecture passed native lifecycle and Fleet command contracts"
