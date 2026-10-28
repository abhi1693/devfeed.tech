# Database migrations

`versions/0001_initial.py` (revision `0001`) is the generated, frozen baseline for
DevFeed 0.0.1. It replaces all 29 pre-release revisions and directly creates the
baseline schema: 40 tables, constraints, indexes, standalone sequences, article slug
assignment and recommendation triggers. It includes reader accounts, source follows,
notifications, source relevance assessments and overview rollups. No seed taxonomy
or legacy data conversions run. Chimely owns its separate database and migrations.

## Fresh database

Configure `DEVFEED_DATABASE_URL` and `DEVFEED_REDIS_URL`, then run:

```sh
uv run devfeed db upgrade
uv run devfeed db check
```

Alembic resolves its migration directory and helper imports relative to `alembic.ini`.
Tools using `alembic -c /path/to/alembic.ini` can run from another working directory
without adding the checkout to `PYTHONPATH`.

This baseline requires an empty application database. Existing development databases
on the removed revision chain must be reset/recreated explicitly before upgrading.
Back up any wanted data and stop application processes before doing so. Neither the
application nor this migration automatically resets or stamps an existing database.

After a development reset, clear retained GET responses with `uv run devfeed cache clear`.
That command does not flush Redis or remove RQ jobs. Old queued jobs can reference
removed rows and must be handled separately; do not flush a shared Redis instance.

`/health/ready` compares the installed database revision to `SCHEMA_REVISION` in
`packages/core/src/version.py`. `devfeed db current` reports the database revision;
`/version` reports the revision required by the application.

## Future changes

Revision `0019` restricts topic kinds to the canonical values in
`packages/core/src/topic_kinds.py`. Before upgrading a database containing historical
kinds, mount a JSON object mapping each historical kind to a canonical value and set
`DEVFEED_TOPIC_KIND_MAP_PATH` to its file path in the migration job. The migration
converts topic rows and proposal drafts/snapshots, then fails before adding the
constraint if any stored value has no mapping. The mapping is runtime data and is not
part of the application source. An empty object is sufficient when no historical
kinds remain.

Example mount contract (the file contents are operator-managed):

```yaml
env:
  - name: DEVFEED_TOPIC_KIND_MAP_PATH
    value: /etc/devfeed/topic-kind-map.json
volumeMounts:
  - name: topic-kind-map
    mountPath: /etc/devfeed
    readOnly: true
```

The map must be mounted into the one-shot migration process; normal API and worker
containers do not need it after the migration completes.

Generate a new revision against a development database at the current head:

```sh
uv run alembic revision --autogenerate -m "describe the schema change"
```

Review the generated operations and update `SCHEMA_REVISION` when the application
requires the new revision. Include any functions, triggers or standalone sequences
that Alembic does not generate. Keep applied revisions and migration-only helpers
frozen; they never import mutable runtime models. `search_visibility.py` shares the
unchanged statement-trigger definitions for revisions `0028` and `0029`. Future
visibility behavior changes belong in a new revision instead of editing that helper.

`uv run alembic history` shows the chain. `uv run alembic upgrade head --sql` renders
SQL without executing it. Integration tests provision disposable databases through
Alembic before exercising application behavior. Downgrading this baseline to `base`
drops its tables and data, functions, triggers and sequences.

## Solver execution lane (0002)

Revision `0002` adds a non-null `requires_solver` boolean with a false default to
source, article and image enrichment jobs. Existing rows remain on their original
queues. It preserves the queue handoff across leases, retries and scheduler restarts.
This is an additive migration for deployed databases; `0001` stays frozen. Deploy
it with the next authorized release and matching `SCHEMA_REVISION=0002` runtime.
No production migration or application release is implied by committing it.

## Stable public source URLs (0004)

