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
