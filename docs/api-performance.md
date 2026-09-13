# API profiling

Run `bash scripts/profile-api.sh` from the repository. It starts its own disposable
PostgreSQL and Redis containers, migrates and seeds a test database, profiles actual
FastAPI handlers through response serialization, writes `reports/api-profile.json`
and `reports/api-profile.tables.json`,
and removes the containers even on failure. It does not use the development database.

```sh
# Defaults: 1,000 rows per entity family, 10 measured repetitions, 8 concurrent users.
bash scripts/profile-api.sh

# Compare a smaller dataset; save both reports.
DEVFEED_PROFILE_ROWS=100 bash scripts/profile-api.sh reports/api-profile-small.json

# Only the complete table/filter matrix, with three measured requests per case.
DEVFEED_PROFILE_SUITE=tables DEVFEED_PROFILE_REPEATS=3 bash scripts/profile-api.sh
```

`DEVFEED_PROFILE_ROWS` accepts 100–10,000, `DEVFEED_PROFILE_REPEATS` accepts 1–100,
and `DEVFEED_PROFILE_CONCURRENCY` accepts 1–16. Large datasets consume substantial
space because each analysis run contains a 32 KiB input snapshot, and article runs
also contain a 32 KiB catalog snapshot. Reports contain synthetic data only and are
ignored by Git.

`DEVFEED_PROFILE_SUITE` selects `all` (default), `core`, or `tables`. The core workload
below covers general endpoint behavior; the table workload additionally checks every
collection endpoint and its query parameters against the OpenAPI inventory.

The workload covers 49 read request shapes: public feed/search/topic filters,
article details, sources/tags/topics; admin article lists/details/content/reviews/
publication decisions, taxonomy lists/proposals/relationships, analysis job lists
and filters, graph search/traversal/path, and overview metrics. Collection pages
compare 1 versus 100 results, with additional offset and 500-source cases. Articles
have distinct sources, tags, and topics so the ORM identity map cannot hide an N+1.
Three historical analysis runs per subject exercise latest-run and retry queries.
Article analysis results also contain 32 KiB of incompressible evidence so profiling
exercises PostgreSQL's out-of-line JSON storage, not only large input snapshots.

