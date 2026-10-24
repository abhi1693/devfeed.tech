# Job payload retention benchmark

Issue [#298](https://github.com/abhi1693/devfeed.tech/issues/298) concerns retention
candidate selection even when almost nothing can be pruned. The benchmark uses
full migrated PostgreSQL 17 schemas, measuring ordinary ticks separately from
maintenance pages and backlog recovery. The standalone Analysis retention performance workflow runs the
comparison on a native ARM64 runner at 100,000 and 250,000 articles.

Fresh read-only production counts on 24 October 2026 showed approximately
92,394 articles, 156,004 article analysis jobs and 159,446 topic analysis jobs.
There were 51,434 old unpruned article successes and 6,735 old unpruned topic
successes. These are age-eligible inputs, not a claim that every payload can be
pruned. Production was PostgreSQL 17.11 on aarch64. No production plans were
executed with `ANALYZE` and no production data or configuration was changed.

The deterministic fixture has 1.7 jobs per article and per topic subject, plus
2,000 failed runs for one subject in each history. Most subjects have one run,
some have two and 10% have six. It includes failed jobs, queued jobs, running
leases, both topic and proposal subjects, 4 KiB incompressible input snapshots,
and an additional 4 KiB article catalog snapshot. The old backlog is deliberately
larger than production's current age-eligible population. Review, provenance,
verification and concurrent completion contracts have separate integration tests.

The phases are:

- Fifty pages with the shipped defaults: scan at most 1,000 rows and prune at
  most 100 payloads per history per call.
- Continue the same backlog to a completed sweep with an explicitly accelerated
  prune limit of 1,000. Keep the scan bound at 1,000. This proves convergence
  without making the default page measurements depend on accelerated settings.
- Five maintenance calls against old latest-only successes and long failed tails.
- Twenty ordinary scheduler calls before the next page is due, with concurrent
  ingestion, followed by twenty ordinary calls without the writer.
- Five scheduler ticks at the deployed 15-second interval, for 75 seconds with
  concurrent ingestion. Preparation adds 4,000 old duplicate jobs to the existing
  full-size history and resets maintenance due time. The baseline runs five pages;
  the candidate runs three due pages at its 30-second maintenance interval.
  Per-call latency and ingestion p95/p99 are reported together with the differing
  prune counts and CPU/WAL cost.

The writer targets twenty committed article/origin/ingestion-job/analysis-outbox
transactions per second. Measured achieved rate and ingestion latency are in each
report; overloaded runs can achieve less. Compressed stress phases advance the
synthetic retention clock 31 seconds per maintenance page so a multi-hour logical
sweep can finish quickly.
Their write bursts deliberately exceed the deployed cadence; use the separately
paced phase to assess ingestion latency during normal scheduling.
Ordinary ticks use a fixed clock. Each call's wall latency excludes deliberate
writer pacing; phase wall duration includes pacing. Application CPU includes the
writer thread and the read-only wait observer. These are controlled benchmark
results, not production latency or PostgreSQL CPU attribution.

Both implementations use the same four-connection SQLAlchemy test pool. These
measurements isolate maintenance with controlled connection reuse; production
connection establishment, the pooler and network require live validation.

The baseline loads the base commit's real retention module against the migrated
schema and removes only the three new retained-success indexes when comparing
against the legacy implementation. A checkpointed baseline keeps those indexes
and uses its durable sweep state for convergence checks. The candidate
restores those indexes on the empty tables after truncation, before seeding.
This includes their additional write cost in candidate measurements. Fixture seeding
temporarily disables triggers; every measured maintenance and ingestion transaction uses the real
migration triggers and indexes. A dedicated PostgreSQL instance is required for
meaningful instance-wide WAL deltas. Reported WAL includes concurrent ingestion;
the no-writer ordinary phase isolates retention's idle WAL cost. Every measured
phase starts after an explicit `CHECKPOINT`, following its rolled-back plan pass;
fixture preparation is vacuumed and analyzed. This gives both implementations a
matched checkpoint boundary. Counters still report checkpoints triggered during
long measured phases.

Reports retain `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON)` for actual
captured candidate statements, including row counts, buffers and temporary
blocks. Before each timed phase, one real page runs inside a rolled-back outer
transaction. Each captured SELECT is explained immediately before execution,
so plans observe its actual pre-mutation inputs. This plan pass is outside the
timed and WAL measurement. The harness rejects excessive per-call pruning,
ordinary ticks that read job history, ingestion errors, and a completed candidate
sweep that leaves eligible payloads behind. Checkpointed implementations also
run a separate plan pass at 80% of the old unpruned history for backlog and
latest-only phases. Cursor setup, plan execution and payload changes all roll
back together; this verifies late pages without changing timed sweep progress.
The JSON retains individual maintenance and ingestion latency samples and
checkpointer and WAL counters before/after each phase so checkpoint activity can
be compared with latency tails. A 100 ms observer records retention and ingestion
backend wait events and blocking PIDs, with elapsed timestamps and no SQL text.
Forced checkpoint counters are recorded separately. The JSON includes PostgreSQL
version/settings, application
architecture, CPU count, throughput, per-call limits, CPU time and WAL bytes.
The native CI log also records `uname` and `lscpu`. Plans and WAL are captured only
in the disposable instance.

To reproduce, start a dedicated PostgreSQL 17 instance, create a fresh database
whose name ends in `_test`, install the locked workspace packages, and run:

```sh
git show <base-sha>:packages/core/src/job_retention.py > /tmp/base-job-retention.py
export DEVFEED_TEST_DATABASE_URL='postgresql+psycopg://user:password@127.0.0.1:5432/retention_benchmark_test'
uv run --locked --no-sync --no-build python scripts/job_retention_benchmark.py \
  --articles 100000 --baseline-file /tmp/base-job-retention.py \
  --output /tmp/retention-100000.json
```

Repeat with `--articles 250000`. Keep the default
`--cadence-interval-seconds 15` for performance evidence. The smoke regression
uses `0.05` seconds while preserving 15-second logical scheduler ticks to test
state transitions quickly. The script upgrades and truncates the disposable
database, including all application tables; give it a dedicated instance and
superuser role for fixture preparation. Keep raw reports untracked or download
the workflow's `retention-arm64-postgresql17` artifact. Compare a fresh production
statistics window only after deployment under matched workload, checking page
logs, queue freshness, query latency, WAL, restarts and interrupted sweep recovery.
