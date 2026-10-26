# ClickHouse resource budgets

Compose mounts `resources.xml` and `users.xml` read-only. These enforce limits in
ClickHouse across all API and report-projector replicas, in addition to each
application process's request semaphore and timeout.

| Budget                    | Default                                |
| ------------------------- | -------------------------------------- |
| ClickHouse container      | 2 CPUs, 2 GiB                          |
| Tracked server memory     | 1.5 GiB                                |
| Concurrent server queries | 6 total, 3 SELECTs, 2 INSERTs          |
| Reader account            | 2 concurrent queries, 512 MiB combined |
| Exporter account          | 3 concurrent queries, 384 MiB combined |
| Individual query          | 256 MiB, 2 threads, 20 seconds         |
| Exporter container        | 0.5 CPU, 512 MiB                       |

The official image's configured `DEVFEED_CLICKHOUSE_USERNAME` retains its existing
administration permissions, including CH-UI access. Its `default` profile limits
all sessions for that account together. The exporter uses a separate private
`devfeed_ingest` account and profile, with schema, materialized-view, read and write
permissions. It cannot manage users. Two busy reader sessions therefore leave a
SELECT slot for exporter metadata or rollup work and two independent INSERT
slots. Reader and exporter memory budgets leave server headroom for merges,
caches, and system tables. Admission limits reject excess queries; the durable
exporter retries without acknowledging uncommitted batches.
The authenticated healthcheck uses the exporter account so reader saturation
cannot consume its allowance.

`DEVFEED_CLICKHOUSE_INGEST_PASSWORD` supplies the exporter credential. An empty
value falls back to the existing ClickHouse password. Set a separate generated
value before starting the exporter, retain it with your private `.env`, and never
commit it. The configuration user reads this value at startup; changing the value
requires recreating ClickHouse and the exporter together. Existing data volumes
and the administration account remain in place.

The shared budgets use the `DEVFEED_CLICKHOUSE_{READER,INGEST,SERVER}_*` settings in
`compose.env.example`. The application query limits use
`DEVFEED_CLICKHOUSE_MAX_{THREADS,EXECUTION_SECONDS,MEMORY_BYTES}`. Profile constraints
prevent a client or CH-UI session from raising those per-query limits.
Lower bounds also reject the zero values that disable memory or time limits.
Change the application and server values together. When scaling the reader query allowance,
keep `SERVER_MAX_SELECTS` at least `READER_MAX_QUERIES + 1` and
`SERVER_MAX_QUERIES` greater than the combined reader and exporter allowances.
Keep the two user memory budgets below the server budget, and the server budget
below the container memory limit. A separately managed ClickHouse deployment
should provision equivalent profiles and configure each process's normal
ClickHouse credentials for its role.

The server uses two background merge workers and bounded mark/uncompressed
caches. Monitor rejected queries, exporter lag, active parts, merge work, and
`system.query_log` before raising limits. Query latency alone can hide scans whose
CPU and read volume grow with retained history. Record `query_duration_ms`,
`read_rows`, `read_bytes`, and `memory_usage` alongside report latency and result
size when comparing table layouts.

Initial bootstrap waits 100 ms between bounded pages and publishes up to 32 dirty
hourly cohorts per cycle; source/topic day cohorts rebuild one at a time. After
bootstrap completes, the exporter polls at the normal one-second cadence even
when busy. New changes coalesce into rollups for up to 30 seconds without moving
their original publication deadline.
