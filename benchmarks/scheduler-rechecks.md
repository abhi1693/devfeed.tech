# Historical PostgreSQL scheduler clock maintenance

**Superseded by the ClickHouse reporting architecture.** The PostgreSQL scheduler
profiler and its performance workflow have been retired. These notes describe the
earlier implementation and measurements. Use the
[native analytics guide](../apps/admin-api/ANALYTICS.md#disposable-profiling) for
current reporting profiles.

Issue [#294](https://github.com/abhi1693/devfeed.tech/issues/294) concerns recurring
article automation checks that change only `automation_next_check_at`. Previously
the article capture trigger enqueued a complete pipeline-card rebuild for every
clock update, including unchanged queued or running analysis. Those rebuilds
loaded retained analysis evidence and ranked the taxonomy again.

Migration 0035 separates these paths. Semantic article changes still enqueue the
normal transactional event. A clock update patches only `payload.next_check_at`
for the serving and building projections, using their primary keys. Unchanged
clock assignments do no projection work. No timer events accumulate behind a
paused publisher. Missing cards are produced by ordinary semantic events or
backfill. Completed cards keep their null next-check field.

The publisher reads current article clocks under ordered share locks immediately
before writing its bounded card batch. Acquisition is nonblocking: a busy article
defers the entire batch with its events intact, avoiding lock cycles with bulk
ingestion that locks articles in canonical URL order. A check committed during
detached card evaluation is read before publication; a later check patches the
published card. Job retry deadlines
in `next_transition_at` retain their separate event-driven behavior.

Automation also reads active analysis execution metadata without the retained
input, catalog, result and usage documents. Unchanged prerequisite reconciliation
reuses its facts. Missing prerequisites and changed image-storage requirements
still pass through the durable job request and recovery paths.

## Earlier comparison method

The retired `Scheduler recheck scale` workflow ran on native `ubuntu-24.04-arm` with a
pinned PostgreSQL 17.11 image. It ran the same measurement harness against the PR
base and candidate, at 100,000 and 150,000 articles. Each application uses its own
locked environment. The base seeds the disposable historical corpus; the candidate
reuses that corpus after removing only the harness's measured ingested articles
and resetting projections. Both warm the measured cards before timing. Publisher
batches refresh setup-time clocks so a large fixture cannot acquire an already-expired
lease; measured ticks start from completed warmup. The base always runs first;
corpus identity and article count are checked before reuse.

The reports included SQL, buffer, temporary-write and WAL counters as well as
clock-only events and card hydration, with signed before/after deltas. Failed,
incomplete or mismatched measurements were labeled unavailable rather than
reported as a speedup.

The retired profiler used explicitly local `DEVFEED_TEST_DATABASE_URL` and
`DEVFEED_TEST_REDIS_URL`, a database ending in `_test`, and Redis database 15.
The disposable PostgreSQL instance enabled `shared_preload_libraries=pg_stat_statements`,
`pg_stat_statements.track=all` and `track_io_timing=on`. Its profiling role could
create the extension and run `CHECKPOINT`. Fixture seeding replaced only that
dedicated database's contents; the base and candidate reused an identified corpus
with the same article count.

The fixture includes historical jobs, reviews, taxonomy links and 8 KiB retained
evidence. Unrelated saved cards are seeded to establish realistic table/index
cardinality after normal seven-day retention; the measured cohort and newly
ingested articles use real card derivation. Expired published placeholders are
excluded before warmup, and measured retention deletes fail the workload gate.
Five repeated batches of 50 queued/running checks execute alongside
ten real feed-entry writes per batch. The harness advances the clock between
batches, preserves job identities and retry/lease metadata, and drains the actual
incremental publisher.

Artifacts contain application runtime/CPU, SQL counts, event and hydration counts,
publisher upsert counts (excluding trigger clock patches), top-level PostgreSQL
statement-statistic deltas, and rollback-only
`EXPLAIN (ANALYZE, BUFFERS, WAL, FORMAT JSON)` plans. A checkpoint precedes each
timed workload, and WAL record/full-page-image counters are recorded separately.
Due-query plans restore the checked cohort's due clocks in a rollback-only
transaction so their execution includes due rows. Candidate assertions enforce
the batch limit, zero clock-only events/full-card hydration, visible current
clocks, unchanged active execution state, and publication of genuine ingestion
events. Functional integration tests separately cover completion races, policy
rebuilds, retry deadlines and transaction rollback.

A bounded read-only production sample on 24 October 2026 at 09:27–09:28 UTC found
92,581 articles, 156,004 analysis jobs, 104,577 enrichment jobs, 255,716 reviews,
95,307 origins, 1,406 sources, 4,450 topics, 49,165 tags, 352,280 article-topic links
and 930,387 article-tag links. There were 7,302 queued and four running analysis
jobs. Production PostgreSQL was 17.11 on ARM64. Benchmark qualification records
this dated comparison rather than treating a changing production count as fixed.

These are synthetic warmed workload measurements, not production p95 or total
scheduler CPU forecasts. Other scheduler services, provider/network calls,
production contention and proxy latency are outside this workload. Small clock
and JSON writes remain; the fix removes full evidence hydration and event-driven
rebuild amplification.

## Deployment and rollback

Pause the previous pipeline publisher, upgrade the database to 0035, and then
start the candidate publisher. The previous publisher lacks clock synchronization
and must not publish detached cards after the trigger changes. The migration changes
only article trigger definitions and adds a trigger function; it does not rewrite
historical articles or backfill card evidence. Existing events remain valid and
drain normally. DDL briefly acquires a table lock, so use the normal migration
window and monitor long-running transactions.

Rolling back to 0034 removes the clock patch trigger and restores full article
invalidation on clock writes. Retain the candidate publisher until the database
downgrade finishes, then roll back the application. No article/job history is
discarded. Coordinate the revision number with any independently developed
migration before merging.

After deployment, compare fresh statement-statistic deltas under matched queue
size and ingestion rate, without resetting production statistics. Check scheduler
duration, pipeline-publisher CPU, event lag/volume, analysis snapshot hydration,
projection writes, current next-check visibility, overdue retry processing and
worker recovery. Deployment verification remains a separate gate from these
disposable-database results.
