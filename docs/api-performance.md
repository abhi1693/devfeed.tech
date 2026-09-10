# API profiling

Run `bash scripts/profile-api.sh` from the repository. It starts its own disposable
PostgreSQL and Redis containers, migrates and seeds a test database, profiles actual
FastAPI handlers through response serialization, writes `reports/api-profile.json`
and `reports/api-profile.tables.json`,
and removes the containers even on failure. It does not use the development database.

```sh
# Defaults: 1,000 rows per entity family, 10 measured repetitions, 8 concurrent readers.
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

The overview's final p95 was 266 ms. The eight-reader mixed burst had no errors and
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
