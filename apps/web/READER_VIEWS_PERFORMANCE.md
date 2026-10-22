# Saved reader view performance

`scripts/profile_reader_views.py` profiles the reader settings, starter discovery,
and public feeds used by Columns. It accepts only explicit loopback PostgreSQL
databases ending in `_test` and loopback Redis database 15. Use dedicated services:
the integration-test fixtures truncate their database and must never share this
profiling database. Seeding requires empty fixture tables and has no reset option.

Set `DEVFEED_TEST_DATABASE_URL` and `DEVFEED_TEST_REDIS_URL` to those dedicated
services, then run from the repository root:

```sh
# Seed once. Business projections and database constraints stay active; the
# report enumerates background queue triggers disabled only during this transaction.
uv run python scripts/profile_reader_views.py --seed --seed-only --output reports/reader-seed.json

# Use the same fixture for both application checkouts.
uv run python scripts/profile_reader_views.py --app-root /path/to/baseline \
  --repeats 100 --concurrency 1,8,16 --output reports/reader-baseline.json
uv run python scripts/profile_reader_views.py --app-root /path/to/candidate \
  --repeats 100 --concurrency 1,8,16 --output reports/reader-candidate.json
```

Repeat three pairs, alternating which revision runs first. Each run measures 13
GET request shapes at each concurrency, a twelve-column wave with three requests
in flight, and settings writes from sixteen independent accounts and sixteen
workers contending for one account. The write phases restore their initial JSON
between phases and revisions. A smaller functional smoke uses a separate empty
database with `--seed --scale .001 --repeats 2 --concurrency 1,2`; its report is
explicitly labelled below production scale.

Select the application checkout with `--app-root`; the harness verifies the imported
package paths so editable installs cannot silently benchmark another revision.
Reports also fingerprint application source and the harness, including uncommitted
changes, rather than identifying both checkouts by a shared HEAD alone.
The baseline and candidate must use the same deterministic data, service limits,
cache policy, and idle host. Raw samples and PostgreSQL
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` plans belong in untracked reports.

After the core comparison, prepare the supplementary recommendation population
and run its real worker build once. A failed build stops qualification and keeps
the failed attempt in the report. Subsequent runs reuse the prepared state:

```sh
uv run python scripts/profile_reader_views.py --prepare-recommendations \
  --recommendations-only --repeats 100 --concurrency 1,8,16 \
  --output reports/reader-recommendations-prepared.json
uv run python scripts/profile_reader_views.py --app-root /path/to/baseline \
  --recommendations-only --repeats 100 --concurrency 1,8,16 \
  --output reports/reader-recommendations-baseline.json