Each report records SQL round trips, HTTP response size, warm p50/p95 latency,
SQL execute time, and PostgreSQL `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans.
The concurrent workload mixes overview, article, job and proposal reads. A populated
public feed cache-hit check must perform zero SQL queries. Normal integration CI
runs the same query budgets with 120 rows per family and one repetition, plus
correctness tests; it does not assert machine-dependent latency.

## Results on September 11, 2026

The initial live database contained no articles or sources, so it could not exercise
article performance. It contained 57 topics, 1,265 proposals and 1,965 research runs;
the connection snapshot showed 16 idle connections and the active inspection query.
Performance testing therefore used synthetic data in isolated services.

The comparison below uses 2,000 articles, 1,000 distinct sources/topics/tags/proposals,
and 6,000 analysis runs. Cache was disabled. Baseline used five measured requests
per shape; final used ten. Both include a warmup request. These are local TestClient
measurements, not production network latency targets.

| Request | Queries before → after | Median before → after |
| --- | --- | --- |
| Overview | 27 → 16 | 33,736 → 249 ms |
| Combined AI jobs, 100 rows | 4 → 4 | 151 → 37 ms |
| Failed AI jobs, 100 rows | 3 → 3 | 175 → 33 ms |
| Article analysis jobs, 100 rows | 3 → 3 | 63 → 22 ms |
| Topic analysis jobs, 100 rows | 3 → 3 | 46 → 25 ms |
| Topic proposals, 100 rows | 3 → 3 | 50 → 22 ms |
| Article content | 5 → 2 | 15 → 7 ms |
| Article review history | 6 → 3 | 19 → 10 ms |
| Public feed, 100 rows | 4 → 4 | 41 → 38 ms |

The overview's final p95 was 266 ms. The eight-user mixed burst had no errors and
a final p95 of 572 ms (40 requests), versus 34,221 ms before (20 requests).
The populated feed cache hit performed zero SQL queries and took 4 ms.

Query counts stayed bounded across page sizes and the 10× dataset increase from
200 to 2,000 articles. One expected difference: an empty proposal page uses two
queries, while a populated page also loads latest analyses in one batch. Feed
remained four queries and admin article lists five. The overview median grew from
62 to 249 ms, and combined AI jobs from 30 to 37 ms. Exact overview counts still
depend on dataset size.

## Changes supported by the profiles

- **Overview blockers:** previously, five blocker groups each counted and selected
  examples separately, repeatedly stripping every nonletter from large article
  texts and looking up analysis history. A materialized CTE now evaluates the
  predicates once per pending article. Window counts and ranks return exact totals
  and at most five examples per group in one query. Groups can still overlap.
  A boolean regex preserves PostgreSQL's alpha class and 40-letter threshold
  without constructing the entire letter-only string. Publication totals,
  autonomous counts and median delay share one aggregate query.
- **Combined AI jobs:** default count/page queries no longer evaluate correlated
  retry-history predicates for all historical jobs. Eligibility is evaluated for
  the returned IDs, unless filters or status sorting require it before pagination.
  The original plan triggered roughly 34 ms of PostgreSQL JIT work per count/page
  query; final default plans avoided JIT, with count/page execution around 6/7 ms.
  Failed/retried filtering still uses the same eligibility rules.
- **Job and proposal lists:** ORM reads load the fields actually presented and
  exclude private input/catalog snapshots. `raiseload` rejects accidental deferred
  column access rather than silently introducing per-row queries. Latest-analysis
  filter subqueries also project only fields needed for filtering.
- **Article subpages:** content, reviews and publication decisions check parent
  existence with an ID-only query, preserving 404 behavior while avoiding the
  article's three relationship loads.

Regression coverage checks query budgets through serialization, omitted snapshot
columns, exact recovery targets, empty/missing content, Unicode/long-text threshold
equivalence, overlapping blockers, superseded failures, and retry-status filtering,
ordering and pagination. No response contract or schema/index change was needed.
Final validation passed all 1,822 backend tests, Ruff lint/format, and mypy for
134 source files; the generated admin OpenAPI contract was unchanged.

The running admin container's overview module matched the source hash after the
existing Compose watch rebuilt it. One read-only live overview calculation took
464 ms and 16 queries with 61 topics and no articles. All Compose services were
healthy at that check. This live observation is separate from the synthetic HTTP
benchmark and is not a production load test.

## Interpretation and remaining limits

No row-by-row N+1 was observed in the populated read paths tested. That does not
guarantee every filter combination, future change, or larger production dataset.
SQL execution timings exclude ORM hydration and response serialization; request
timings include those but exclude TCP/TLS, the Next.js gateway and real OIDC-provider
verification. Some p95 samples show local process/host variability even where the
SQL plans are inexpensive. Ten repetitions characterize this sample, not a latency SLA.

Write endpoints and external Codex, Chimely, source fetching and import workflows
are covered by functional tests, not this read-load benchmark. The existing worker
snapshot regression separately checks batched SQL for 1 versus 20 busy workers.
This workload is a short concurrent burst, not sustained saturation testing.

Exact totals, substring search and deep offset pagination still require increasing
database work at scale. The default pool remains five connections plus five overflow
per process; the profiles do not justify raising it. Reprofile representative retained
job history and article volumes before adding indexes, changing pools, caching admin
aggregates, or replacing offsets with cursors. Such changes have write/storage or
freshness/contract costs and should be justified by their query plans.

## Complete table and filter audit

The follow-up covers **28 collection APIs and 579 request cases**, plus six supporting
calls for filter options, ingestion status, and worker telemetry. It runs without
filters, with every supported filter individually (including all declared enum
values), representative combined/pair filters, positive and no-match searches,
literal wildcard searches, every supported sort direction, and offset/cursor pages.
This is not the Cartesian product of every possible filter value.

Both 100- and 1,000-row-per-family runs passed. The larger fixture contains 2,000
articles, 1,000 sources/topics/tags, 1,200 topic proposals, 1,000 relationship
proposals, and 17,000 jobs across all seven exposed job types. Histories have enough
rows on a single parent to test full pages. Three measured requests follow a warmup
per table case; SQL plans are collected outside those request timings.

| Collection | Cases | SQL queries per request |
| --- | ---: | ---: |
| Admin articles | 33 | 2–5 |
| Admin sources / topics / tags | 25 / 21 / 16 | 2 |
| Topic proposals | 46 | 2–3 |
| Topic relations / combined relationships / relationship proposals | 14 / 19 / 25 | 2 / 2–4 / 2–3 |
| Replacement choices | 20 | 2 |
| Each of seven job types | 29 each | 2–3 |
| Combined AI jobs | 35 | 2–4 |
| Legacy ingestion / enrichment jobs | 13 each | 1 |
| Article reviews / source reviews / decisions / policy history | 10 each | 3 |
| Public feed | 31 | 1–4 |
| Public sources / topics / tags / topic relations | 12 / 6 / 6 / 1 | 1 / 1 / 1 / 2 |

Empty article/feed pages skip relationship hydration. Mixed AI pages load at most
two job types. Neither behavior is a per-row N+1. Busy worker snapshots use three
queries for both one and twenty distinct workers; idle snapshots use one. Ingestion
status uses six fixed aggregate queries, around 13 ms in the larger sample.

The audit found and fixed behavior as well as SQL costs:

- Four history endpoints exposed `q` but ignored it. Article/source review history
  now searches action/decision, actor and note; publication decisions search their
  decision JSON; policy history searches mode and actor. Parent checks read IDs only.
- Replacement choices ignored `sort`. They now honor name, slug, kind and status
  in either direction, and reject unsupported sort fields with 422.
- Empty job and combined-relationship pages issued unnecessary follow-up reads.
  They now stop after count and page queries. Approved-only relationship pages
  also skip looking up proposals.
- Broad job status ordering and retried-history views repeatedly inspected sibling
  runs. They now derive retry eligibility in a batch using window functions.
  Selective unresolved-failure filters retain indexed lookups. Supplying both
  `status=failed` and `retryable_only=true` applies eligibility once; contradictory
  combinations correctly return an empty page.

On the 1,000-row fixture, retried AI jobs improved from **151 to 73 ms** median,
status-sorted AI jobs from **115 to 64 ms**, and failed/retryable article analysis
jobs from **96 to 29 ms**. No table request exceeded five SQL queries. The slowest
median was about **100 ms** for broad AI-job text search; substring search still
scans data. Individual tail samples were higher, so these remain local measurements,
not production latency guarantees.

The matrix checks returned rows against filter semantics, stable response ordering,
page overlap, empty searches and retry eligibility. Batch eligibility is compared
with the existing single-job predicate for every exposed job model; separate cases
cover an older active run and equal timestamps resolved by UUID. Adding a collection
route or filter parameter without matrix coverage fails the integration test.

UI tables containing static notification preferences or already-returned import and
deletion previews have no separate paginated/filterable collection call. External
inbox/provider APIs and write/preview workflows are outside this database read-load
matrix and retain their existing functional tests.

Final validation: **1,825 backend tests passed** against disposable PostgreSQL and
Redis, with Ruff lint/format and mypy passing. The admin OpenAPI contract is
unchanged. A separate read-only check against the running development database
returned five status-sorted AI jobs in 142.5 ms using three SQL queries; this was a
service-function measurement, not an HTTP load test.


## Public and personalized discovery scalability follow-up

The September 11 follow-up adds `tests/test_discovery_query_budgets.py` for the
new user endpoints. It profiles 1,000 and 100,000 articles, with equally many likes
and opens, 201 topics/sources, and users following one rare topic, 100 topics, or
no topics. The rare topic/source has twelve old articles; eleven are published.
Only those articles have activity in the seven-day trending window. This catches
plans that look cheap on a full first page but scan the catalogue for a small page.

```sh
DEVFEED_PROFILE_SUITE=discovery DEVFEED_DISCOVERY_PROFILE_ROWS=100000 \
  DEVFEED_PROFILE_REPEATS=5 bash scripts/profile-api.sh reports/discovery.json
