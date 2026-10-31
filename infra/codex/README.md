# Codex bridge health

The bridge process exposes a small, unauthenticated health-only HTTP endpoint on
port **4502**, `/readyz`. It returns only `ready` or `unavailable`. Each check still
requires the upstream Codex `/readyz` response to succeed and a fresh connection to
the private Unix bridge socket. Concurrent liveness/readiness probes share only
an in-flight check, never a previous successful result. Failed, timed-out and
stopped-child checks fail closed. The TLS/WebSocket RPC service and credentials
remain on their existing transport and are not exposed through this endpoint.

Compose uses Alpine's native `wget` against loopback instead of starting another
Node process for every probe. The legacy `node /opt/devfeed/health.cjs` probe stays
compatible during mixed-image rollout. The candidate-image smoke validates both
commands and still exercises the shipped entrypoint, private socket permissions,
TLS credentials, model fixture, idle/disconnect cleanup and active-turn safety.

## Measurements

On the actual local amd64 candidate image, twenty successful legacy Node probes
consumed **3.394 seconds of cgroup CPU** and **3.048 seconds of wall time**. Twenty
HTTP/wget probes against the existing bridge process consumed **0.206 seconds of
cgroup CPU** and **0.125 seconds of wall time**. This is about 94% lower probe CPU
in the isolated fixture, not 94% lower total Codex container CPU. Measurements
include the existing bridge/Codex process and probe child processes together.

Run `node --test tests/codex_transport.test.cjs tests/codex_health.test.cjs`, then
build the image and run `bash scripts/ci/smoke-codex.sh IMAGE amd64 0.1.8` on an
amd64 host (use native `arm64` on ARM64). The native smoke records both probe costs
without making timing an unreliable pass/fail threshold.

## Production rollout

After publishing a compatible image with an immutable digest, change the Codex
startup, readiness and liveness probes in GitOps to:

```yaml
httpGet:
  path: /readyz
  port: 4502
```

Preserve the existing periods, timeout and startup failure budget. Port 4502 is
only a health listener: do not add it to the public RPC Service, ingress or tunnel.
Validate kubelet reachability and network policy on the native ARM64 image. A
pre-feature image has no listener on 4502, so keep the legacy exec probe when
rolling back that image. This PR does not change production image pins or manifests.

The separate retention sidecar can use a native shell check of its existing
successful-maintenance marker. That GitOps-only improvement does not depend on
this new endpoint or a new image.