```

Alternate baseline and candidate serving runs on the same prepared population.
The recommendation worker qualification is separate from serving latency.

The full fixture targets 500,000 articles, 7,500 sources, 25,000 topics, 250,000
tags, 550,000 origins, two million topic links, five million tag links, 1,000
accounts, and 250,000 likes. It includes ordinary readers, 100 follows per kind,
prolific like histories, language mismatches, inactive/unapproved records, and
empty/sparse selections. The production snapshot on October 22, 2026 contained
90,217 articles, 1,403 sources, 4,450 topics, 47,796 tags, 91,868 origins,
344,971 topic links, and 849,616 tag links. These are relevant reader tables,
not a copy of the production database or its unrelated job history.

The reader settings refactor shares one account projection with all four ownership
predicates. Writes read the JSON snapshot in their `SELECT ... FOR UPDATE`, reducing
ordinary saves from three SQL statements to two. Initialization that preserves an
already saved workspace needs one locked read instead of two. Workspace reads use
column-only normalization, and first-use My feed discovery reuses the provider's
state instead of reading the same columns twice. Profile label changes preserve
pending saves; a confirmed session revision still refreshes preferences.

After building web and both extensions, the shared browser scenarios exercise
personal initialization, retained saves, ordinary session refreshes, and twelve
different feeds at the retention limit of three pages each. They assert 864 article
rows, 36 feed requests, and at most three concurrent feed/metadata requests. Resource
reports also record DOM size, Chromium heap/layout/script counters, and supported
long-task observations. These measurements are evidence for rendering changes;
they are not machine-independent timing assertions.

The October 22 comparison used PostgreSQL 17.11, matching production's version,
in an owned container limited to four CPUs and 6 GiB, with 512 MiB shared buffers.
Redis 8.2 was limited to 256 MiB. The host was Linux x86_64 with sixteen logical
CPUs and approximately 30 GiB RAM. The API used a sixteen-connection pool with
no overflow and connected directly to PostgreSQL. It did not reproduce production's
PgBouncer, ARM64 hardware, ingress, or competing worker workload.

Request timing includes warm authentication, ORM work, response serialization,
and waits after admission to the selected concurrency. It excludes the generator's
admission queue, network transport, and browser work. The column-wave timing
includes its three-request queue. HTTP response caching is disabled; Redis cursor
sequences and authenticated sessions remain real dependencies. Query counts cover
application statements, while timing also includes connection health checks.

### October 22 results

The baseline was `6ed694e2`; candidate application sources were frozen and
fingerprinted before measurement. This first comparison covered the reader
preference refactor before the recommendation-query change described below.
Three alternating pairs passed all response,
query-count, and two-second route-p95 checks: 33,000 measured HTTP requests,
including 7,200 column-wave reads and 2,400 settings writes. The largest observed
individual-route p95 was 968 ms; the largest twelve-column-wave p95 was 1,045 ms.
Every distinct public feed path was separately verified to return 24 eligible,
correctly filtered articles.

| Operation                                             |   Before |    After |
| ----------------------------------------------------- | -------: | -------: |
| Ordinary settings save, SQL statements                |        3 |        2 |
| Preserve an initialized workspace, SQL statements     |        2 |        1 |
| First-use My feed columns-state GETs                  |        2 |        1 |
| Browser script work at 864 rows, median of three runs |  3.989 s |  2.875 s |
| Browser long tasks, median of three runs              |       43 |       22 |
| Browser long-task duration, median of three runs      | 3,117 ms | 1,658 ms |

Browser DOM size stayed at 864 article rows and 32,509 elements, with 36 feed
requests and no more than three requests active. The browser comparison had fixture
seeding in the background; the repeated observations and neighbor-render regression
support the specific rendering improvement. Heap measurements varied with garbage
collection, so they do not establish a memory improvement.

API latency comparisons remain inconclusive. A candidate-wide slowdown did not
repeat, and the mixed ASGI workload shared a Python interpreter across the public
and user services. A follow-up public-only diagnostic recorded equivalent Latest
p95 values of 143.93 and 144.71 ms, with approximately 100–114 ms garbage-collection
pauses in both versions and comparable SQL time. Those diagnostic phases used
60 samples per public route and explain why route-tail changes in the mixed process
must not be presented as production regressions or speedups. Garbage collection
remained enabled throughout.

Starter query plans also covered hot candidates containing thousands of unpublished
articles, empty accounts, and 100 nonmatching follows. The slowest single starter
statement took approximately 45 ms with 50,130 shared-buffer hits. A prototype
removing its repeated origin proof preserved results but saved fewer than 0.1% of
buffer accesses across ten alternating plan pairs; its SQL and visibility policy
were kept unchanged. Those starter-query plans did not justify an additional index.

### Recommended-feed rebuild

The supplemental fixture also exceeds the separately observed production
recommendation population: 497,500 synthetic candidate edges and 1,000 states,
versus 12,500 edges and 33 states at 10:06 UTC. Those extra profiles have valid
precomputed followed-source candidates and are explicitly unmeasured. The measured
reader receives a real recommendation build through the existing domain worker;
another reader remains pending to exercise the following-feed starter. Serving
qualification checks actual Redis order, generation identity, reasons, and disjoint
pages, rather than accepting an empty or database-fallback response as ready.

The first real build failed at the unchanged five-second statement timeout. Its
379 topic/source/tag signals repeatedly evaluated overlapping article visibility.
A read-only diagnostic with a separate twenty-second ceiling completed the original
query in 7.36 seconds, with 739,823 article primary-key probes and approximately
5.80 million shared-buffer accesses. The failed worker attempt and its uncomputed
state are retained separately from subsequent results.

The revised set-based query shares eligibility across the three signal kinds.
It obtains filtered public IDs and article ordering through separate, narrow
projections covered by the existing facet and feed indexes. This avoids repeatedly
loading wide article rows and avoids a full heap scan when the article table
exceeds shared buffers. No index or migration was added. The full fixture's
visibility map permitted index-only scans; a production table with recent writes
can still require heap fetches until vacuum marks those pages all-visible.

Small and sparse profiles retain indexed per-signal probes. The selector reads
ordinary PostgreSQL statistics for the reader's actual topic, approved-source,
and tag signals, including common values and residual frequencies. It shares
eligibility when estimated association work reaches an article-catalog pass;
missing or inconsistent statistics preserve the indexed path. The existing
selective-filter strategy and all eligibility rules, ranking, and per-signal
limits remain intact. Dense, ordinary, sparse, and language-mismatch probes
confirmed why neither query should replace the other unconditionally.

The final covered query produced the same 5,226 raw candidate records as the
original per-signal query on the full profile. Its diagnostic actual-worker plan
completed in 3.27 seconds, using both existing article indexes without heap
fetches. It used approximately 527,000 shared-buffer accesses, compared with
5.80 million in the original per-signal diagnostic. Compared with the intermediate
wide-table shared query, temporary writes increased from approximately 161 MiB
to 221 MiB: the covered projections exchange repeated heap reads for narrow
scans and sequential temporary work. This diagnostic had a separate twenty-second
ceiling for observation; the subsequent normal worker statement used the unchanged
five-second limit.
Diagnostic timings have different cache histories and are not a paired speedup
measurement.

Three subsequent fresh-state builds through the normal worker completed in
3.633, 2.738, and 3.110 seconds overall. Candidate statements took 3.201, 2.379,
and 2.515 seconds respectively, under the unchanged five-second limit. Every
build returned 500 recommendations and qualified its actual Redis sequence and
generation. The first followed a clean restart of the owned PostgreSQL container
and normal fixture preparation, without the profiler's global cardinality scans.
This cleared PostgreSQL shared buffers; it did not clear the operating-system
page cache. No competing tests or builds ran during measurement.

The final population was 498,000 recommendation edges and 1,000 states. Of those,
497,500 edges belong to the separately labelled synthetic, unmeasured profiles.
Full recommendation regressions passed 105 tests on the final query; the combined
profiler, fixture, strategy, and source-deduplication checks passed 75 tests.

A subsequent core qualification on the final application and maintained harness
passed 5,500 measured requests: all HTTP 200, no invalid payloads, and all SQL
and p95 budgets satisfied. The maximum route p95 was 502.7 ms at concurrency 16;
the twelve-column wave p95 was 563.0 ms. Every measured settings write used two
SQL statements. This is a separate final-source qualification, not a latency
comparison against the older harness used for the original six runs. Raw reports
record full hashes; final application source starts `eacf510b350e` and the
maintained harness starts `deda695be25d`.

Keep production cardinalities separate from local latency measurements. A larger
fixture can expose query growth; it does not establish production capacity across
different hardware, cache warmth, traffic mixes, network paths, and worker load.