Revision `0004` adds a unique, indexed source slug and backfills existing sources
in creation order without changing UUIDs or their relationships. Names normalize to
lowercase hyphenated ASCII; duplicate names receive numeric suffixes. Empty names
after normalization fall back to `source`; `suggest` and UUID-shaped names receive
a `source-` prefix to protect existing routes. Concurrent creation is serialized
while allocating slugs. Renames preserve the original slug and direct slug changes
are rejected. Existing sources are queued for search reindexing.

Apply with `uv run devfeed db upgrade` before starting the matching runtime, which
requires `SCHEMA_REVISION=0004`. The migration briefly locks the sources table for
the backfill; schedule it with the next authorized release. Public response cache
keys and the sitemap inventory format change so old UUID-only payloads are rebuilt.
Existing public UUID links redirect permanently; internal UUIDs and follows remain
valid. No database reset or production rollout is part of this source change.

## Read later bookmarks (0007)

Revision `0007` adds private article bookmarks, keyed by account and article, with
an index for pagination by save time. Apply `uv run devfeed db upgrade` before
starting the matching APIs: signed-in engagement reads now require this table.
Compose Watch rebuilds APIs but does not apply migrations on every rebuild.
Existing article and like data is preserved. Downgrading removes saved bookmarks;
roll back application code first. This change does not create a new release.

## Per-call inference accounting (0008)

Revision `0008` adds `inference_calls`, indexed by start time and durable job ID.
It records invocation metadata and usage, never prompts or results. Apply the
migration before starting the new build; existing history is not backfilled.
See [AI cost optimization](../docs/ai-cost-optimization.md) for usage reporting,
accounting limitations and the optional compact article prompt format.

## 0014: discovery and retention

Adds nullable input-retention markers and concurrent indexes for alphabetic active
topics and old successful jobs. Requires runtime schema `0014`. Before retrying an
interrupted concurrent build, inspect `pg_index.indisvalid`; drop only an invalid
new index through an authorized operation before retrying. Column additions and
valid indexes are idempotent. Downgrade removes schema/indexes but cannot restore
pruned input payloads. No publication repair or immediate history deletion occurs
in this migration.

## 0024: partnerships

Revision `0024` follows the unchanged source-logo revision `0023` and creates the partnership
catalog, configurable import connections, accounts and memberships. It includes tier policy,
product logos and featured images, campaigns, exclusions, account and campaign audiences,
native advertising formats, product delivery restrictions, permanent article sponsor disclosures,
prepared contextual matches, delivery reservations and daily reporting.

The same revision creates bounded promotion-enrollment checkpoints, an indexed tracking-event
retention path and the full product/asset history index. Transactional matching triggers capture
product, article and taxonomy changes in durable jobs. Indexed token projections narrow candidate
lookups; the migration backfills existing article/topic tokens before building their GIN indexes,
including long publisher text. Source-logo jobs and existing reader/catalog data are preserved.
There is no per-reader exposure setting or periodic product/article sweep state.

Apply with `uv run devfeed db upgrade` before starting runtimes requiring schema `0024`.
This feature's development migrations are consolidated into this single revision; no compatibility
migration for superseded development revision markers is included. Existing local databases on
those markers require a separate repair based on their actual schema.

Downgrading to `0023` removes all partnership tables, product image jobs and image-job fields,
matching functions/triggers/token projections and article sponsor disclosures. Existing article,
topic and source image jobs remain. Sponsored articles are unpublished before their disclosure is
removed; clear reader response caches and rebuild the search index before starting the older
runtime. Retain a database backup if partnership data must survive a rollback. No production rollout
is implied by committing the migration.

## Adaptive source polling

Revision `0025` adds adaptive source polling after partnership revision `0024`.
Apply it before starting this feature. Existing source-logo and partnership migrations remain unchanged.

## Source processing lifecycle (0032)

Revision `0032` follows inference-summary revision `0031`. It adds separate source
profile and relevance operations, their results and current-work index, and private
processing requests. Quiesce source dispatch and drain older coupled source workers
before upgrading. Start the updated APIs and source/image workers before resuming
dispatch; older workers must not consume split-operation deliveries. Downgrade
requires draining split work and resolving explicit processing requests first.

