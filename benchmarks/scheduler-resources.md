# Scheduler connection resources

Measured on x86-64 with disposable PostgreSQL 18, Redis 8 and ClickHouse 26.8.
These are local measurements, not ARM64 or production results.

| Workload                                                | Without reuse | With reuse |
| ------------------------------------------------------- | ------------: | ---------: |
| 20 warmed idle main-loop ticks: process CPU             |       4.643 s |    1.030 s |
| Same ticks: wall time                                   |      14.931 s |    1.958 s |
| Same ticks: Application SQL statements                  |           860 |        860 |
| Same ticks: new SQL connections                         |           720 |          0 |
| Separate process peak RSS                               |     125.9 MiB |  126.3 MiB |
| 10 warmed ticks with 100,000 retained jobs: process CPU |       2.100 s |    0.455 s |
| Same retained-history ticks: median latency             |      674.8 ms |    85.3 ms |
| Same retained-history ticks: Application SQL statements |           400 |        400 |
| Same retained-history ticks: new SQL connections        |           350 |          0 |

The retained-history comparison verifies identical scheduling outcomes and an
unchanged 100,000-job terminal cohort. SQL counts differ between fixtures, so
compare each row only within its workload. Warm-up is excluded from the table;
zero connections means no new connections after warm-up, not no open connections.
Counts cover SQLAlchemy application statements; driver connection setup and
pre-ping SQL are outside those counters. CPU timings are observations, not CI
pass/fail thresholds. Memory remains roughly
unchanged; this change reduces CPU and connection churn, not stored payload size.

Run against disposable services using the integration environment described in
[the test guide](../tests/README.md):

```sh
uv run pytest -q -s tests/workers/test_scheduler_resources.py
```

The test requires a `*_test` database and Redis database 15. It inserts 100,000
synthetic terminal jobs and uses the existing truncating fixtures; never point it
at a real application database. The suite also checks simultaneous three-lane
access, idle SQL backend loss, Redis reconnection and expired ingestion recovery.
Existing partner and publication/catalog tests exercise independent refill and
publication safety with the new factory ownership.

For a daemon comparison on disposable services, seed retained history with
`tests.support.scheduler.retained_analysis_jobs(session_factory())`, then run each
mode in a separate process. Supply the same database/Redis settings and AI flags.
A Linux user systemd scope can constrain the actual daemon to 100m CPU and 256 MiB:

```sh
DEVFEED_SCHEDULER_CONNECTION_REUSE_ENABLED=true systemd-run --user --scope \
  -p CPUQuota=10% -p MemoryMax=256M \
  /usr/bin/time -v timeout --signal=TERM 65s uv run devfeed scheduler
```

Repeat with reuse disabled, including the same startup/measurement window.
`timeout` returning 124 is expected at the end of this bounded exercise. This is
an idle/retained-history resource check; it does not certify those limits for
active production catalogs or a sustained mixed backlog. See the
[canary and rollback procedure](../dev/scheduler-resources.md).

## Probe cost

Twenty subprocess executions of the production Redis probe consumed 6.101 CPU
seconds (6.262 wall seconds); twenty local-marker probes consumed 1.231 CPU
seconds (1.261 wall seconds), about 80% less CPU. Both checked a fresh heartbeat
against the same disposable Redis instance; the marker was written before timing.
The local check also passes with deliberately invalid database and Redis settings,
so it neither validates application configuration nor establishes network clients.

## Full daemon under a local resource cap

Both modes also completed a 65-second daemon window under a Linux systemd scope
with `CPUQuota=10%` (100m CPU) and `MemoryMax=256M`, with 100,000 retained jobs.
Maintenance checkpoints and disposable Redis data were reset before each mode.
The measurement starts after Python imports and excludes external probe processes.

| Measurement                | Without reuse | With reuse |
| -------------------------- | ------------: | ---------: |
| Completed main-loop ticks  |             5 |          5 |
| Process CPU                |       2.720 s |    1.007 s |
| New SQL connections        |           371 |          4 |
| Application SQL statements |           424 |        426 |
| Process peak RSS           |     126.8 MiB |  126.8 MiB |

This observed about 63% less daemon CPU. Background threads run on independent
clocks, so their poll counts can differ slightly; the isolated main-loop test
above verifies identical application queries and outcomes. These windows cover
idle operation with retained history, not sustained active production throughput.
The original implementation also fit this idle cap, so this exercise does not
establish that production's present resource reservation is necessary or sufficient.
