# Worker dispatch and process overhead

These measurements compare the worker changes against the ClickHouse branch at
`687e0ef9`. They describe local Linux x86_64 fixtures, not the deployed `0.1.4`
release, production ARM64 throughput, or a cluster capacity guarantee.

## Durable dispatch

The fixture contained 25,000 queued ingestion jobs with separate synthetic sources,
ordered deadlines, the full Alembic schema, application sessions and analytics
capture. PostgreSQL 18 ran with two CPUs and 2 GiB; Redis 8 provided the actual
`DurableQueue` adapter. Database pooling remained disabled. Each case used three
runs; queue contents and dispatch markers were reset before each run.

Publication still occurs before the database commit. Unique delivery identities
repair interrupted publication; the benchmark does not substitute a no-op queue
or use RQ bulk enqueue to bypass that behavior. Analytics capture reads and
normalization are included. ClickHouse transport, provider inference, extraction,
and execution of the resulting jobs are outside this dispatch measurement.

| Route              | Deliveries | Baseline median | Candidate median | Speedup | Baseline → candidate commits |
| ------------------ | ---------: | --------------: | ---------------: | ------: | ---------------------------: |
| Single lane        |        100 |       582.67 ms |        128.81 ms |    4.5× |                        1 → 1 |
| Single lane        |      1,000 |     5,397.60 ms |      1,066.35 ms |    5.1× |                        1 → 1 |
| Shared queue lanes |        100 |     3,110.30 ms |        141.54 ms |   22.0× |                      100 → 1 |
| Shared queue lanes |      1,000 |    36,178.76 ms |      1,042.54 ms |   34.7× |                    1,000 → 1 |

SQLAlchemy execution events show the source of the reduction:

| Route              | Deliveries | Baseline → candidate SELECTs | Baseline → candidate UPDATE batches |
| ------------------ | ---------: | ---------------------------: | ----------------------------------: |
| Single lane        |        100 |                      300 → 3 |                             100 → 1 |
| Single lane        |      1,000 |                   3,000 → 11 |                           1,000 → 1 |
| Shared queue lanes |        100 |                      301 → 4 |                             100 → 1 |
| Shared queue lanes |      1,000 |                   3,001 → 12 |                           1,000 → 1 |

An UPDATE batch is one driver `executemany` operation, not one updated row or one
server execution. Every delivered job still receives its own durable dispatch
marker. The SELECT totals include analytics preimages and terminal-attempt checks
in groups of at most 200 identities. Shared lanes also perform their bounded
candidate selection. Batching reduces client round trips and transactions; these
results do not claim fewer changed rows or measured PostgreSQL CPU/WAL savings.

The first batching implementation still performed 1,006 SELECTs for 1,000 direct
deliveries because analytics capture reread each ORM update separately. Batching
those preimages removed that remaining cost. General SQL predicates retain their
exact filters rather than widening reads to a guessed primary-key set.

The measured corpus is one job kind. Shared-lane ordering, overlapping lanes,
concurrent claims, lease replacement, partial Redis publication and discovery
repair are covered separately by native regression tests. A blocked or already
published prefix still needs representative query-plan measurements when sizing
much larger production backlogs; these timings do not establish that every query
has constant scan cost.

## Native health probe

The native `devfeed-worker-health` executable reads the atomically replaced parent
heartbeat, compares its boot-clock timestamp with `/proc/uptime`, and checks that
the positive parent PID remains alive. It uses shell builtins and starts neither
Python nor a Redis client. The probe makes no network request; it can accept a
fresh marker during a transient Redis outage. A sustained inability to acknowledge
RQ heartbeats still lets that marker expire. Queue progress and application error
metrics remain separate signals.

A local comparison of 50 fresh processes per implementation measured:

| Probe                               | Median wall time | Median process CPU |  Peak RSS |
| ----------------------------------- | ---------------: | -----------------: | --------: |
| Previous Python parent-marker probe |         26.84 ms |           26.64 ms | 12.96 MiB |
| Native shell parent-marker probe    |         0.945 ms |           0.842 ms |  1.84 MiB |

RSS used five direct `/usr/bin/time` executions per implementation. The larger
inherited-parent peak reported by spawn `wait4` was excluded from that comparison.
These are per-probe costs on x86_64. The live ARM64 Python probe was independently
observed taking roughly 1.84 seconds, but the candidate has not been deployed and
no production CPU reduction is claimed here.

Native tests reject missing, stale, future, dead-parent, extra-field and malformed
markers. Both dash and the production image's BusyBox shell execute the probe;
the packaged wheel contains the executable and no Python probe fallback.

## Reproduce the current workload

Run the optional manual tool from an installed development checkout with Docker:

```sh
uv run --no-sync python scripts/worker_dispatch_benchmark.py \
  --jobs 25000 --batch 100 1000 --repetitions 3 \
  --output reports/worker-dispatch.json
```

The tool creates and removes its own PostgreSQL/Redis containers. It accepts no
database or Redis URL, ignores repository `.env` files, uses a `_test` database and
Redis database 15, and never touches the ordinary Compose stack. It applies the
packaged PostgreSQL migrations and verifies the requested number of unique queue
deliveries after each sample. Reports include architecture, library versions,
source checksum, query/update-batch/commit counts, client Python CPU, and the
process's lifetime peak RSS. That RSS is not a per-job or PostgreSQL memory metric.

This portable runner profiles the current source only and uses the existing
uncapped disposable-service defaults. The table above is the separately captured
before/after comparison with the explicit PostgreSQL limits described earlier;
compare new timings only under equivalent hardware, limits, versions and workload.
There is no hosted performance workflow or automatic production benchmark.
