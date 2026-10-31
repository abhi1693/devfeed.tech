# Source tag identity reuse across worker processes

The process-local source-tag identity cache disappears with each RQ work horse.
The optional shared projection retains only names/slugs/aliases and UUID identities;
article classification and approval decisions remain current transaction reads.
`DEVFEED_SOURCE_TAG_SHARED_CACHE_ENABLED` defaults to false.

Ingestion now prefetches the bounded Redis payload and decodes it before opening
its final SQL transaction. A delivery-scoped context carries that immutable
candidate; the database revision is still authoritative before and after reuse.
The context is cleared on every exit. Without prefetch, other callers skip shared
Redis reads and use the baseline SQL path. Cold projection compression/publication
runs only after the root transaction has ended and its connection is checked in;
explicit rollback cancels publication. A known-open cache breaker skips expensive
serialization. Cache failures cannot turn a committed transaction into a failed
delivery, and RQ deadlines still propagate.

The projection occupies one dedicated namespace slot, separate from the generic
tag catalog and public response generations. It uses a UUID table and integer
references compressed at level 1. GETRANGE reads at most 4,000,001 bytes; retained
wire data is capped at 4,000,000 bytes, decompression at 16,000,001 bytes and valid
projections at 100,000 distinct tag IDs. Oversized or malformed data falls back to
SQL. TTL bounds storage; transactional revisions fence raw SQL edits and rollback.
A failed shared-hit fence falls back without a third revision read or publication.

## Resolver CPU sample

The original local x86-64 fixture contained 55,000 tags with one alias each, above
the 54,055 observed production count. It cleared the in-process snapshot between
calls to model successive work horses:

| Resolver measurement | Cold projection | Fresh-worker shared hit |
| --- | ---: | ---: |
| Process CPU | 1.879 s | 0.328 s |
| Source-name normalization calls | 110,001 | 1 |
| Full catalog queries | 1 | 0 |

The stored projection was 2,424,003 compressed bytes / 6,291,220 decoded bytes.
This earlier resolver sample includes loading/publication on the cold path; it is
not a whole-worker or production CPU/RSS result. The final prefetch/deferred
lifecycle is audited separately below. Payload bytes do not bound Python heap or
establish production resource limits.

## PostgreSQL work and slot audit

The final code ran actual baseline functions from `6f12ed1d` and current functions
on matching local x86-64 PostgreSQL 18/Redis fixtures: 55,000 tags, 4,450 active topics,
one alias each, and one tagged feed entry for whole-ingestion cases. Server
`pg_stat_statements` counters were reset immediately before each target call.
The [raw counters and negative observations](source-tag-identities-postgresql.json)
include planning/execution elapsed, rows, buffers, checkout/transaction counts,
row-locking statements and PostgreSQL-container CPU. Application SQL counts
exclude driver transaction commands; nested trigger work belongs to its enclosing
statement. Transaction and slot times sum their respective intervals.

| Resolver condition | App SQL before → after | SQL rows before → after | Shared hit/read blocks before → after | SQL execution ms before → after | Slot ms before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cold | 3 → 3 | 55,002 → 55,002 | 11,703 → 11,711 | 61.28 → 62.53 | 1,507.8 → 1,366.4 |
| Same-process local hit | 1 → 1 | 1 → 1 | 72 → 72 | 0.03 → 0.02 | 5.9 → 5.6 |
| Fresh-worker shared hit | 3 → 2 | 55,002 → 2 | 11,703 → 74 | 56.00 → 0.06 | 1,539.9 → 7.3 |
| Redis outage | 3 → 3 | 55,002 → 55,002 | 11,701 → 11,703 | 54.69 → 54.17 | 1,431.3 → 1,612.9 |
| Catalog alias edit | 3 → 3 | 55,002 → 55,002 | 11,706 → 11,706 | 62.15 → 52.98 | 1,658.6 → 1,392.4 |
| Corrupt cache payload | 3 → 3 | 55,002 → 55,002 | 11,703 → 11,703 | 51.79 → 52.07 | 1,569.0 → 1,581.7 |

Each resolver call uses one transaction/checkout and zero row-locking statements
in both versions. Native cases assert zero checked-out SQL connections during
Redis GET/decode and publication, including corrupt/unavailable transports, root
commit/close and revision changes during validation. A concurrent failed-fence
case retains the baseline three SQL reads and uses fresh tag rows.

| Whole tagged ingestion condition | App SQL before → after | SQL rows before → after | Shared hit/read blocks before → after | SQL execution ms before → after | Slot ms before → after |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cold | 30 → 30 | 114,473 → 114,473 | 16,075 → 16,031 | 152.60 → 155.33 | 3,117.4 → 3,185.1 |
| Shared hit | 30 → 29 | 114,473 → 59,473 | 16,068 → 4,582 | 153.71 → 103.94 | 3,384.3 → 1,779.5 |
| Redis outage | 30 → 30 | 114,473 → 114,473 | 16,068 → 16,066 | 148.83 → 152.82 | 3,171.8 → 3,436.6 |
| Catalog alias edit | 30 → 30 | 114,473 → 114,473 | 16,031 → 16,033 | 148.44 → 148.53 | 3,236.9 → 3,036.2 |
| Corrupt cache payload | 30 → 30 | 114,473 → 114,473 | 16,067 → 16,065 | 148.41 → 156.05 | 3,391.8 → 3,242.6 |

Every whole-ingestion case retains two transactions/checkouts and seven existing
row-locking statements. The unrelated heuristic catalog reads are unchanged in
this independent PR. Ordinary samples retain background maintenance: one cold
whole-ingestion CPU observation increased 528.4→627.9ms and corrupt-payload CPU
increased 224.5→236.7ms despite matching SQL work. These are preserved, not a
zero-pressure certification.

A second disposable-only cohort disabled autovacuum and checkpointed before each
sample to reduce maintenance/order noise. Three samples per side produced:

| Resolver condition | Median PostgreSQL-container CPU ms before → after | Median slot ms before → after |
| --- | ---: | ---: |
| Cold | 88.70 → 87.10 | 1,463.4 → 1,398.5 |
| Shared hit | 85.97 → 31.32 | 1,586.9 → 6.5 |
| Redis outage | 86.31 → 86.87 | 1,559.4 → 1,553.1 |
| Catalog alias edit | 92.78 → 88.46 | 1,466.3 → 1,483.7 |
| Corrupt cache payload | 87.18 → 92.86 | 1,506.8 → 1,457.5 |

Container CPU includes fixed cgroup-read probes and background costs. Three
samples cannot establish CPU equivalence; the upward outage/corrupt CPU and
17 ms churn-slot observations remain visible. These tests prove no additional
fallback SQL calculation/scan/transaction/lock and correct placement of cache
work, not universal zero cost or production neutrality.

Keep the switch off until a native ARM64 canary measures PostgreSQL CPU, worker
RSS, slot occupancy, label distribution, hit ratio, edit rate and concurrent
imports against explicit budgets. Frequent edits can remove reuse benefits and
add application publication CPU/Redis storage. Disabling the dedicated flag
restores process-local behavior without changing public response caching.
No migration or production deployment is part of this PR.

```sh
uv run pytest -q -s tests/sources/test_source_tags_integration.py
uv run pytest -q tests/sources/test_source_tags.py
```

Use only disposable `*_test` PostgreSQL and Redis DB15, as described in
[the test guide](../tests/README.md). Tests truncate their configured databases.