## Article processing lifecycle (0033)

Revision `0033` follows source-processing revision `0032`. It builds the article-image
history index and expands the due-automation predicate to include approved unpublished
articles. Analysis and enrichment lookups reuse the `*_latest` indexes owned by `0027`;
this revision does not create duplicate history indexes for those tables.

The builds run concurrently with a five-second metadata lock timeout and repair invalid
interrupted builds on retry. Downgrade restores the pending-only automation predicate and
removes only the image-history index, preserving the parent indexes and all article data.
The new runtime requires schema `0033` and also accepts `0032` during a forward rollout.
Earlier schemas lack required source-processing fields. Drain older processes before
applying `0033`; when returning to older binaries, first restore the schema they support. No production
migration or publication repair is performed by viewing the pipeline.

## Analysis payload retention (0037)

Revision `0037` adds two durable retention checkpoints and three partial indexes for
newer retained successful analysis results. Existing age-selection and general history
indexes remain. Builds run concurrently with a five-second metadata lock timeout;
retry repairs invalid new indexes and preserves checkpoint rows created by an earlier
interrupted attempt. The migration does not scan or redact job payloads.

Stop older schedulers before migration so their per-tick retention cannot overlap the
new policy. Apply with `uv run devfeed db upgrade` before starting schema `0037` runtimes.
Workers can continue while the indexes build. Check `pg_index.indisvalid` for all three
`*_retained_success` indexes before starting the new scheduler. PostgreSQL 17 or later
is required for the five-second per-page transaction timeout.

Rollback requires stopping the new runtimes, downgrading to `0036`, then starting the
older binaries. Downgrade removes only the checkpoints and new indexes. It cannot restore
previously redacted inputs, catalogs or specialist outputs; durable job identities,
results, accounting and failures remain. Rolling back also restores the old retention
policy, so keep the scheduler stopped until that cost and policy are acceptable.

## Direct ClickHouse analytics (0038)

Revision `0038` follows deployed revision `0037`. It adds only the impression and
click receipt timestamps needed for delivery idempotency. It creates no analytics
queues, leases, projections or source capture triggers in PostgreSQL.

Prepare the ClickHouse schema before the new application accepts writes; the
Compose migration container performs PostgreSQL upgrade and schema-only ClickHouse
initialization. Start the new application and workers, drain old application
processes, then start the analytics history worker. Live application commits publish directly to
ClickHouse while the worker imports bounded historical pages. Its temporary guards
freeze only old analytics writes; business sources stay writable. Older analytic
endpoints fail fast during this handoff. Short NOWAIT metadata locks retry instead
of holding application writers for the duration of history import. The worker drops
legacy analytic tables, functions and search counters only after ClickHouse confirms
all history and current cards. Readiness remains false until this completes. There
is no ongoing PostgreSQL history polling after completion.

Back up ClickHouse for historical analytics. Direct application delivery is bounded
and best effort: a crash between a business commit and its ClickHouse insert can
lose that observation. There is no durable PostgreSQL outbox or automatic recovery
from complete ClickHouse data loss.

The earlier undeployed `0038`–`0044` draft revisions were removed. For a disposable
development database stamped with one of those drafts, stop its old processes and
inspect `partner_placement_deliveries` first. If both `impression_recorded_at` and
`click_recorded_at` already exist as nullable timezone-aware timestamps from the
former `0044`, run `uv run alembic stamp --purge 0038`. Otherwise, when neither
column exists, run `uv run alembic stamp --purge 0037` followed by
`uv run devfeed db upgrade`. Investigate a partial or different column layout
before stamping it. These commands change migration metadata only or add the two
operational columns; they retain old analytics for the automatic import. Do not
run this development repair on a deployed database.
