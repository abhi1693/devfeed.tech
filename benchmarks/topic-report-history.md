# Topic report history profiling

Issue [#299](https://github.com/abhi1693/devfeed.tech/issues/299) concerns repeatedly
expanding large decision state JSON and sorting repeated stages in administrative
reports. The disposable profiler compares the complete previous and current
decision metrics and inference chart responses on the same fixture. It alternates
strategy order and requires exact JSON equality for 1-, 7-, 30- and 90-day reports.

`topic_decision_calls` stores one typed fact for each original `(proposal_id,
ordinal)`, including its lifetime occurrence number within its stage. Source run
JSON remains authoritative. A transactional trigger updates only changed facts and
deletes removed tail entries; unrelated state updates skip projection work. Each
write handles that run's calls, with no global rollup or separate refresh scheduler.
Report time filters use the fact timestamp index, while completed proposal usage
joins all lifetime facts for proposals reviewed in the requested window.

Migration `0035` installs the projection, backfills existing runs and creates the
indexes atomically. Its bounded lock admission waits at most 30 seconds before a
retryable failure. Once admitted, backfill/index creation pauses proposal and run
writes while allowing ordinary reads. The current application requires schema
`0035`; rollback application code together with the schema. Downgrade removes the
derived facts while preserving source JSON, allowing a later upgrade to rebuild it.

The production reference was collected read-only on 24 October 2026 at 09:29 UTC:
92,581 articles, 6,011 proposals, 5,000 decision runs and 14,373 calls. PostgreSQL
17.11 ran natively on ARM64 with `work_mem=4MB`. A bounded 100-run sample had mean
state JSON size 20,511 bytes, median 22,131 and p95 35,444 bytes. These cardinalities
describe that observation, rather than a continuously refreshed production count.

`scripts/profile_topic_reports.py` uses migrated, empty, explicitly local
PostgreSQL 17 databases ending in `_test`. It refuses libpq routing overrides and
never reads the checkout's `.env` or truncates fixture tables. The benchmark uses
ordinary article, inference and decision triggers during seeding. Each run has
approximately 20 KiB of incompressible unrelated evidence, plus one to five ordered
calls with repeated draft and verification stages. The first 6,000 runs cover the
recent 90 days; further runs add older unrelated history. Proposal review dates
vary independently, exercising whole-workflow accounting beyond the report window.

The ARM64 workflow runs 100,000 and 300,000 article fixtures, with respectively
6,000 and 18,000 decision runs and 500,000 and 1,500,000 inference ledger rows.
It saves complete `EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)` plans, report wall
time, application process CPU, shared buffers and temporary blocks as artifacts.
Four concurrent reporting workers run alongside 200 article insert and decision
correction transactions, rotating through one- to five-call workflows. Both
strategies execute the same operations, paced identically using the measured legacy
runtime to keep ingestion active throughout reporting. The artifact records overlap
for every report; transaction latency excludes pacing. These writes
roll back so report inputs remain identical; ingestion latency measures transaction
work without durable commit/fsync cost. Concurrent process CPU is reported once for
the entire process; summing per-report CPU under concurrency would double count.

The legacy concurrency phase briefly disables only the new decision projection
trigger on this dedicated fixture database, restoring it in `finally`. Committing
that short trigger-state change prevents an open `ALTER TABLE` transaction from
holding an exclusive lock during reporting. No workload writes commit. A separate
rolled-back correction captures before/after WAL and trigger execution, and a full
equivalent backfill measures initial extraction and index insertion separately from
steady-state reporting. An equivalent backfill excludes migration DDL/lock wait.

To run locally, create and migrate a new disposable database and make a clean
checkout of the pre-change revision. Set `DEVFEED_DATABASE_URL` to that database
for migrations, and export the same URL as `DEVFEED_TEST_DATABASE_URL` for profiling:

```sh
uv run --locked --no-sync alembic upgrade head
uv run --locked --no-sync python -m scripts.profile_topic_reports \
  --baseline-root /path/to/baseline --seed --articles 100000 \
  --output /path/outside/repository/topic-reports.json
```

The path-filtered `Topic report history profile` pull-request workflow uses a native
`ubuntu-24.04-arm` runner and an immutable PostgreSQL 17.11 image. It has read-only
repository permissions and no deployment or production credentials. Local AMD64
smoke measurements validate the harness; they do not establish native ARM64 speed.
When the pull-request base still uses historical JSON expansion, the workflow
requires lower concurrent report and topic SQL runtime, zero topic SQL temporary
writes, and complete overlap between reports and ingestion. Later changes compare
against their current base and retain exact output parity without requiring every
change to make the already normalized queries faster again. Full phase
elapsed time includes identical writer pacing and is not a report latency metric.

After deployment, compare fresh `pg_stat_statements` deltas under matched report
windows and concurrency: calls, total execution time, temporary blocks per call and
buffer reads. Also check report freshness, worker latency and write amplification,
then verify a retried correction and deletion converge immediately without duplicate
facts. These deployment checks require a separately authorized release; the
benchmark workflow only accesses its disposable database.
