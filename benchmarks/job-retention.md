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

The writer targets twenty committed article/origin/ingestion-job/analysis-outbox
transactions per second. Measured achieved rate and ingestion latency are in each
report; overloaded runs can achieve less. The synthetic retention clock advances
31 seconds per maintenance page so a multi-hour logical sweep can finish quickly.
Ordinary ticks use a fixed clock. Each call's wall latency excludes deliberate
writer pacing; phase wall duration includes pacing. Application CPU includes the
writer thread. These are controlled benchmark results, not production latency or
PostgreSQL CPU attribution.

The baseline loads the base commit's real retention module against the migrated
schema and removes only the three new retained-success indexes when comparing
against the legacy implementation. A checkpointed baseline keeps those indexes
and uses its durable sweep state for convergence checks. The candidate
restores those indexes before its seeded run. This includes their additional
write cost in candidate measurements. Fixture seeding temporarily disables
triggers; every measured maintenance and ingestion transaction uses the real
migration triggers and indexes. A dedicated PostgreSQL instance is required for
meaningful instance-wide WAL deltas. Reported WAL includes concurrent ingestion;
the no-writer ordinary phase isolates retention's idle WAL cost.

Reports retain `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON)` for actual
captured candidate statements, including row counts, buffers and temporary
blocks. Before each timed phase, one real page runs inside a rolled-back outer
transaction. Each captured SELECT is explained immediately before execution,
so plans observe its actual pre-mutation inputs. This plan pass is outside the
timed and WAL measurement. The harness rejects excessive per-call pruning,
ordinary ticks that read job history, ingestion errors, and a completed candidate
sweep that leaves eligible payloads behind. The JSON includes PostgreSQL version/settings, application
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

Repeat with `--articles 250000`. The script upgrades and truncates the disposable
database, including all application tables; give it a dedicated instance and
superuser role for fixture preparation. Keep raw reports untracked or download
the workflow's `retention-arm64-postgresql17` artifact. Compare a fresh production
statistics window only after deployment under matched workload, checking page
logs, queue freshness, query latency, WAL, restarts and interrupted sweep recovery.