```

`DEVFEED_DISCOVERY_PROFILE_ROWS` accepts 300–100,000 (default 300 in ordinary CI).
These fixtures omit large research snapshots, so their size setting is separate
from the core/admin profile's `DEVFEED_PROFILE_ROWS`. All data and activity writes
are isolated in disposable services. HTTP timings include handler execution and
serialization through TestClient; they exclude network/TLS and real OIDC/Redis
session verification. Public response caching is disabled for the scale workload.

### Measured changes

At 100,000 articles, the baseline used three repetitions after warmup and the final
profile used five. Rounded medians below are local samples, not service guarantees.

| Request | Baseline median | Final median | Final SQL calls |
| --- | ---: | ---: | ---: |
| One article from a rare topic | 348 ms | 25 ms | 5 |
| One article in a sparse personalized feed | 331 ms | 32 ms | 5 |
| Personalized feed with no followed topics | 305 ms | 10 ms | 1 |
| Trending, up to 100 results | 65 ms | 27 ms | 4 |
| Public feed, 100 results | 74 ms | 40 ms | 4 |

The query plans provide stronger evidence than timing alone: the old sparse feed
visited approximately 90,000 articles. Final plans fetch twelve candidate IDs via
existing topic membership and article primary-key indexes. The main personalized
feed query took about 0.2 ms in EXPLAIN versus about 341 ms before. Trending starts
from the two indexed recent-activity ranges (24 rows here), aggregates scores, and
fetches only matching articles; its main query also took about 0.2 ms. The scale
profiler checks article rows visited as well as SQL round trips for these sparse
paths, without asserting machine-dependent latency.

- Public and user feed reads share an explicit projection with `raiseload=True`.
  Private classification evidence, editorial fields, source polling/history and
  unused topic/tag fields are excluded from ORM hydration. Public response fields,
  publication/source approval gates and relationship serialization are preserved.
- Topic feeds first resolve the active topic ID. Personalized feeds first resolve
  the user's bounded followed-topic IDs and immediately return an empty feed when
  none are active. The extra fixed lookup lets the planner use concrete values;
  these feeds use at most five queries, not one query per result or followed topic.
  Invalid cursors are still rejected before empty-result shortcuts.
- Migration `0001` increases the statistics target to 1,000
  for topic/source membership columns. PostgreSQL analyzes these as data arrives. The default
  most-common-value list omitted the rare topic and estimated hundreds of matches,
  leading to a bad ordered-feed scan. The change creates no new index or application
  data and is reversible. It increases ANALYZE sampling/catalog statistics costs on
  those two columns. See [PostgreSQL's planner-statistics documentation](https://www.postgresql.org/docs/current/planner-stats.html).
- Trending combines recent opens and likes before joining articles, preserving the
  three-to-one like weighting, seven-day cutoff, visibility and tie ordering.
- Article card links no longer prefetch complete previews. A live homepage trace
  showed twelve preview requests without a click; both the current local production
  build and the refreshed live homepage perform zero such requests. Opening the
  routed modal on click was verified against the disposable user environment.
  Other navigation links retain their existing prefetch behavior.

### Remaining limits and rollout

The broad 100-topic feed's main SQL plan stayed inexpensive (about 0.3 ms), but its
HTTP median increased from 18 to 30 ms for one result and 52 to 73 ms for 100 results
in these samples. The eight-worker mixed burst p95 was 343 ms versus 260 ms in the
baseline, with no request errors. Different repetition counts and shared host load
limit that comparison; this is evidence of reduced scan work, not proof of increased
system throughput. The 1,000-row final run had a similar mixed p95 (343 ms).

Topic-directory visibility checks still inspect membership, exact admin overview
counts still grow with retained data, and all-time like counts grow with an article's
likes. Deep offset lists, high-cardinality text searches, high-volume seven-day
activity and sustained write contention require representative production profiling.
No replica router, larger connection pool or shared cache for personal data was added.

The existing core/admin profile passed again at 1,000 entities per family; overview
median was 236 ms and other profiled core reads were below 50 ms in that run. A live
read-only snapshot showed nine idle database connections and the inspection query,
with no blocked query in that snapshot; this is not a saturation test.

After validation on disposable databases, Compose watch picked up the new code
and the running APIs required the new schema revision. The normal Alembic upgrade
was then applied through the existing API container to the local development
database. Public and user readiness endpoints returned 200 afterward. No Docker
image build was triggered by this review. Reports and plan evidence:
`/tmp/devfeed-api-baseline.json`, `/tmp/devfeed-discovery-before-expanded.json`,
`/tmp/devfeed-discovery-stats-large.json`, `/tmp/devfeed-discovery-final-small.json`.


Validation: the full backend run passed 2,007 tests and exposed nine outdated mock/
filter-audit cases. Those were corrected; the targeted rerun passed all 59 tests
covering those files, including real PostgreSQL publication checks and the table
matrix. Web tests passed all 61 cases; production build, ESLint/TypeScript, Ruff
and mypy (150 source files) passed. The preview/follow UI passed eight responsive
Playwright states and four accessibility scans, plus hosted mock sign-in,
follow persistence after reload and unfollow. New logs and reports remain in `/tmp`.

## Admin availability fix on September 13, 2026

Production dashboard requests exceeded the admin frontend's ten-second timeout.
Each API replica had one database connection, no overflow, and a two-second pool
acquisition timeout; concurrent settings and readiness requests therefore also
failed while a dashboard calculation held the connection.

`EXPLAIN (ANALYZE, BUFFERS)` located the dominant work in the pending-article
blockers query. Its lateral subquery extracted publication policy from each
historical analysis result before sorting and limiting to the newest job. Large
JSON results caused repeated out-of-line reads. The subquery now carries the raw
result through `LIMIT 1` and extracts policy afterward. Latest-run ordering,
Unicode text thresholds, overlapping blockers, counts and recovery targets remain
unchanged. This dashboard query change needs no index or schema migration; the
scheduler follow-up below requires revision `0005`.

A read-only comparison used one repeatable-read production snapshot containing
8,804 articles, including 3,209 pending articles. Both implementations returned the
same metrics and targets. Comparison sorted the unrelated processing summary by
kind because its existing `UNION ALL` does not guarantee row order.

| Measurement | Before | After |
| --- | ---: | ---: |
| Complete overview calculation | 10,823 ms | 1,586 ms |
| Pending-article blockers query | 9,053 ms | 586 ms |
| SQL statements | 32 | 32 |

These are sequential diagnostic calculations, not deployed HTTP measurements or
latency guarantees. Database cache warmth and concurrent worker activity affect
results. The production service was not replaced by the diagnostic process.

A separate disposable profile with 2,000 articles, 1,000 topics/proposals and 6,000
analysis jobs measured overview p50/p95 of 391/399 ms across three requests after
warmup. The eight-worker mixed workload completed all 12 requests successfully,
with p95 920 ms. Reproduce the populated workload with:

```sh
DEVFEED_PROFILE_SUITE=core DEVFEED_PROFILE_ROWS=1000 \
DEVFEED_PROFILE_REPEATS=3 DEVFEED_PROFILE_CONCURRENCY=8 \
bash scripts/profile-api.sh reports/admin-fix-api-profile.json
```

The overview now waits asynchronously for an existing cache refresh instead of
immediately returning 503. One poller per day range per API process prevents a
burst of waiting requests from exhausting Redis connections and falling back to
SQL together. Database work stays in a worker thread and releases its connection
before cache publication. The existing 60-second cache lifetime and authentication
requirements are retained. See [the concurrency checks](api-concurrency.md) for the
one-connection, concurrent dashboard/settings/readiness regression scenario.

## Production worker query audit on September 13, 2026

The follow-up inspected DevFeed's `pg_stat_statements`, table/index sizes and fresh
read-only query plans. Statement statistics are cumulative since their last reset
on September 8; they are not a current request latency distribution. No production
data, schema, settings or workloads were changed by these diagnostic queries.

- Source approval guards accounted for 94,473 calls and about 53 minutes of total
  statement time, with a maximum above ten seconds. They used exclusive row locks
  even when callers only needed approval and policy to remain stable. Workers now
  use `FOR SHARE OF sources`, retaining ordered acquisition and conflicts with
  source updates/deletes while allowing guards for other articles from that source.
  The regression uses independent transactions to prove concurrent guards succeed
  and source revocation waits until they finish. Article/job locks and the taxonomy
  write lock are retained.
- Pending-topic matching fetched full proposal documents and normalized the same
  article again for every candidate. It now projects name, slug, aliases and
  keywords and normalizes article evidence once. A production comparison of 3,211
  candidates reduced the returned JSON from 2.42 MB to 524 KB with equivalent
  identity fields. A second repeatable-read comparison with 3,206 current proposals
  and a synthetic 44 KB article took 10,754 ms before and 556 ms after, with the same
  matching result. This includes Python matching time, which database statement
  statistics exclude. Regression coverage includes missing alias/keyword arrays
  and descriptions that mention a topic but are not matching identities.
- Automation reanalysis reuses the catalog it already loaded under the taxonomy
  lock in that transaction. This removes duplicate topic/tag scans without a cache
  that could outlive an editorial change.
- Dispatch and expired-job recovery omit input/catalog snapshots, results,
  notification payloads and requester documents. Deferred fields use `raiseload`
  so an accidental access fails instead of issuing a hidden query. Workers still
  load their bodies when executing a job; dispatch/recovery keep their existing
  eligibility, lease ownership and retry policies. Tests verify stored bodies
  survive dispatch and recovery unchanged.
- Recovery lacked indexes dedicated to running leases. A fresh production
  notification recovery plan visited 3,147 buffers across 47,807 retained rows,
  took 151 ms and returned no expired jobs. Migration `0005` adds partial
  `(lease_until, id)` indexes with `status = 'running'` to all eight durable job
  tables. A disposable 50,000-completed-notification comparison reduced the scan
  from 1,470 buffers to one (9.915 ms to 0.035 ms), returning zero rows in both cases.

Timing comparisons are diagnostic samples affected by cache warmth and concurrent
activity, not deployed throughput measurements. Exact overview counts, broad
catalog matching and topic-research correction predicates still grow with retained
data. No general claim is made that every production query is now inexpensive.

Reproduce the new concurrency, projection and recovery-plan regressions using
explicit disposable database and Redis URLs, following the `_test` / database-15
safeguards in [development](development.md):

```sh
DEVFEED_HOTSPOT_PROFILE_REPORT=reports/recovery-index-profile.json \
uv run pytest tests/test_production_query_hotspots.py \
  tests/test_admin_overview_concurrency.py
```

Release this change with the normal application migration process to schema
revision `0005`. The index migration uses transactional `CREATE INDEX`, which
blocks writes to each indexed table until the migration transaction commits;
schedule it with the application rollout and account for that pause. Readiness
checks require the application's exact schema revision, so migration and workload
versions must be coordinated. Downgrade removes only these eight indexes and was
verified, along with re-upgrade, on the disposable database. No production migration
or deployment has been performed by this audit.

Final validation passed 2,411 backend tests against disposable PostgreSQL and
Redis (six skipped), Ruff lint/format, mypy for 183 source files and workspace
version checks. Regenerating the admin API contract produced no changes.
