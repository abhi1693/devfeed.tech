# Ingestion heuristic catalog hydration

AI ingestion imported explicit publisher labels and then skipped heuristic
classification, but still hydrated every active Topic and every Tag as ORM objects
before checking any origins. Repeated HTTP 200 feeds paid the same cost even when
all entries were already known.

Catalog loading now occurs only when an entry reaches heuristic classification:
AI is disabled, the origin is new, and the retained article has no manual or AI
classification. Both catalogs are shared by the eligible entries in that feed
transaction. Publisher-supplied tags still resolve and attach on repeated imports.

A disposable PostgreSQL integration case seeds 10,000 active topics and 10,000
unrelated tags, imports two entries, and observes actual ORM load events:

| Mode | First import catalog ORM objects | Repeated import catalog ORM objects |
| --- | ---: | ---: |
| AI enabled | 0 | 0 |
| Heuristic classifier | 20,000 | 0 |

The test also confirms that both first-pass entries are inserted and the repeated
pass creates none. Existing suites cover heuristic classification, source tag
restoration, AI/manual classification protection, multilingual articles, source
changes, lost leases, and rollback/retry behavior.

```sh
uv run pytest -q tests/sources/test_ingestion.py \
  tests/sources/test_source_tags_integration.py \
  tests/taxonomy/test_languages_integration.py
```

Use disposable `*_test` PostgreSQL and Redis database 15 as described in
[the test guide](../tests/README.md). Tests truncate the configured databases.
The local validation ran on x86-64, not native ARM64 or production. Object-load
counts prove removed work; they are not a claim about total worker CPU savings.
The independent lightweight source-tag identity projection still runs when feed
labels need resolution. The changed path does not add a cache or migration.

## PostgreSQL work, connection and lock audit

A separate controlled local x86-64 PostgreSQL 18 run used 4,450 active topics,
55,000 tags with one alias each, and the same two-entry feed. It executed the
actual baseline `store_entries` from `6f12ed1d` and this implementation against
fresh disposable fixtures. `ANALYZE` preceded each sample;
`pg_stat_statements` was reset immediately before the workload. Application
statement counts exclude driver transaction commands; returned rows, shared
buffers and SQL execution time come from PostgreSQL's top-level counters.
Nested trigger costs belong to their enclosing statement. See the
[recorded statements and counters](ingestion-heuristic-catalogs-postgresql.json).

| Workload | App SQL before → after | SQL rows before → after | Shared hit/read blocks before → after | SQL execution ms before → after | Open transaction ms before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| AI, new entries | 18 → 16 | 59,460 → 10 | 3,492 → 2,216 | 106.10 → 29.68 | 1,363.8 → 72.6 |
| AI, retained entries | 4 → 2 | 59,452 → 2 | 1,412 → 145 | 70.35 → 0.05 | 1,332.3 → 6.6 |
| Heuristic, new entries | 18 → 18 | 59,460 → 59,460 | 3,748 → 3,774 | 95.36 → 114.60 | 13,057.0 → 14,352.5 |
| Heuristic, retained entries | 4 → 2 | 59,452 → 2 | 1,412 → 145 | 63.73 → 0.07 | 1,415.0 → 6.6 |

Each call used one transaction and checkout in both versions. New-entry cases
used two existing article row-locking statements in both; retained cases used
none. The removed catalog scans add no Redis dependency, invalidation query,
background transaction or replacement database calculation. The native regression
now checks actual catalog query row counts as well as ORM load events.

These are one paired observation per workload with warm analyzed fixture data,
not a cold-storage, concurrent production or native ARM64 sizing result. The
heuristic path performs identical SQL work; its elapsed time was higher in this
sample and it receives no database speedup claim. PostgreSQL-container CPU
observations are preserved in the JSON, including probe/background cost; they do
not establish isolated backend CPU or a production resource limit. Cache outage
and cache churn do not change this PR's path because it adds no cache. Sustained
concurrency, PostgreSQL CPU and slot occupancy remain rollout measurements.
