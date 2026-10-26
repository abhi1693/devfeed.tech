# Historical PostgreSQL topic report profiling

**Superseded by the ClickHouse reporting architecture.**

Issue [#299](https://github.com/abhi1693/devfeed.tech/issues/299) concerns repeatedly
expanding large decision state JSON and sorting repeated stages in administrative
reports. The disposable profiler compares the complete previous and current
decision metrics and inference chart responses on the same fixture. It alternates
strategy order and requires exact JSON equality for 1-, 7-, 30- and 90-day reports.

`topic_decision_calls` stores one typed fact for each original `(proposal_id,
ordinal)`, including its lifetime occurrence number within its stage. Source run
JSON remains authoritative. A transactional trigger filters unchanged facts before
the upsert, avoiding conflict locks on unaffected calls, and deletes removed tail
entries; unrelated state updates skip projection work. Each
write handles that run's calls, with no global rollup or separate refresh scheduler.
Report time filters use the fact timestamp index, while completed proposal usage
joins all lifetime facts for proposals reviewed in the requested window.

Migration `0036` follows the scheduler clock migration `0035`, installs the topic
projection, backfills existing runs and creates the indexes atomically. Its bounded
lock admission waits at most 30 seconds before a retryable failure. Once admitted,
backfill/index creation pauses proposal and run writes while allowing ordinary reads.
The current application requires schema `0036`; rollback application code together
with the schema. Downgrading to `0035` removes the derived topic facts while preserving
source JSON and the scheduler clock changes, allowing a later upgrade to rebuild the facts.
Downgrade briefly takes exclusive table locks, waiting for active readers and
writers before removing the trigger and projection; schedule it during a quiet
period.

The production reference was collected read-only on 24 October 2026 at 09:29 UTC:
92,581 articles, 6,011 proposals, 5,000 decision runs and 14,373 calls. PostgreSQL
17.11 ran natively on ARM64 with `work_mem=4MB`. A bounded 100-run sample had mean
state JSON size 20,511 bytes, median 22,131 and p95 35,444 bytes. These cardinalities
describe that observation, rather than a continuously refreshed production count.

`scripts/profile_topic_reports.py` uses migrated, empty, explicitly local
PostgreSQL 17+ databases ending in `_test`. It refuses libpq routing overrides and
never reads the checkout's `.env` or truncates fixture tables. The benchmark uses
ordinary article, inference and decision triggers during seeding. Each run has
approximately 20 KiB of incompressible unrelated evidence, plus one to five ordered
calls with repeated draft and verification stages spaced six hours apart and offset
by one hour from exact integer-day boundaries. Repeated stages span UTC dates and
the 30- and 90-day report boundaries. The first 6,000 runs cover the
recent 90 days; further runs add older unrelated history. Proposal review dates
vary independently, exercising whole-workflow accounting beyond the report window.

The ARM64 workflow runs 100,000 and 300,000 article fixtures, with respectively
6,000 and 18,000 decision runs and 500,000 and 1,500,000 inference ledger rows.
It saves complete `EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)` plans, report wall
time, application process CPU, shared buffers and temporary blocks as artifacts.
Four concurrent workers run 90-day report pairs alongside 200 article insert and decision
correction transactions, rotating through one- to five-call workflows. Both
strategies execute the same operations, paced identically using the measured legacy
runtime to keep the writer phase active throughout reporting. The artifact records
`writer_phase_overlap` for every report. That phase includes SQL, rollback and pacing
sleeps; the event does not prove that every report overlaps an actual writer SQL
statement. Transaction latency excludes pacing. These writes
roll back so report inputs remain identical; ingestion latency measures transaction
work without durable commit/fsync cost. Concurrent process CPU is reported once for
the entire process; summing per-report CPU under concurrency would double count.

The legacy concurrency phase briefly disables only the new decision projection
trigger on this dedicated fixture database, restoring it in `finally`. Committing
that short trigger-state change prevents an open `ALTER TABLE` transaction from
holding an exclusive lock during reporting. No workload writes commit. A separate
rolled-back five-call correction captures before/after WAL and trigger execution after a
warm-up sample avoids charging only the first strategy for checkpoint-dependent
full-page images. The root `EXPLAIN` WAL excludes AFTER-trigger work, so
`statement_wal_bytes` also records the server-wide LSN delta around the statement,
including trigger WAL. This dedicated-server measurement can include background
WAL and excludes the later transaction rollback. A full
equivalent backfill measures initial extraction and index insertion separately from
steady-state reporting. An equivalent backfill excludes migration DDL/lock wait.

Separate committed-write comparisons run after the read diagnostics. Fresh cohorts
start with four or nineteen calls, then append a fifth or twentieth call, settle it,
correct an earlier call and save an unrelated checkpoint. These are four separate
transactions per workflow; the append also inserts an article. Latency ends after
COMMIT. Five calls cover the configured default; twenty cover the maximum initial
budget plus both operator grants. Each strategy uses the same logical source states
and 20 KiB of unrelated evidence. Repeated pairs alternate strategy order. Final
source-state hashes must match, and all current facts must match their source before
cleanup. Setup, trigger changes, verification and cleanup are outside timing.

Committed samples report per-operation median and overall p95, transaction counts,
application thread CPU and WAL snapshots. They measure these lifecycle transactions,
not the full worker pipeline or maximum ingestion capacity. Each phase uses fresh
rows on the same dedicated database; cleanup does not restore identical heap/index
bloat. Server-wide WAL counters include background work and can contain unequal
full-page images. Retain those counts rather than interpreting one ratio as stable
write amplification. No universal acceptable writer-latency budget has been defined.

The PostgreSQL-only profiler and its workflow were retired when historical reports
moved to ClickHouse. These notes describe the earlier measurement methodology and
production reference; they do not describe the current reporting implementation or
establish ClickHouse performance. Use `scripts/profile_analytics_reports.py` against
a prepared disposable ClickHouse fixture for current report measurements. See
[the native analytics guide](../apps/admin-api/ANALYTICS.md).
