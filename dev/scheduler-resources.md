# Scheduler resources

The scheduler reuses a bounded SQL pool **within one main-loop cycle** and
closes its database sockets before the 15-second polling wait, including after a
failed cycle. The initialized SQLAlchemy dialect remains reusable. Sessions keep
independent transactions and commit/rollback boundaries. Only the main thread
uses this engine, so disposing its idle connections cannot interrupt the
interactive dispatch threads.

When the generic database pool is already explicitly enabled, its original
shared engine, limits and idle behavior are preserved without an additional pool.
The active-cycle override applies only to a NullPool baseline.

Interactive recommendation and partner dispatch retain their previous database
connection policy. In production that policy is NullPool; they release each
transaction's client connection instead of retaining extra idle sessions. APIs,
workers and the sitemap refresher keep their existing database access paths.

`DEVFEED_SCHEDULER_CONNECTION_REUSE_ENABLED=true` enables active-cycle reuse even
when workers use `DEVFEED_DATABASE_POOL_ENABLED=false`. The main pool has four
connections and no overflow; `DEVFEED_SCHEDULER_DATABASE_POOL_SIZE` accepts 3–16.
This is an active-cycle maximum, not an idle reservation. Helper connections
outside the main pool still count toward the database/PgBouncer budget. No
inference or article-content cache is introduced. SQLAlchemy rolls back returned
transactions and checks connections between helpers within an active cycle.

Production uses a session-mode PgBouncer pool with ten server slots per replica.
A permanently retained scheduler pool would reserve those slots while idle. That
implementation was replaced: native lifecycle checks prove all main client sockets
close before polling. Session-mode pooler measurements include client setup,
`DISCARD ALL`, implicit transactions and empty driver pre-pings; application query
counts alone omit that work.

Each logical main-loop queue retains its own Redis client, preserving queue
routing, retries, fresh/background budgets and delivery identities. Redis reconnects
broken sockets. Interactive threads own their Redis clients separately. Owned SQL
and Redis resources close on shutdown, including when one client's close fails.

## Work and latency

The main loop still waits 15 seconds after completion. Interactive dispatchers
still poll every second. Batch sizes, source due times, job leases, retries,
publication checks, retention checkpoints and sitemap freshness are unchanged.
The change reduces repeated setup within a cycle; scheduling work is not delayed. CPU needed for ranking and sustained backlog processing still
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
Main-loop database sockets are released before polling; retained Redis clients
remain bounded by the logical queue count. Check PgBouncer assigned clients,
waiters, PostgreSQL CPU, SQL/WAL/buffer work and lock/transaction duration during
canary admission. More completed jobs per second can increase database QPS even
when work per job falls; compare both fixed arrival rates and a sustained backlog
at unchanged replicas and admission budgets. Stop the canary on a sustained
PostgreSQL or pooler regression. No before/after production rollout has occurred.

Set `DEVFEED_SCHEDULER_CONNECTION_REUSE_ENABLED=false` and restart the scheduler to
restore the previous connection policy. No migration or job rewrite is required.
Keep the old Redis health probe during an image rollback: an older image does not
publish the local marker required by the new probe.
