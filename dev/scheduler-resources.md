# Scheduler resources

The scheduler owns a small SQL connection pool and retains the Redis clients used
by its main dispatch loop. The main loop, interactive recommendation dispatcher,
and partner refill thread share that SQL pool. Their transactions and sessions
remain independent. APIs and workers retain their existing database configuration;
the sitemap refresher retains its existing database access path.

`DEVFEED_SCHEDULER_CONNECTION_REUSE_ENABLED=true` enables reuse even when workers
use `DEVFEED_DATABASE_POOL_ENABLED=false`. The default scheduler pool size is four
with no overflow. `DEVFEED_SCHEDULER_DATABASE_POOL_SIZE` accepts 3–16; three
connections allow simultaneous main, partner, and reader transactions. Additional
helper connections outside this pool still count toward the database/PgBouncer
budget. No inference or article-content cache is introduced. SQLAlchemy rolls back
returned transactions before reuse and checks idle connections before checkout.

Each logical main-loop queue retains its own Redis client, preserving queue
routing, retries, fresh/background budgets and delivery identities. Redis reconnects
broken sockets. Interactive threads own their Redis clients separately. Owned SQL
and Redis resources close on shutdown, including when one client's close fails.

## Work and latency

The main loop still waits 15 seconds after completion. Interactive dispatchers
still poll every second. Batch sizes, source due times, job leases, retries,
publication checks, retention checkpoints and sitemap freshness are unchanged.
The change removes connection setup rather than delaying work or moving it to
another service. CPU needed for ranking and sustained backlog processing still
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
Retaining connections trades a bounded set of idle SQL/Redis connections for lower
setup cost; check PgBouncer's budget and long-idle failover behavior. The available
cluster credentials returned authentication errors during this investigation, so
there is no live before/after production measurement.

Set `DEVFEED_SCHEDULER_CONNECTION_REUSE_ENABLED=false` and restart the scheduler to
restore the previous connection policy. No migration or job rewrite is required.
Keep the old Redis health probe during an image rollback: an older image does not
publish the local marker required by the new probe.
