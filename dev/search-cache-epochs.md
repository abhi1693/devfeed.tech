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
