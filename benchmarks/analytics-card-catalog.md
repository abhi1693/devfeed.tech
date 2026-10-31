# Analytics card catalog reuse

Historical card import previously rebuilt the complete catalog and candidate
ranking index for each page of at most 200 identities. It now retains those
immutable inputs for the importer lifetime when caching is enabled, with
transactional revision checks on every page. Disabled caching keeps the old page
SQL path and does not acquire extra revision reads. This extends the recurring reconciler's existing retained scopes
to a separate caller that did not use them. The recurring reconciler's production
CPU improvement was already on `master`; this PR adds its regression benchmark.
The production audit confirmed recurring reconciliation activity, without
establishing that a historical import was active.

The native regression compares direct cold `build_cards` calls, representing the
old historical page path, against the actual `CurrentStateReconciler._build` and
`HistoricalAnalyticsImport.import_cards` methods. All three use the same native
card builder, unchanged candidate scoring, PostgreSQL data, 200-row page limit,
and exported DTOs. Caching is enabled with a fault injected at the cache transport boundary to
compare the native fallback paths; per-page revision reads remain enabled. Separate
PostgreSQL measurements cover actual Redis cold/warm caches and disabled caching. The sink acknowledges writes in memory so that
ClickHouse network latency does not obscure builder cost. These measurements
cover card construction and Python process CPU, excluding PostgreSQL CPU, fixture
insertion, checkpoint transport, process startup, telemetry, and idle polling.

## Reproduce

Use disposable migrated PostgreSQL and Redis services as described in
[`tests/README.md`](../tests/README.md). `DEVFEED_TEST_DATABASE_URL` must name a
database ending in `_test`, and `DEVFEED_TEST_REDIS_URL` must select database 15.
The fixture truncates application tables and flushes that Redis database. It
never uses production credentials or connects to ClickHouse.

```bash
# Compact integration regression used by CI
uv run pytest -q -s tests/admin/reporting/test_analytics_card_catalog.py

# Audited production-size catalog and actual 200-card historical pages
DEVFEED_CARD_CATALOG_TAGS=55000 \
DEVFEED_CARD_CATALOG_TOPICS=4450 \
DEVFEED_CARD_CATALOG_ARTICLES=600 \
DEVFEED_CARD_CATALOG_PAGE_SIZE=200 \
uv run pytest -q -s tests/admin/reporting/test_analytics_card_catalog.py
```

The `CARD_CATALOG_BENCHMARK` JSON line includes catalog loads, ranking preparations,
SQL statement count, CPU/wall seconds, architecture, and fixture sizes. Timing is
informational; regression assertions cover deterministic work and behavior:

- Three cold pages load and prepare three catalogs. Both retained owners load and
  prepare once, preserving every exported field and semantic hash.
- A raw SQL committed definition edit forces a replacement catalog and ranking
  index, and current analysis becomes stale. The replacement matches a fresh cold
  build. A rolled-back edit keeps the previous committed generation valid.
- The old prepared generation becomes collectible after replacement. Only one
  catalog/index generation is retained by each owner.
- Source-card construction reads no catalog and prepares no ranking index, even
  beside the full taxonomy. Private feed query credentials are not exported.
- Rendering leaves analysis jobs and article publication statuses unchanged.

The separate importer unit suite checks cancellation before acknowledgement,
exclusive build/publication ownership, thread completion before retry, failed
build/insert replay, checkpoint ordering, empty/oversized pages, baseline revision
identity, safe error logging, and memory release when the finite import finishes.

## Local production-size observation

One paired x86_64 run on 2026-10-31 used PostgreSQL 18 with 2 CPUs and 2 GiB,
55,000 tags, 4,450 active topics, and 600 active article cards in three 200-row
pages. All database revision and classification triggers remained enabled.

| Builder path                 | Catalog loads | Ranking preparations | Python CPU | Wall time |
| ---------------------------- | ------------: | -------------------: | ---------: | --------: |
| Cold historical page control |             3 |                    3 |    14.65 s |   15.18 s |
| Existing current reconciler  |             1 |                    1 |     9.67 s |    10.10 s |
| Historical importer with reuse |           1 |                    1 |     10.08 s |   10.57 s |

Historical construction used about 31% less Python CPU in this single sample.
The deterministic regression is the two avoided catalog loads/preparations and
identical output. This is a sequential local sample with warm database pages,
without alternating rounds or a statistical confidence interval. Its magnitude
does not predict production savings. The cold path executed 54 SQL statements versus 48 for both retained owners.
The retained paths avoid repeated catalog reads and their before/after revision
fences. Disabled caching instead bypasses this added scope and preserves the old
SQL count. See the [paired PostgreSQL mode report](importer-postgres-work.md).
Both owners' source-card pages together used 36 SQL statements, with zero catalog
loads or ranking preparations. Full counters are in
[`analytics-card-catalog.json`](analytics-card-catalog.json).

## Limits

Catalog retention exchanges bounded process memory for repeated loading and
preparation. It retains the existing bounded candidate-result caches, and clears
the old generation before replacement. Cheap revision checks add SQL calls on
small workloads; query count alone does not measure the cost of transferring and
preparing tens of thousands of catalog entries.

This fixture does not establish whole-container savings or a new resource limit.
Native ARM64 and released-image telemetry measurements remain separate rollout
checks. No image pin, live resource request, policy, migration, or production
checkpoint is changed by this work.
