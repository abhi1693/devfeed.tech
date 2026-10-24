# Feed-options source-facet scale checks

Issue [#297](https://github.com/abhi1693/devfeed.tech/issues/297) concerns the source
facet inside `/v1/feed/options`. The fix orders approved/enabled source identities
before checking article eligibility, uses an ordinary `EXISTS` semijoin, and limits
identities before hydrating public source metadata. PostgreSQL can stop after 500
matching identities, choose an article-first plan for selective filters, and avoid
sorting private editorial payloads. The filtered topic/tag/search path uses the
same public source hydration. No new index or migration adds ingestion work.

## Reproduce

Use an otherwise idle native ARM64 host and native PostgreSQL 17. The PR's
`Feed options scale comparison` workflow owns a disposable PostgreSQL 17.11 service
and publishes the full matrix as an artifact and job summary. Local x86 runs are
diagnostic evidence and cannot establish ARM64 performance.

The workflow also updates a Bot-authored PR comment with before/after tables and
signed percentage improvements: positive means less time or work, negative means
more. Slower medians remain visible alongside paired measurements, and the full
scenario table is expandable. Comments identify the measured commits and run;
partial or unavailable results are labelled explicitly. Older runs cannot replace
newer results, and human-authored comments are preserved.

Create a new disposable database named **exactly**
`feed_options_benchmark_test`, exposed on `127.0.0.1`. The harness refuses a
non-loopback target, another database name, an existing public schema, or a
PostgreSQL version outside 17.x. It migrates this empty database and replaces its
synthetic fixture between scales. Supply its URL explicitly; the harness does not
load the checkout's `.env` or contact Redis, providers, or production.

```sh
DEVFEED_TEST_DATABASE_URL='postgresql+psycopg://benchmark:benchmark@127.0.0.1:55498/feed_options_benchmark_test' \
  uv run --locked python scripts/feed_options_benchmark.py \
  --baseline-ref 6da65eca --rows 2000 100000 1000000 --repeats 5 \
  --require-arm64 --output reports/feed-options-benchmark
```

The database must be new for each invocation. Remove `--require-arm64` only for
explicitly labeled local diagnostics. The output remains under ignored `reports/`.

## Workload and evidence

The harness runs each revision's actual `feed_options` function, validates
`FeedOptionsOut`, and serializes public JSON. Every baseline/candidate pair shares
one read-only repeatable-read snapshot, including during concurrent ingestion.
Different output fails the run. Each case warms both revisions once, then
alternates their order for the timed pairs. Recorded wall time includes SQL,
hydration, schema validation and JSON serialization; application CPU measures the
request thread with `thread_time`, excluding the ingestion thread.

The fixed fixtures contain 2,000, 100,000, and 1,000,000 articles. Source counts are
1,406, 1,406, and 14,060: the larger fixtures preserve approximately the production
article/source ratio. Two publishers receive about 4% of origins each; the
remaining publishers have long-tail histories. Roughly 79% of articles are
published, 85% approved, and 96% English. Content types follow the observed broad
distribution. Status, language, type and source assignment use independent
arithmetic distributions. The fixture includes disabled/unapproved sources and
duplicate same-source origins, with more than 500 eligible sources at 100,000
articles and beyond. Actual counts and distributions are recorded and the
eligible-source assertion fails an inadequate fixture.

For realistic relation sizes, each article has nine tag links, four topic links,
1.7 historical analysis jobs, 1.13 enrichment jobs, and 1.37 ingestion jobs on
average. This approximates the production cardinalities observed during the
issue investigation; it does not copy production data or job payloads. Image
jobs, external services, worker execution and production cache contents are not
modeled. Historical bulk loading bypasses USER triggers while retaining foreign
keys and indexes; triggers are restored before `ANALYZE` and every measured
ingestion write. This avoids replaying millions of historical invalidations and
read-model events during fixture preparation.

Each scale covers English, all languages, rare and absent languages, a minority
content type, combined topic/tag filters, and selective full-text search, both
idle and with concurrent transactional insertion of articles, origins and
analysis jobs. The writer records successful commit counts, wall time and its
thread CPU; an insertion error fails the run. These metrics describe the shared
mixed workload, not separate before/after ingestion throughput or maximum
capacity. The two larger scales additionally force server-prepared generic
execution for broad English and rare-language cases. Psycopg's prepare threshold
is explicitly zero and recorded.

`results.json` includes revision provenance, the candidate feed file hash and dirty
state, host/database architecture, settings, raw timing samples, dataset
metadata, ingestion commits and per-case plan metrics. `summary.md` presents the
comparison. SQL artifacts contain `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT
JSON)` with actual loops, row widths, sort behavior, shared reads/hits and temporary
reads/writes. Plans are replayed with the captured binds **after both timed calls**;
they are execution diagnostics rather than captures of the exact prepared plan
used by every timed call. Inspect all plans when PostgreSQL chooses different
join strategies: a semijoin need not always perform fewer nested index probes.

These warm synthetic measurements do not establish production p95, cold-cache
behavior, cache isolation, maximum HTTP concurrency or worker recovery. Existing
behavior/integration tests cover visibility transitions and the HTTP cache
boundary separately. After an authorized deployment, compare fresh window-delta
query statistics under matched traffic and verify latency, ingestion freshness,
write load and recovery. Rollback restores the prior application image; no schema
or index rollback is needed.
