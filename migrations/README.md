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
that Alembic does not generate. Keep applied revisions frozen; each revision contains
its own schema definition and never imports mutable runtime models.

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

## 0032: remove product exposure limits

Removes the partner-account per-reader exposure setting and its stored values. Product
recommendations and sponsored placements no longer have a per-reader daily cap or repeat
cooldown. Campaign budgets, catalog relevance, attribution and historical measurements remain
unchanged. Apply before starting runtimes requiring schema `0032`; no production rollout is
implied. Downgrade recreates the older setting with its original default of three, since removed
values cannot be recovered.

## 0033: advanced partnership audiences

Adds a non-null empty account audience and a nullable campaign audience. Null campaign criteria
inherit the account; an explicit empty object means all readers. Existing inventory, attribution
and reporting retain their behavior. Platinum and Diamond enforce populated criteria at issuance;
other tiers preserve stored settings without delivering targeted inventory.

Apply before starting runtimes requiring schema `0033`. Downgrading pauses accounts and campaigns
with populated or unrecognized audience criteria before dropping the new fields, preserving
inventory and measurements while preventing an older runtime from broadening a restricted audience.
Empty criteria and inherited unrestricted campaigns remain active. No production rollout is implied.

## 0034: native advertising formats and product settings

Adds independent feed, homepage sponsorship, sponsored content and engagement formats, available
from Silver. Existing accounts, campaigns and attribution assets retain the feed format. Account
automatic formats default to feed only; per-product nullable format/location allowlists inherit
account settings, while empty lists disable delivery. Issued receipts retain their placement
location for live policy checks. No per-reader exposure limit is introduced.

Sponsored-content campaigns reference approved articles and store the article identity in the
immutable attribution asset. Permanent article sponsor names survive campaign expiry. New format
and article identities receive separate assets, preserving historical counters and receipts.
Apply before starting all runtimes requiring schema `0034`.

Downgrade pauses accounts with new format or product restrictions, pauses non-feed campaigns and
assets, and unpublishes disclosed articles before dropping the disclosure field. Silver accounts
using the new inherited Sponsored default are also paused. Non-feed managed campaigns and automatic
assets become inactive historical records to satisfy the older unique indexes; their IDs and
measurements remain. Format/location settings and sponsor names cannot be recovered after downgrade.
Before restarting an older runtime, clear reader caches and rebuild the search index so removed
articles cannot be served from a previous cached response. Review paused accounts and unpublished
articles explicitly before reactivating them. No production rollout is implied.

## 0035: bounded automatic promotion enrollment

Adds `partner_promotion_enrollments`, with one durable product cursor per account and an index
for due work. Catalogs with at most 200 products enroll during policy saves; larger catalogs
queue enrollment in the same transaction. Each scheduler pass advances one queued account by
at most 200 products, with separate bounded repair work for imported inventory. Inserts use
500-row chunks. Retries resume committed progress, policy changes restart the sweep, and
future campaign starts schedule another pass. Existing assets and reporting are preserved.

Apply before starting runtimes requiring schema `0035`. Downgrading removes pending enrollment
jobs only; automatic assets, campaigns and tracking metrics remain. No production rollout is implied.
