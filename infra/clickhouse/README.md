# ClickHouse resource budgets

Compose mounts `resources.xml` and `users.xml` read-only. These enforce limits in
ClickHouse across all API replicas and the native exporter, in addition to each
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

## Ingestion and read practices

The exporter follows ClickHouse's [insert strategy guide](https://clickhouse.com/docs/concepts/best-practices/selecting-an-insert-strategy)
and [asynchronous insert guidance](https://clickhouse.com/docs/concepts/features/operations/insert/asyncinserts):

- Capture and historical backfill use 1,000 source records per page by default.
  Native, LZ4-compressed inserts avoid repeatedly parsing JSON on the server.
  Column types are discovered once per table/column shape; concurrent inserts
  use independent driver contexts, as required by the [Python insertion guide](https://clickhouse.com/docs/integrations/language-clients/python/advanced-inserting).
- Every client insert sets `async_insert=1, wait_for_async_insert=1`. The exporter
  waits for the flush and dependent materialized views before acknowledging
  captured IDs. It never acknowledges an in-memory buffer or drops failed work.
  Replays rerun idempotent fact/change materialized views even after partial failure.
- A current-card batch hydrates at most 200 identities across at most eight
  shards of one kind. Canonical changes, complete shard snapshots, and manifests
  each use one insert for the batch. Snapshot copying streams 1,024-row blocks
  under the existing query memory cap. The manifest is published last under the
  native lease; exact identity/generation acknowledgements preserve racing changes.
- Small status/manifest writes remain separate where publication ordering requires
  it. Async buffering combines only matching insert shapes and settings, and a
  single publisher waiting for a flush cannot combine its next sequential insert.
  Application batching therefore matters even with async inserts enabled.
- Queue pressure has its own cached gauge query. Its refresh does not scan
  historical outcomes or throughput. Historical charts share their calculations
  and refresh only on demand; only Queue pressure polls after a successful load.

Keep the existing application, account, and container budgets when adopting these
changes in k3s. The table above describes checked-in defaults; local `.env`
overrides can differ. Avoid increasing worker batch size or query concurrency
without measuring the resident catalogue, insertion buffers, merges, and reads
together. See [the scale results](../../benchmarks/clickhouse-current-serving.md).

Use `system.parts` to watch active part counts and bytes per table, and compare
`system.query_log` read rows/bytes, memory, CPU, and exceptions with exporter lag.
`system.asynchronous_insert_log` records flush failures and flush CPU. More async
requests alone do not prove fewer parts. Leave background merges enabled in the
application; [routine `OPTIMIZE FINAL`](https://clickhouse.com/docs/concepts/best-practices/avoid-optimize-final)
adds merge work and does not solve an excessive insert rate.
