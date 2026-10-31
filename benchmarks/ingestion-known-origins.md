# Bounded ingestion origin reads

A repeated HTTP 200 feed previously performed one origin point lookup per entry.
Ingestion now reads the current bounded feed's origin identities once inside the
existing guarded persistence transaction and shares that map with entry storage.
Empty and single-entry feeds keep their original query path. Inserts extend the
map so duplicate keys in one feed retain the existing deduplication behavior.

Language inference remains outside every database transaction and runs for all
parsed entries, as before. There is no advisory preflight, additional transaction,
new lock or language cache. The transaction still checks the current lease,
approval, enablement and feed URL before reading origins. Explicit tag restoration,
current manual/AI classifications, source metadata and arrival accounting retain
their existing paths. An article removed while inference runs is missing from the
current batch and can be reimported with its already-inferred language.

## Controlled PostgreSQL comparison

Local x86-64 PostgreSQL 18 ran actual baseline ingestion from `6f12ed1d` and this
implementation on matching disposable fixtures. Both sides received the same
synthetic HTTP feed and deterministic language output, with no language model or
network request measured. `pg_stat_statements` was reset immediately before each
whole ingestion; its top-level counters include trigger execution in their
parent statement. Application counts exclude driver transaction controls.
[Recorded counters and plans](ingestion-known-origins-postgresql.json) include
0, 1, 10, 128 and 500 entries, new and repeated feeds, and a 100,000-row retained
source history. The scaling relation was analyzed before measurement.

| Workload | App SQL before → after | SQL rows before → after | Shared hit/read blocks before → after | SQL execution ms before → after | Open transaction ms before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Empty new feed | 17 → 17 | 14 → 14 | 1,867 → 1,852 | 13.06 → 12.09 | 66.4 → 53.1 |
| One retained entry | 18 → 18 | 15 → 15 | 1,926 → 1,926 | 12.79 → 12.98 | 58.9 → 60.0 |
| 10 retained entries | 27 → 18 | 24 → 24 | 1,946 → 1,929 | 13.41 → 13.27 | 62.9 → 62.6 |
| 128 retained entries | 145 → 18 | 142 → 142 | 2,948 → 1,935 | 19.31 → 13.18 | 198.5 → 61.3 |
| 500 retained entries | 517 → 18 | 514 → 514 | 14,924 → 1,953 | 62.93 → 13.86 | 474.1 → 68.4 |
| 500 new entries | 4,017 → 3,518 | 2,514 → 2,514 | 84,417 → 80,221 | 1,655.73 → 1,566.34 | 7,286.5 → 6,104.2 |
| 500 retained entries, 100k older origins | 517 → 18 | 514 → 514 | 3,952 → 3,676 | 33.24 → 18.17 | 482.2 → 71.5 |

Every workload retains two transactions and two checkouts. The 500-entry repeat
retains five row-locking statements; the new feed retains 505, including unchanged
article locks. The 100k-history query uses the existing composite origin index:
one `Index Scan` returns 500 rows with 1,660 shared hits, replacing 500 individual
indexed point queries. No sequential scan of that history is introduced. A native
regression independently checks the plan against 100,000 source-origin rows.

An earlier two-snapshot language-skipping design increased shared buffer work
and connection occupancy for some workloads. It is excluded from this PR. This
change spends no extra PostgreSQL work to avoid language inference: the 500-entry
repeat still receives 500 inference inputs, and the deleted-origin regression
runs inference outside SQL before reading the authoritative current origins.

These are single paired fixture observations, not a native ARM64 or sustained
production CPU/RSS benchmark. Small timing changes are noise; the empty/singleton
SQL paths are unchanged. Container CPU observations in the JSON include read
probes and background maintenance and must not be treated as isolated backend CPU
or production sizing. Cache outage/churn cannot add a path here because this PR
adds no cache. Existing source-tag identity/cache behavior is held constant.

```sh
uv run pytest -q tests/sources/test_ingestion.py \
  tests/sources/test_source_tags_integration.py \
  tests/taxonomy/test_languages_integration.py
```

Use disposable `*_test` PostgreSQL and Redis DB15 as described in
[the test guide](../tests/README.md). Tests truncate the configured databases.
