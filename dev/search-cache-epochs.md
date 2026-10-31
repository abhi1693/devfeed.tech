# Search projection cache epochs

Search-index publication and public database data have separate Redis epochs.
Cached Typesense search results depend on both: a completed projection must not
leave a previously cached empty search behind, and content withdrawal must not
leave old visible search results behind. Article/feed/catalog responses depend
only on the public-data epoch. Lua checks every dependency and loader token in
one atomic publish so an old renderer cannot repopulate an invalidated epoch.
Suggestions and PostgreSQL full-text feed filtering keep their own data semantics;
they do not inherit the Typesense publication epoch.

## Conservative source provenance

Migration `0041` adds `search_events.public_change` with a constant true default.
Every retained event, database-triggered business change and unknown/manual event
therefore retains the old public-data invalidation fallback. This matters for
raw psycopg and direct SQL writers that do not run application commit hooks.

Indexer-created projection/fanout continuation events explicitly carry false.
A completed nonempty batch always invalidates the search-index epoch, and
invalidates the public epoch when any selected event may represent a real data
change. Normal application commits still invalidate public data immediately;
raw SQL source events retain the existing eventual indexer fallback before their
remaining fanout pages drain. Backfill explicitly fences public data once after
enqueue commit and marks its pages projection-only, preserving repair semantics
without flushing unrelated content on every page.

This does not promise to remove every observed indexer invalidation: source
business events remain conservative. It does prevent index maintenance, duplicate
projection pages and fanout continuation from unconditionally expelling unrelated
public data. Direct SQL that bypasses or disables triggers still requires the
explicit backfill/repair fence, as before.

## Rollout

1. Apply the single additive migration `0041` before upgrading the applications.
   Its constant default retains unknown events as public changes without rewriting
   their tuples; a populated 10,000-event test checks unchanged IDs/xmin.
2. Upgrade every public API and search indexer replica. Leave
   `DEVFEED_SEARCH_CACHE_ISOLATION_ENABLED=false` during mixed versions. Updated
   APIs always understand both epochs; the default-off indexer gate continues
   invalidating public data for every completed batch, matching old APIs.
3. Enable the gate only after all public APIs and indexers are compatible.
   Verify cached article/feed reads survive projection-only pages while real
   source/article changes, raw SQL, withdrawal and rejected sources still fence
   old readers. Monitor cache hit rate, API CPU, SQL work, index lag and freshness.
4. Roll back by disabling the gate first. The old invalidation policy resumes;
   keep the additive column until the compatible image rollback is complete.

No TTL, replica count, search delay or production resource limit changes here.
The gate is intentionally off until a compatible rollout is explicitly enabled.

## Validation

Real Redis/PostgreSQL tests cover atomic dependency fences, projection-only pages,
raw-SQL source edits, explicit repair backfills and populated schema round trips.
Twenty projection-only pages produce twenty unrelated content-cache misses and
SQL reads with the gate off; with isolation enabled the same reads stay cached
and perform no SQL. This is a controlled demonstration of avoided work, not an
estimate that all production cache misses or CPU would disappear. Native ARM64
workload measurement and hosted CI must pass before promoting the draft.

## PostgreSQL impact

Local x86-64 PostgreSQL 18, session-mode PgBouncer and Redis; three samples per
isolation mode, alternating order. Each sample includes the producer insertion,
indexer processing/acknowledgement and public API read for 20 projection-only pages.
The baseline is isolation off, which retains the old broad invalidation behavior.
A separate observer role captures PostgreSQL utility/nested statements, driver
configuration, transaction commands and pooler resets. Warm-up is excluded. All
samples use the same hydrated response and preserve the raw-writer/publication fences.

