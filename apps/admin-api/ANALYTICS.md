# Administration analytics

Application analytics live in ClickHouse. PostgreSQL retains business records,
job execution, private evidence and transactional action validation. There are no
PostgreSQL analytical queues, projection tables, gauges or reporting counters
after the finite historical import completes.

## Application delivery

Application sessions collect narrow changed identities and publish asynchronously
after their business transaction commits. The publisher reads current associated
inputs in bounded batches, builds sanitized observations and writes directly to
ClickHouse. Reparenting and deletion publish both affected identities. Private
article bodies, prompts, evidence, notes and credentials remain in PostgreSQL.

Completed job attempts retain distinct identities. Current publications, feed
inputs and operator cards use replacing observations with increasing revisions;
replaying the same observation does not increase totals. Feed deadlines and queue
availability are evaluated when queried instead of maintaining timer queues.

Reader opens, inference invocations, successful searches and partner delivery
measurements use dedicated direct stores. Partner receipt timestamps are short
lived operational replay claims, not aggregate counters. Reader raw open records
expire after 30 days; ingest materialized aggregate states preserve lifetime totals
and daily audience estimates.

Writes use bounded batches and acknowledged ClickHouse asynchronous inserts.
Application delivery keeps a bounded in-memory queue. Business commits continue
when ClickHouse is unavailable. A process crash, full queue or failed delivery
can lose an observation: there is no durable outbox, WAL replication,
retry volume or periodic historical reconciliation.

## Current cards and reports

Five card kinds cover articles, sources, discovery candidates, topic proposals
and relationship proposals. Builders hydrate at most 200 identities per batch.
`analytics_card_canonical` stores each policy/kind/shard/entity's latest card or
tombstone. Queries filter typed fields before fetching limited page payloads;
counts and pages read current ClickHouse observations. URI and private metadata
hydration remains separate and bounded.

The supported query API accepts allowlisted analytical query identifiers and
validated parameters. It does not accept browser-supplied SQL. Related overview
charts share section calculations, resolved UTC windows and cached responses.
Each process owns one asynchronous client with bounded query admission. Expired
cache results are discarded; query or readiness failures return the retryable
`analytics_preparing` response. The admin retries preparing results automatically.
Only Queue pressure continues polling after a successful response.

Overview accepts `from`/`to` date math or inclusive UTC dates. Exact resolved bounds
identify caches and filter data before aggregation. Distinct-reader sketches and
percentiles remain estimates; integer accounting remains exact.

`analytics_direct_status` contains historical readiness, the current card policy
and the fixed reader cutoff. A policy change requires bounded current-card import
before that policy is served. Operator actions always validate and lock current
business facts in PostgreSQL; an analytical observation never authorizes a write.

## Finite historical import

`python -m devfeed_core.analytics_worker` initializes direct stores and imports
retained history automatically. It reads at most 1,000 PostgreSQL source rows
or 200 current card identities per page. Source
transactions close before ClickHouse network writes. Durable ClickHouse
checkpoints advance only after acknowledged writes; failed pages replay safely.

The worker installs temporary rejection guards on old analytics relations and
removes old calculation/capture triggers under short NOWAIT metadata locks. These
guards stop old analytics writes while business tables remain writable. Old
analytic endpoints can fail fast during the handoff. Outbound legacy foreign keys
are removed so a business deletion cannot erase frozen historical rows.

The import preserves retained invocations, reader totals and opens, search totals,
partner measurements, retained processing jobs and closed daily audience
history. It also imports current business snapshots. Current card pages have a policy-specific checkpoint so changing a
policy does not replay immutable history.

After all pages finish, the worker removes legacy PostgreSQL analytics tables,
functions, sequences and search counters, then publishes readiness. Subsequent
cycles and ready restarts perform no PostgreSQL historical polling. Marked import
code is removed in a later release under the
[retirement gates](ANALYTICS_BACKFILL_RETIREMENT.md).

## Compose and rollout

Compose runs persistent ClickHouse and the finite `analytics-importer` service.
Their default budgets are two CPUs/2 GiB and 0.5 CPU/512 MiB respectively. Application
writers and the importer use bounded ingestion credentials; read and ingestion
profiles reserve separate capacity. Client limits must fit server profile limits.
ClickHouse database ports stay private. CH-UI publishes port 4000 on
`DEVFEED_CH_UI_BIND_IP`; set `DEVFEED_CH_UI_URL` to its browser origin for LAN use.

Prepare ClickHouse and apply schema `0038` before starting new application
producers. The migration container initializes ClickHouse schemas and fixes the reader cutoff under the import
advisory lock with ingestion credentials and waits for ClickHouse health; it never scans history. Then deploy
the new applications, drain old application/worker processes and start the
historical importer. New application
requests and direct writes remain available during import; analytical readiness
waits for complete history. The worker proves legacy analytics are frozen; it
cannot prove that an old pod stopped producing business changes after a source
page was scanned. Draining old processes is therefore required for the handover.
Do not put the full historical scan in a synchronous migration or readiness hook.

Back up ClickHouse together with import progress and readiness. After PostgreSQL
history is removed, an empty ClickHouse database cannot reconstruct deleted
history. Restoring an older ClickHouse snapshot also loses later observations
unless those observations are available from another backup. No automatic archive
or reconciliation service is introduced.

For local databases stamped with an earlier undeployed draft revision, follow the
metadata-only development repair in [the migration guide](../../migrations/README.md).
No development repair changes a production database.
