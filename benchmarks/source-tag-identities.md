# Source tag identity reuse across worker processes

The process-local source-tag identity cache is discarded with each RQ work horse.
Every new process can therefore fetch and normalize the same catalog even when
its PostgreSQL revision is unchanged. The shared projection is opt-in through
`DEVFEED_SOURCE_TAG_SHARED_CACHE_ENABLED=true`; it defaults to false while native
resource and catalog-mutation behavior are validated.

The projection has its own Redis namespace slot, separate from the generic tag
catalog and public response generations. It stores one UUID table and compact
integer references, then compresses that representation. PostgreSQL's existing
transactional tag revision is checked before and after reuse; TTL only bounds
storage. Raw SQL edits and transaction rollback cannot reuse an obsolete revision.
Missing, stale, unavailable or malformed cache data falls back to PostgreSQL.
Article/editorial decisions are still read from the current transaction.

A local disposable PostgreSQL/Redis test seeds 55,000 tags, slightly above the
54,055 tags observed in the production audit. Each synthetic tag has one alias:

| Resolver measurement | Cold projection | Fresh-worker shared hit |
| --- | ---: | ---: |
| Process CPU | 1.879 s | 0.328 s |
| Source-name normalization calls | 110,001 | 1 |
| Full tag catalog queries | 1 | 0 |

The stored projection was 2,424,003 bytes compressed and 6,291,220 bytes decoded.
The cold path includes database loading, normalization and cache publication;
these are resolver-path observations, not whole-worker or production savings.
Timing is reported, not used as a flaky test threshold. The test clears the
in-process snapshot between calls to represent successive RQ work horses.

Limits are explicit: a bounded GETRANGE reads at most 4,000,001 bytes, transport
payloads are capped at 4,000,000 bytes, decompression stops at 16,000,001 bytes,
and valid projections contain at most 100,000 distinct tag IDs. Oversized producers
skip publication. Truncated streams, trailing data, oversized decoded output,
invalid UUID/index references and unsupported payload shapes are rejected.

```sh
uv run pytest -q -s tests/sources/test_source_tags_integration.py
uv run pytest -q tests/sources/test_source_tags.py
```

Use only disposable `*_test` PostgreSQL and Redis database 15, as described in
[the test guide](../tests/README.md). Tests truncate their configured databases.
Coverage includes raw SQL alias changes, uncommitted projection rollback,
concurrent feeds, publisher tag restoration, and current manual classifications.

Before enabling the flag in production, measure native ARM64 resolver CPU/RSS,
shared hit ratio, catalog edit frequency, cold publication/compression cost, and
Redis payload/connection budgets under concurrent imports. Frequent catalog edits
can erase reuse benefits and add publication cost; bound and measure that tradeoff.
Local validation is x86-64 and does not establish a production memory limit.
Disable the dedicated flag to restore process-local behavior without changing the
public response cache. No migration or production deployment is part of this PR.