| Metric | Before | After | Measurement basis |
| --- | ---: | ---: | --- |
| Public API cache outcomes | 20 MISS | 20 HIT | 20 projection-only pages per mode, three samples. |
| API data SELECTs / all recorded PG commands | 20 / 360 | 0 / 260 | Entire producer + indexer + API stage; no extra indexer query replaces the saved reads. |
| Rows returned/affected / shared buffer hits | 100 / 720 | 80 / 660 | Includes inserted/acknowledged outbox rows. |
| PG planning + execution elapsed | 28.449 ms | 24.192 ms | Medians; elapsed time is not database CPU. |
| PostgreSQL-container CPU | 152.606 ms | 131.571 ms | Median cgroup delta, includes logging, background work and probes. |
| Read transaction rollbacks / pooler resets | 20 / 60 | 0 / 40 | Producer/indexer retain 40 BEGIN and 40 COMMIT in both modes. |
| Real Redis outage: commands / rows / buffer hits | 360 / 100 / 720 | 360 / 100 / 720 | Owned Redis paused; 20 API bypasses in each mode, three samples. |
| Real Redis outage: PG-container CPU range | 150.297–172.076 ms | 152.390–164.025 ms | Overlapping ranges with identical statement/row/buffer work. |
| Public churn: commands / rows / buffer hits | 360 / 100 / 720 | 360 / 100 / 720 | Public epoch invalidated before every read; 20 misses in both modes, three samples. |
| Public churn: PG-container CPU range | 149.617–163.599 ms | 149.616–166.850 ms | Overlapping ranges; no fallback SQL added by isolation. |
| Total workload WAL | 4,440–4,539 B | 4,440–4,539 B | Observed range in all three scenarios on the new schema, including outbox writes and pruning. |
| Backend startups / remaining locks / idle transactions | 0 / 0 / 0 | 0 / 0 / 0 | Warmed session pool; same in-transaction advisory/table lock footprint sampled during remote sync. |

These gate-off/on results isolate runtime invalidation behavior on the new schema.
They do not hide the additive schema cost below, establish an across-the-board
PostgreSQL CPU reduction under failure, or predict whole-production container savings.
The degraded cases retain equal SQL work; they are not an outage capacity guarantee.

### Migration and ongoing outbox storage

A fresh disposable database at revision 0040 held 103,000 events before the actual
Alembic upgrade. Before/after checks matched every ID/xmin checksum, relation filenode
and the 7,888,896-byte heap. All old rows read `public_change=true`. No heap rewrite
or row-by-row backfill occurred. A deliberately held reader made the requested
AccessExclusiveLock wait and fail at the migration's five-second lock deadline;
a subsequent uncontended upgrade succeeded.

| Metric | Before | After | Measurement basis |
| --- | ---: | ---: | --- |
| Retained event heap / IDs / xmin | 7,888,896 B / 103,000 unchanged | Same | One actual 0040→0041 upgrade; filenode and row checksum identical. |
| Migration commands / WAL | Not applicable | 8 / 2,153 B | Actual Alembic upgrade; ALTER TABLE metadata used 2,020 B and version-row update 133 B. |
| Migration lock | Reader holds AccessShareLock | AccessExclusiveLock requested; blocked attempt timed out at 5.038 s | Deliberate contention in the disposable database; this is not a production lock-duration estimate. |
| INSERT commands / rows | 1 / 1,000 | 1 / 1,000 | Three 1,000-event samples before/after, raw SQL, no new lookup or backfill. |
| INSERT WAL | 172,250 B median | 173,250 B median | Approximately one extra WAL byte per new event; both true and false provenance measured. B-tree/sequence page boundaries also affect individual samples. |
| INSERT planning + execution elapsed | 4.676 ms | 4.709 ms true / 4.743 ms false | Three samples per form; small timing differences are not proof of zero overhead. |
| INSERT PG-container CPU | 24.045 ms | 23.528 ms true / 23.890 ms false | Medians, probe/background-inclusive; no whole-production CPU inference. |

The boolean provenance has a concrete storage/WAL cost, even while the fixed runtime
workload avoids API SQL. Keep the PR in draft and isolation disabled until migration
contention, native ARM64 and a representative production canary have been reviewed.
Do not weaken raw-SQL or source-withdrawal fences to remove this byte. Rollout still
requires every public API and indexer to support both epochs before enabling isolation.
