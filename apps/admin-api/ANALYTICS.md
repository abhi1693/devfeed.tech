# Administration analytics

Current article, source, discovery, proposal, and relationship boards read typed
ClickHouse cards. Overview charts query compact native summaries and current
cards through one shared API cache. PostgreSQL retains authoritative business
records, evidence, job execution, and transactional action validation.

## Capture and publication

One `python -m devfeed_core.analytics_worker` process owns historical facts,
current gauges, and operator cards. There are no pipeline/report projector
processes or saved report documents in PostgreSQL.

Source transactions append narrow before/after records to
`analytics_ingest_events`. Native capture watches omitted card dependencies,
including content and decision evidence, without serializing those private
fields. Body, prompt, result, review-note, and credential content remains in
PostgreSQL. Owner changes retain both identities so unlinking, reparenting,
candidate promotion, and deletion invalidate every affected card.

The exporter acknowledges exact event IDs after durable historical insertion and
current-card invalidation. IDs are identifiers rather than commit-order cursors;
replay and late commits cannot silently skip work. Unsupported source/format
versions fail before acknowledgement. A shared generation-fenced native lease
prevents an expired owner from publishing over its replacement.

The existing typed facts, canonical aggregates, hour/day summaries, current
gauges, and cold ClickHouse archives retain their history. Current-card bootstrap
has separate policy and coverage state; adding a card dependency does not reset
historical source cursors or replay the historical population.

## Current operator cards

Five kinds—articles, sources, discovery candidates, topic proposals, and
relationship proposals—use 256 stable UUID shards each. PostgreSQL keeps only
three small native control tables:

- `analytics_card_refresh` coalesces exact dirty identities.
- `analytics_card_backfills` checkpoints initial coverage for the five kinds.
- `analytics_card_fanout` checkpoints source/catalog invalidation passes.

Builders hydrate at most 200 combined identities before evaluating lifecycle
rules. Each publication batches up to eight shards of the same kind into one
canonical insert, one snapshot copy, and one manifest insert. Bulk control-table
updates retain per-identity generation checks. Source fanout uses bounded origin keysets. Catalog fanout considers
catalog-sensitive current ClickHouse cards. Concurrent changes coalesce without
repeatedly rewinding a running scan. Timed job availability transitions enqueue
bounded due-card work. Article scheduler-clock changes create no capture event or
card rebuild; returned article IDs receive their current display clock in one
bounded authoritative query.

`analytics_card_canonical` retains the latest durable card and tombstone for each
policy/kind/shard/entity. It has no TTL. Full immutable shard generations are
copied into `analytics_cards`; the exporter publishes the small
`analytics_card_manifest` last. Semantic hashes prevent unchanged facts from
publishing another generation. Quiet snapshots renew daily; serving snapshots
expire after seven days without deleting canonical state or business history.

Every request freezes a manifest vector. Counts, ordering, pages, and payloads
all use that vector. Filters operate on typed columns, then fetch payloads only
for the limited page. HTTP reads never calculate population-wide lifecycle
state. Private source/candidate URI matching uses narrow PostgreSQL identity
queries, and returned page URIs are hydrated separately; credentials never enter
ClickHouse search/payload fields.

A changed derivation policy builds beside the previous complete policy. Initial
coverage waits for all five backfills, initial fanout, and a fixed initial-dirty
barrier. Later writes add lag without starving first readiness. Only complete
coverage switches the serving policy. Truncation resets current card coverage
without resetting historical export or deleting archived facts.

## Reports and freshness

Twelve chart response contracts remain independent. Related cards share
singleflight calculations by section, resolved UTC window, and policy. A process
owns one asynchronous ClickHouse client with a global two-query admission limit.
Results remain fresh for 30 seconds; complete local/Redis fallback results may be
served with explicit stale metadata during failures. Cold ClickHouse failures
return the retryable preparing response, and the admin automatically retries.
There is no PostgreSQL historical aggregation fallback or saved-report worker.

