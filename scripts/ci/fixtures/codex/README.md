# Codex Fleet command fixture

`retention.cjs.fixture` is an exact snapshot of the Fleet-owned script, not application runtime code.
Production ownership remains in `abhi1693/home-lab`:

- Commit: `767e3061ec666dbedda27f68ecbaa86ffe3c101a`
- Path: `kubernetes/projects/applications/apps/devfeed/codex-log-retention.cjs`
- SHA-256: `b574ab87cf3fe2518f58a8beee6b63254f773b101f2f4807a29214b55af4b563`

The `.fixture` suffix prevents formatting tools from rewriting the immutable bytes. The smoke
driver mounts them with the production `retention.cjs` filename.

The native Codex smoke executes this unchanged script as UID/GID 10001 against disposable SQLite
logs, checks its heartbeat using the deployment probe, and executes its `--cleanup-legacy` command.
It verifies that diagnostic cleanup preserves synthetic authentication and unrelated state files.
No real credentials, model provider, or production volume is used.

Before deployment, compare this fixture's SHA-256 with the **final Fleet ConfigMap payload**, not just
its filename. Stop if they differ; refresh the attributed fixture and repeat native validation.
The production script is mounted as `/opt/codex-runtime/retention.cjs`. Keep the production deployment's
commands, flags, writable mounts and probes aligned with these native contract checks.

After Fleet convergence, compare the applied payload without printing any secret data:

```sh
sha256sum scripts/ci/fixtures/codex/retention.cjs.fixture
kubectl -n devfeed get configmap devfeed-codex-runtime \
  -o go-template='{{index .data "retention.cjs"}}' | sha256sum
```

The rendered desired ConfigMap must have the same hash before rollout. Re-run the image contracts
when changing this fixture or the Fleet script; a source-manifest image digest change alone does not
change the script contract.
