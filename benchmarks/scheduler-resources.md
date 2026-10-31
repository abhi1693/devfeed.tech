# Scheduler resources and PostgreSQL impact

The proposed implementation reuses only Redis clients and uses a lightweight
local liveness probe. Its SQL session factory and engine are the same shared
objects used before this PR. No PostgreSQL pool or connection-lifetime change is
included. Earlier SQL-reuse performance claims are withdrawn.

## Current implementation: session-mode PgBouncer control

One local x86-64 paired sample, PostgreSQL 18 and Redis 8, 100,000 retained terminal
jobs, ten warmed main-loop ticks. The session-mode pooler matches production:
default/max DB/max user pool 10, max clients 60, minimum pool 1, idle timeout 120 s,
DISCARD ALL on client return. The workload role was separate from the observer.

| Metric                                                                |            Before |             After |
| --------------------------------------------------------------------- | ----------------: | ----------------: |
| New logical main-loop Redis clients after warm-up                     |               110 |                 0 |
| Application SQL statements                                            |               400 |               400 |
| DBAPI cursor commands, including setup/driver SQL                     |               750 |               750 |
| Opened / closed PgBouncer SQL clients                                 |         350 / 350 |         350 / 350 |
| Server-recorded SQL commands, including transactions and utility SQL  |             1,800 |             1,800 |
| New PostgreSQL backends behind the prewarmed pool                     |                 0 |                 0 |
| Returned rows / WAL bytes / physical reads                            |        60 / 0 / 0 |        60 / 0 / 0 |
| Shared buffer hits                                                    |             4,802 |             4,800 |
| SQL planning / execution elapsed                                      | 119.50 / 35.37 ms | 113.96 / 35.55 ms |
| PostgreSQL container CPU                                              |            526 ms |            543 ms |
| Scheduler process CPU                                                 |           1.720 s |           1.671 s |
| Median tick latency                                                   |          234.2 ms |          217.1 ms |
| Assigned clients after work / held workload locks / idle transactions |         0 / 0 / 0 |         0 / 0 / 0 |
| Prewarmed, available PostgreSQL server connections                    |                 1 |                 1 |

Work, statements, rows, writes and connection lifetimes match. Small timing and
buffer variations are noise; there is no established main-loop or database CPU
speedup in this sample. PostgreSQL container CPU includes background and observer
probe work, and SQL execution elapsed is not pure CPU. The useful result is removal
of repeated Redis setup without any new PostgreSQL code path. Empty driver commands
are traced separately because pg_stat_statements can omit them.

The native regression asserts identical scheduling outcomes, application SQL,
driver command and opened/closed SQL connection counts. It also asserts that the
resource owner uses the same shared engine and neither opens nor disposes it at
construction/shutdown. Other cases cover queued work, expired lease recovery and
Redis reconnection.

```sh
uv run pytest -q -s tests/workers/test_scheduler_resources.py
```

Use disposable DEVFEED_TEST_DATABASE_URL ending in `_test` and Redis database 15,
as described in [the test guide](../tests/README.md). Never point the truncating
fixture at an application database. Server counters were collected with
pg_stat_statements.track=all, planning/I/O timing and workload-role filtering on
an isolated server; no production statistics were reset.

## Probe savings

Twenty legacy Redis liveness subprocesses consumed 6.101 CPU seconds versus
1.231 seconds for the local-marker probes (about 80% less probe CPU). Probe code
is unchanged from that measurement. The new probe imports no settings, Redis or
database modules and opens no network/database connection. It preserves heartbeat
expiry, Redis-outage handling and stalled-cycle semantics.

## Rejected SQL-reuse proposal

A prototype reused SQL connections during a main-loop cycle and closed them before
the 15-second wait. It reduced setup/reset work and PostgreSQL CPU in local tests,
but PostgreSQL slot occupancy is also a resource budget. Three paired samples via
SHOW POOLS at 5 ms intervals indicated more assigned slot-time during active cycles:

| Estimated assigned database-slot time, ten ticks | Previous connection policy |    SQL-reuse prototype |
| ------------------------------------------------ | -------------------------: | ---------------------: |
| Median across three paired samples               |          1.50 slot-seconds |      1.76 slot-seconds |
| Sample range                                     |     1.32–1.67 slot-seconds | 1.66–1.82 slot-seconds |
| Peak assigned clients                            |                          1 |                      1 |
| Assigned clients after each cycle                |                          0 |                      0 |

Sampling can miss brief baseline connections, so this is not a precise capacity
forecast. The repeated indication was enough to reject the proposal under the
requirement not to transfer pressure to PostgreSQL. Permanent and active-cycle
scheduler SQL pooling are absent from the proposed code. No unsafe opt-in remains.

## Remaining rollout gates

Native ARM64, the revised full-daemon resource-cap comparison, representative active
catalogs, sustained mixed backlog, peak RSS and production CPU/latency remain
unmeasured. Old daemon/RSS numbers included SQL pooling and do not certify this
implementation. Compare fixed arrival rates and backlog at unchanged admission and
replicas before resizing. More completed jobs can increase database QPS even when
per-job work is unchanged. See the [canary procedure](../dev/scheduler-resources.md).