Queue pressure uses a separate gauge-only calculation and cache key; polling it
does not refresh historical processing charts. Historical throughput and outcomes
share their two summary queries. Only Queue pressure polls after success; other
charts retain preparing retries and explicit reload behavior.

Overview accepts Grafana-style `from`/`to` date math or inclusive UTC dates.
Resolved bounds identify caches, so equal-length custom intervals do not replace
each other. Summary queries filter exact bounds before grouping. Large ranges use
bounded weekly/monthly series. Token and financial counters remain exact;
approximate distinct-reader and percentile summaries remain labelled estimates.

Current status comes from `analytics_card_status`; historical readiness comes
from `analytics_pipeline_status`. Bootstrap readiness and ongoing freshness are
independent. A stale/catching-up observation never authorizes an operator action:
advance, retry, approval, and publication still lock and validate current
PostgreSQL facts and reject changed action tokens.

## Resource isolation and Compose

Compose retains one analytics exporter and one persistent ClickHouse service.
Default service budgets are 0.5 CPU/512 MiB for the exporter and two CPUs/2 GiB for
ClickHouse. Shared reader and ingestion profiles reserve separate concurrency and
memory budgets across replicas. The `DEVFEED_CLICKHOUSE_*` settings must fit
within those server constraints; client settings cannot raise profile ceilings.

The exporter uses the separate `devfeed_ingest` account. Only the admin API and
exporter receive application ClickHouse credentials. ClickHouse database ports
remain private. CH-UI joins the private data network and publishes port 4000 on
`DEVFEED_CH_UI_BIND_IP` (falling back to the shared bind address/all IPv4). Set
`DEVFEED_CH_UI_URL` to the browser origin for LAN use. Its own persistent
`ch-ui-data` volume retains sessions, saved queries, dashboards, and encryption
state. Management UI availability does not gate applications or migrations.

## Migration and rollout

Build compatible backend/exporter and admin images before migration. Stop the
old exporter and both obsolete projectors. Before applying `0042`, back up the
five disposable derived tables and native PostgreSQL control state, and record
business identity/count checks. Preserve the persistent PostgreSQL and ClickHouse
volumes and all analytical facts, manifests, and cold archives.

Migration `0042` removes the old projection/report triggers and functions,
renames the queue to `analytics_ingest_events`, preserves pending IDs, their
sequence and format-1/2 contents, then makes new captures format 3. The rename
fences older exporters that cannot interpret current dependencies. It clears the
old lease while preserving historical ready flags, source cursors, and retained
facts. It creates only current-card controls and removes the five derived tables:
`pipeline_projections`, `read_model_events`, `read_model_states`,
`report_snapshots`, and `report_refreshes`.

Start only the native exporter and compatible APIs after the migration. Wait for
both historical readiness and current policy coverage, then verify board counts,
search, transitions, action guards, report freshness, and source-write cost.
Retire the old projector deployments from the final Fleet manifests. The native
image smoke gate checks the adapted exporter command, both readiness contracts,
retained business records, and real fresh/stale health probes.

The migration rejects downgrade. Reverting application code cannot recreate
compatible derived state or reverse the capture protocol. Use a planned forward
repair or coordinated backup restore. Local validation does not imply a
production rollout.

## Disposable profiling

`scripts/scale_fixture.py --seed` prepares explicitly local PostgreSQL/ClickHouse
`_test` databases with deterministic business history and native export. Redis
must be explicitly local database 15. Seeding replaces the selected fixture;
never select the persistent interactive stack for destructive pytest fixtures.
The former profiler's stable entity identities and production-baseline counts
remain available in this reusable helper.

`scripts/profile_analytics_reports.py` measures native report queries and optional
ClickHouse query-log resource costs. `scripts/profile_reader_views.py` reuses the
same business fixture helpers for reader profiling. Use unique scratch databases
for migration/recovery tests, then validate the normal persistent seeded stack
separately without truncating it.

Historical measurements and their limitations remain in
[the native baseline](../../benchmarks/clickhouse-native-baseline.md) and
[the retired PostgreSQL baseline](../../benchmarks/postgresql-read-model-baseline.md).
They do not certify the new current-card path or production ARM64 hardware.
