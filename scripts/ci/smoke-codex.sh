#!/usr/bin/env bash
set -euo pipefail
image=$1
architecture=$2
version=$3
container=""
tls_initializer=""
tls_volume=""
fixture_directory=$(mktemp -d "${TMPDIR:-/tmp}/devfeed-codex-smoke.XXXXXX")
cleanup() {
  if [[ -n "$container" ]]; then docker rm -f "$container" >/dev/null || true; fi
  if [[ -n "$tls_initializer" ]]; then docker rm -f "$tls_initializer" >/dev/null || true; fi
  if [[ -n "$tls_volume" ]]; then docker volume rm "$tls_volume" >/dev/null || true; fi
  rm -rf "$fixture_directory"
}
trap cleanup EXIT
trap 'echo "Codex native smoke failed at line $LINENO" >&2; if [[ -n "$container" ]]; then docker logs "$container" >&2; fi; if [[ -n "$tls_initializer" ]]; then docker logs "$tls_initializer" >&2; fi' ERR
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
# Match the Fleet Secret volume's root ownership, fsGroup and 0440 file mode.
# Only this Docker volume is chowned; the bind-mounted host fixtures stay untouched.
cat > "$fixture_directory/tls-init.cjs" <<'JS'
const fs = require("node:fs");
for (const name of ["cert.pem", "key.pem"]) {
  const destination = `/run/smoke-tls/${name}`;
  fs.writeFileSync(destination, fs.readFileSync(`/run/tls-source/${name}`), { mode: 0o440 });
  fs.chownSync(destination, 0, 10001);
  fs.chmodSync(destination, 0o440);
}
fs.chownSync("/run/smoke-tls", 0, 10001);
fs.chmodSync("/run/smoke-tls", 0o550);
JS
chmod 644 "$fixture_directory/tls-init.cjs"
tls_volume=$(docker volume create --label devfeed.ci.fixture=codex-tls)
tls_initializer=$(docker create --network none --read-only --cap-drop ALL --cap-add CHOWN \
  --security-opt no-new-privileges --user 0:0 \
  --mount "type=bind,src=$fixture_directory,dst=/run/tls-source,readonly" \
  --mount "type=volume,src=$tls_volume,dst=/run/smoke-tls" \
  --entrypoint node "$image" /run/tls-source/tls-init.cjs)
timeout 20 docker start -a "$tls_initializer"
docker rm "$tls_initializer" >/dev/null
tls_initializer=""
container=$(docker run -d --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges --user 10001:10001 --memory 1g \
  --tmpfs /tmp:rw,nosuid,nodev,size=128m,mode=1777 \
  --tmpfs /var/lib/codex:rw,nosuid,nodev,size=128m,mode=0700,uid=10001,gid=10001 \
  --tmpfs /var/lib/codex-runtime:rw,nosuid,nodev,size=64m,mode=0700,uid=10001,gid=10001 \
  --tmpfs /run/codex:rw,nosuid,nodev,size=1m,mode=0700,uid=10001,gid=10001 \
  -e CODEX_SQLITE_HOME=/var/lib/codex-runtime \
  --mount "type=bind,src=$PWD/scripts/ci,dst=/opt/devfeed-smoke,readonly" \
  --mount "type=volume,src=$tls_volume,dst=/run/smoke-tls,readonly" \
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
