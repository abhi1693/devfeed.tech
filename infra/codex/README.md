# Analysis runtime

Each backend inference opens a fresh ephemeral Codex thread with a deny-all filesystem profile,
network tools disabled unless explicitly permitted for research, and no inherited integrations.
The shipped root setting `thread_unload_delay_secs = 0` releases an inactive thread after its last
subscriber leaves. Active turns remain loaded until their work ends. This avoids the ordinary
30-minute idle grace period for one-shot analysis requests; it uses the existing pinned Codex
0.163.0 capability, independently of the Node 26 runtime update.

The image entrypoint installs `/opt/devfeed/config.toml` in its writable `CODEX_HOME` on each start.
Keep Fleet CLI overrides consistent with this policy. The production `history.persistence="none"`
argument does not replace the shipped configuration. No authentication or permission profile is
relaxed, and the gateway's existing close/cleanup behavior is unchanged.

Run the candidate image's native checks from the repository root:

```sh
bash scripts/ci/smoke-codex.sh IMAGE ARM64_OR_AMD64 VERSION
```

Use `arm64` on the ARM64 release runner (`amd64` is supplemental local proof). The harness runs with
UID/GID 10001, a read-only root filesystem, disposable writable mounts and no external network or
credentials. It invokes the actual shipped entrypoint, checks configuration through the app-server
RPC, and proves idle/disconnected thread cleanup while a local mock response keeps an active turn
safe. It also checks the TLS bridge, Unix socket/health command, native SQLite retention and fresh,
stale and recovered heartbeat probes, legacy cleanup, and the login-present init bypass. The mounted
Fleet script is an [attributed immutable fixture](../../scripts/ci/fixtures/codex/README.md); compare
its hash against the final desired Fleet payload before deployment and the applied ConfigMap after
convergence.

After rollout, observe representative processing for more than 30 minutes. Check loaded-thread
counts, RSS/working-set trends, OOM counters, liveness/readiness and restart counts together. Immediate
thread unloading does not promise immediate allocator RSS release or resolve every memory fault.

Pinned implementation: [configuration schema](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/core/config.schema.json#L8070)
and [inactive-thread lifecycle](https://github.com/openai/codex/blob/rust-v0.163.0/codex-rs/app-server/src/request_processors/thread_lifecycle.rs#L59).
The [app-server documentation](https://learn.chatgpt.com/docs/app-server#unsubscribe-from-a-loaded-thread)
describes the default last-subscriber grace period.
