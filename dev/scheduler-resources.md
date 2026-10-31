# Scheduler resources

The scheduler retains the Redis clients used by its main dispatch loop. It uses
the existing shared SQL session factory unchanged: no scheduler-specific SQL pool,
connection cap, pre-ping policy, transaction scope, isolation level or database
connection lifetime is introduced. Interactive dispatchers, workers, APIs and the
sitemap refresher keep their existing database ownership.

`DEVFEED_SCHEDULER_CONNECTION_REUSE_ENABLED=true` controls Redis-client reuse only.
The SQL-pool proposal was withdrawn because the production session-mode PgBouncer
pool has finite server slots. Even active-cycle reuse indicated higher assigned
slot-time in local sampling. Keeping that optimization would trade application CPU
for database capacity. Its negative evidence remains in the measurement report.

Each logical main-loop queue retains its own Redis client, preserving queue
routing, retries, fresh/background budgets and delivery identities. Redis reconnects
broken sockets. Interactive threads own their Redis clients separately. Owned
Redis clients close on shutdown, including when one client's close fails.
The shared SQL engine remains owned by its existing application lifecycle.

## Work and latency

The main loop still waits 15 seconds after completion. Interactive dispatchers
still poll every second. Batch sizes, source due times, job leases, retries,
publication checks, retention checkpoints and sitemap freshness are unchanged.
The change avoids repeated Redis-client setup and expensive liveness imports;
scheduling work is not delayed. CPU needed for ranking and sustained backlog processing still
scales with workload. [Measurements](../benchmarks/scheduler-resources.md) compare
identical work with and without reuse.

## Lightweight liveness

Use this command after upgrading the scheduler image:

```yaml
livenessProbe:
  exec:
    command: [python, -m, devfeed_aggregator.scheduler_probe]
  periodSeconds: 30
  timeoutSeconds: 5
  failureThreshold: 3
  initialDelaySeconds: 90
```

The heartbeat publishes an owned local marker only after its Redis heartbeat
succeeds. A failed Redis heartbeat removes the marker, so the next probe fails
after the heartbeat detects the outage (at most its 20-second cadence). The probe checks that marker without importing settings, Redis,
SQLAlchemy or scheduler code. Both heartbeat forms expire after 120 seconds;
stalled or repeatedly failing cycles stop renewal after the existing five-minute
deadline. Startup clears a previous marker; shutdown removes the current marker.

The old Redis probe remains compatible. Compose uses the new probe. Production
GitOps must switch its probe separately after upgrading the image; this application
PR does not change cluster manifests or deploy anything.

## Production canary and rollback

Keep the PR in draft until a native ARM64 comparison covers idle operation,
representative active catalogs, sustained mixed backlog and provider/Redis/SQL
recovery. Compare scheduler CPU/RSS, PostgreSQL CPU and connection count, queue
age, throughput, p95/p99 interactive admission latency and tick duration against
the same release/configuration without reuse. Check the exact image digest and
GitOps convergence before reducing production resource reservations.

A 100m CPU request and 256 MiB memory request are initial canary candidates, not
validated production sizing. Keep burst capacity and measured memory headroom.
PostgreSQL connection ownership is unchanged; retained Redis clients remain
bounded by the logical queue count. Check PgBouncer assigned clients,
waiters, PostgreSQL CPU, SQL/WAL/buffer work and lock/transaction duration during
canary admission. More completed jobs per second can increase database QPS even
when work per job falls; compare both fixed arrival rates and a sustained backlog
at unchanged replicas and admission budgets. Stop the canary on a sustained
PostgreSQL or pooler regression. No before/after production rollout has occurred.

Set `DEVFEED_SCHEDULER_CONNECTION_REUSE_ENABLED=false` and restart the scheduler to
restore per-pass Redis-client creation. No migration or job rewrite is required.
Keep the old Redis health probe during an image rollback: an older image does not
publish the local marker required by the new probe.
