# Scheduler resource and PostgreSQL impact

Local x86-64, disposable PostgreSQL 18 and Redis 8, 100,000 retained terminal
jobs, ten warmed main-loop ticks per mode. The current implementation reuses SQL
only within a tick, disposes its sockets before polling, and leaves interactive
SQL on its original policy. Earlier results for permanently retained SQL pools
are superseded by this report.

## Session-mode PgBouncer comparison

The pooler matches the production session mode and limits: default/max DB/max user
pool 10, max clients 60, minimum pool 1, idle timeout 120 s, `DISCARD ALL` on return.
One local paired sample was recorded per mode. Do not interpret timings as stable
production estimates or CI thresholds.

| Metric, ten warmed ticks                                           |            Before |             After |
| ------------------------------------------------------------------ | ----------------: | ----------------: |
| Scheduler process CPU                                              |           1.274 s |           0.917 s |
| Median tick latency                                                |          219.5 ms |          174.1 ms |
| Application SQL statements                                         |               400 |               400 |
| DBAPI cursor commands, including setup and empty pre-pings         |               750 |               750 |
| New/closed PgBouncer client connections                            |         350 / 350 |           10 / 10 |
| New PostgreSQL backends behind the prewarmed pooler                |                 0 |                 0 |
| Server-recorded commands, including transaction/utility SQL        |             1,800 |             1,120 |
| Additional empty pre-ping commands excluded by pg_stat_statements  |                 0 |               340 |
| DISCARD ALL commands                                               |               350 |                10 |
| Server planning / execution time                                   | 112.54 / 30.52 ms | 110.98 / 10.42 ms |
| Returned SQL rows                                                  |                60 |                60 |
| Shared buffer hits / physical reads                                |         4,802 / 0 |         4,800 / 0 |
| WAL bytes / temporary blocks                                       |             0 / 0 |             0 / 0 |
| PostgreSQL container CPU                                           |            503 ms |            436 ms |
| Assigned application clients after the cycle                       |                 0 |                 0 |
| Prewarmed PostgreSQL server connections after the cycle            |      1, available |      1, available |
| Idle transactions / lock waiters / held workload locks after cycle |         0 / 0 / 0 |         0 / 0 / 0 |

The same work uses about 28% less scheduler process CPU in this sample, with no
additional application queries, result rows, WAL, physical reads or idle server
slot reservation. PostgreSQL container CPU includes background work and observer
probe overhead; planning/execution time is not a direct database CPU measurement.
The two-hit buffer difference is incidental. This proves a bounded fixture, not
all production workloads. No production sizing or p95/p99 guarantee is made.

`pg_stat_statements.track=all`, planning and I/O timing were enabled in the disposable
server. An observer superuser distinct from the workload role collected counters;
only the workload role's statements were totaled. Driver tracing also captures
empty psycopg pre-pings (`;` in the installed version), which server statement
statistics omit. Statistics were reset only on the isolated disposable server.
Pooler SHOW POOLS/CLIENTS/SERVERS verified no assigned application client after
closing each measured mode. No production statistics were reset.

## Direct PostgreSQL control

A separate direct-connection control used the same retained fixture and ten ticks.
It intentionally excludes the production pooler and cannot represent production
backend-creation savings.

| Metric                                              |              Before |             After |
| --------------------------------------------------- | ------------------: | ----------------: |
| Application SQL / cursor commands                   |           400 / 750 |         400 / 750 |
| New/closed PostgreSQL connections                   |           350 / 350 |           10 / 10 |
| Server-recorded commands                            |               1,450 |             1,110 |
| Server planning / execution time                    | 1,291.92 / 30.65 ms | 450.73 / 12.60 ms |
| Shared buffer hits / physical reads                 |         142,322 / 0 |        45,820 / 0 |
| Returned rows / WAL bytes                           |              60 / 0 |            60 / 0 |
| PostgreSQL container CPU                            |             6.361 s |           1.040 s |
| Idle transactions / held workload locks after cycle |               0 / 0 |             0 / 0 |

## Lifecycle and correctness checks

The native retained-history regression asserts identical scheduling outcomes,
application query counts, no higher driver-command count, and equal created/closed
client counts. Two consecutive successful/failed cycles close every socket before
polling. Other native cases cover concurrent main/partner/reader work, independent
nested transaction boundaries, backend loss within an active cycle, Redis recovery,
expired ingestion leases, and the original queued-job behavior.

```sh
uv run pytest -q -s tests/workers/test_scheduler_resources.py
```

Use only disposable `DEVFEED_TEST_DATABASE_URL` ending in `_test` and Redis database
15, as described in [the test guide](../tests/README.md). The fixture inserts
100,000 terminal jobs and truncates tables. For server-side measurement, enable
pg_stat_statements on that disposable server, collect workload-role counters before
and after the warmed comparison, and trace driver commands separately. Repeat
through session-mode PgBouncer with production-equivalent limits and inspect its
assigned client/server counts between cycles.

## Probe cost and remaining gates

The unchanged probe comparison measured twenty legacy Redis subprocesses at
6.101 CPU seconds versus 1.231 seconds for local-marker probes. The local probe
imports no service configuration or database code and creates no database client.

Native ARM64, a full daemon resource-cap comparison on this revised implementation,
representative active catalogs, sustained mixed backlog, peak RSS and production
before/after CPU/latency remain unmeasured. The earlier full-daemon and RSS values
were collected before the database lifecycle redesign and are not carried forward
as evidence for this head. See the [canary procedure](../dev/scheduler-resources.md).
