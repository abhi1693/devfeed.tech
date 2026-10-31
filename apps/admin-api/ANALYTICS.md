# Administration analytics

Application analytics live in ClickHouse. PostgreSQL retains business records,
job execution, private evidence and transactional action validation. There are no
PostgreSQL analytical queues, projection tables, gauges or reporting counters
after the finite historical import completes.

## Application delivery

Application sessions collect narrow changed identities and publish asynchronously
after their business transaction commits. Typed SQLAlchemy writes use the shared
application session; maintenance SQL must explicitly publish affected identities.
Private layout and sign-in activity updates do not publish analytics.
The publisher reads current associated
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
Application delivery keeps a bounded in-memory queue and retries each failed
chunk up to three times with short background backoff. Acknowledged chunks are
not replayed when a later chunk fails. Business commits continue when ClickHouse
is unavailable. A process crash, full queue or exhausted delivery can still lose
an observation: there is no durable outbox, WAL replication or historical replay
service. Exhaustion logs only structured error classes and codes.

After bootstrap, the existing analytics process owns canonical operator-card
construction and repairs queued/running job observations. Direct application
delivery acknowledges compact overview and queue writes while holding the
publication fence; it does not build cards or expand a catalog change into
article-wide card work. The reconciler compares bounded source snapshots with
acknowledged ClickHouse fingerprints, republishes changes and tombstones missing
identities. Complete matching source fingerprints and canonical target hashes
avoid card rehydration. Missing or overflowing dependency proofs force a rebuild.
This works with priority pools and dedicated queue overrides alike.

RQ work horses use `DEVFEED_ANALYTICS_WORKER_DRAIN_SECONDS` (default 45, range
5–45 seconds) for their final synchronous drain, allowing the bounded publication
admission and retries to finish before the child exits. Ordinary process shutdown
retains its five-second default. Asynchronous drain callers offload that wait so
their event loop remains available. Business commit delivery remains best effort;
these bounds do not promise delivery after process termination or an outage.

Source snapshots join the PostgreSQL publication lock's wait queue so continuous
live publication cannot repeatedly bypass repair. Admission waits asynchronously
for at most ten seconds, then the repeatable-read snapshot and revision are
reserved before releasing the publication lock. Source queries keep their
five-second deadlines; each complete repair page has a twenty-second deadline.
Publication contention and an unfinished native card builder retry without
discarding an independent, valid active-job cycle. Unbuilt cards never advance
their cursor or fingerprint; cancellation and unrelated failures close the
coordinator and reacquire its source state on retry. Busy and failed pages log
structured reason and error-class codes without SQL or private parameters.

## Retained overview recovery

The same process independently reconciles retained publication, dimension, user,
membership, topic-decision and completed-job observations. Each operational source
has its own `overview:<source>` progress scope, fixed primary-key ceiling and
bounded cursor in the existing ClickHouse progress table. Publication recovery
receives separate turns, so its first acknowledgement does not wait behind a large
job history. These passes do not reset finite-import checkpoints, reader cutoffs,
pipeline policy or historical readiness, and never reopen retired analytics tables.

Pages read at most `DEVFEED_ANALYTICS_BACKFILL_BATCH_SIZE` narrow source identities
and row versions (maximum 1,000). Unchanged source and matching target fingerprints
skip hydration and publication. Publication associations and feed/membership
dependencies receive bounded metadata hydration so child deletion or a changed
feed computation cannot hide behind an unchanged parent row version. PostgreSQL
source transactions close before ClickHouse network I/O. Every destination write
acknowledges before its fingerprints, presence markers or page cursor advance.

Before submitting a repair INSERT, the source's progress row acknowledges a
bounded pending-page checkpoint containing source keys, safe target descriptors
and typed immutable attempt/decision observations. The source cursor remains
unadvanced. After interruption, a newer fenced snapshot rehydrates those keys;
missing mutable owners become higher-revision tombstones, while original immutable
facts retain their IDs, timestamps and revisions. The checkpoint clears only after
the resumed writes acknowledge. This also protects against a server-buffered old
INSERT arriving after cancellation, source deletion and process restart. Pending
checkpoints are limited to 1,000 source keys and two megabytes; they contain no
article bodies, prompts, credentials or arbitrary job results.

Mutable target identities have a per-cycle presence ledger in the existing
fingerprint table. After the finite source pass, bounded target pages remove
identities absent from that cycle. The initial publication fence bounds this
target universe, and orphan tombstones use that initial revision: a later valid
live publication wins even if it races between target selection and deletion.
Historical completed-job and topic-decision facts remain after operational row
retention removes their source. Deleted or reopened proposals do not erase a
completed decision; a later actual completion updates its stable observation.
Recovered jobs publish attempt facts only, never delayed queue rows; current queue
state remains exclusively ordered by its own repair protocol.

Recovery preserves publication, discovery, follow, computation and completion
timestamps and existing stable identities. It can restore the latest retained
completed attempt, not an earlier attempt overwritten before successful delivery.
Inference observations, reader opens and other direct-store events without a
retained source require their existing backups or acknowledged direct writes.
Missing usage or completion observations are not synthesized from aggregate job
totals or from the time recovery ran.

Publishing, processing, audience and coverage reports require their relevant
source acknowledgements before initial results are served. Missing acknowledgements
return `analytics_preparing`. Completed sources remain independent; later cycles
report `catching_up` while pending and `stale` when their acknowledged coverage
expires. The age bound is at least 600 seconds and otherwise twice the configured
reconciliation sweep interval. Historical import lag alone cannot certify these
reports as fresh. The administration cache namespace changes with this contract
so older empty reports do not bypass the new gates.

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
cache results are discarded. An incomplete import or policy returns
`analytics_preparing`, which the admin retries automatically. Query failures,
exhausted admission and deadlines return `analytics_unavailable`; the chart shows
an error with a Retry action. Safe server diagnostics include the section, error
class and structured ClickHouse error code, without SQL or parameter values.
Only Queue pressure continues polling after a successful response.

Overview accepts `from`/`to` date math or inclusive UTC dates. Exact resolved bounds
identify caches and filter data before aggregation. Distinct-reader sketches and
percentiles remain estimates; integer accounting remains exact.

Every Overview panel uses the selected period. Event panels filter their activity
timestamp: publication time, job completion, inference start, reader opens or
account creation. Publication latency measures the complete wait from discovery
for articles published in the period; selecting Today does not cap that wait at
24 hours.

Current-state panels use explicit period cohorts rather than lifetime totals:

| Panel                     | Period cohort                                                                                                                                       |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Review and recovery       | Current review items created in the period; source failures with source activity in the period                                                      |
| Queue pressure            | Jobs created in the period that are currently queued or running                                                                                     |
| Personalized feed health  | Feeds computed in the period, or accounts created in the period when no computation exists                                                          |
| Topic demand and coverage | Active explicit follows created in the period, current inferred interests belonging to feeds computed in the period, and publications in the period |

These current cohorts describe retained state, not a reconstruction of queue or
membership state at an earlier date. The UI resolves relative calendar ranges
once for all panels and invalidates them together at UTC midnight. Absolute
custom ranges retain their original dates.

Versioned audience metadata repair seeds actual account, feed and follow times
and membership user identities into existing ClickHouse observations. It advances
bounded primary-key pages only after acknowledged writes, protects newer live
publications, and resumes interrupted work. It neither resets finite import
checkpoints nor reconstructs immutable history. After completion, normal
application delivery maintains the metadata. A low-rate ClickHouse check detects
missing required metadata, including late writes from an older process during
rollout, and restarts bounded repair only when needed. Healthy metadata causes no
further PostgreSQL source scans. Feed and topic reports return `analytics_preparing`
until repair completes and required metadata is present, preventing partial
timestamps from appearing as complete zero counts. Inferred membership
queries join current feed computation times instead of rewriting every membership
when an unchanged feed refreshes.

`analytics_direct_status` contains historical readiness, the current card policy
and the fixed reader cutoff. A policy change requires bounded current-card import
before that policy is served. Operator actions always validate and lock current
business facts in PostgreSQL; an analytical observation never authorizes a write.
Current queues and boards use separate acknowledged reconciliation coverage.
Their timestamp describes covered source state rather than the time of an HTTP
request, the newest individual card or the one-time import-ready record.
Incomplete coverage is shown as catching up; expired coverage is stale. Existing
counts and cards remain visible during repair. Historical report readiness is
independent of these current-view checkpoints.

Each card population has its own `cards:<kind>` source and reverse-target cycle,
with an independent source boundary and fair repair turns. Article boards use
article coverage; Source boards combine source and candidate coverage. Aggregate
operations views require all selected populations, using their oldest verified
boundary and retaining any pending state. A large Article scan cannot hold a
Source board's verified boundary back. Missing population checkpoints remain
catching up; old whole-board or active-only checkpoints do not certify them.

The existing `cards` progress row remains the durable dirty-owner ledger. Upgrades
retain its queued owners, including historical identities outside the current
window. It is not a coverage checkpoint. Dirty-owner repair rotates across kinds
and completion of a population waits for its affected owners. Reverse-target
passes remain required to detect deletions and no-longer-current ghost cards.

An active or due queue snapshot takes one bounded page per turn until its coverage
is acknowledged, so slower card and overview builders cannot age an unfinished
queue snapshot. The queue poll interval starts at completion; card, overview and
audience work retain their weighted turns during that cooldown.

Card identity pages return text IDs but order by the qualified UUID column.
Ordering by the unqualified text output alias forces a sort of the remaining
eligible set before `LIMIT`, defeating bounded primary-key pagination. Preserve
the UUID ordering for every card kind and cursor.

Canonical point reads also constrain the immutable UUID-derived shard in the
`(policy,kind,shard,entity_id)` key. Keep `FINAL` so newer versions and tombstones
win. Canonical inserts validate that shard invariant before contacting ClickHouse;
invalid or partially valid batches are rejected without partial publication.

The reconciler retains one revision-validated catalog and its bounded ranking
cache across native builder pages. Every page still reads PostgreSQL catalog
revisions and current article/job inputs; a catalog edit discards the previous
generation before loading its replacement. This reuses preparation work, never
a publication or readiness decision. Both contexts run inside the existing
shielded native builder, whose exclusive ownership survives page cancellation.

Current-state work uses these resource bounds:

| Setting                                     | Default | Purpose                                                               |
| ------------------------------------------- | ------: | --------------------------------------------------------------------- |
| `DEVFEED_ANALYTICS_RECONCILE_BATCH_SIZE`    |   `100` | At most 200 identities per repair page                                |
| `DEVFEED_ANALYTICS_RECONCILE_POLL_SECONDS`  |    `30` | Active-job reconciliation cadence                                     |
| `DEVFEED_ANALYTICS_RECONCILE_SWEEP_SECONDS` |   `300` | Current working-set sweep cadence and interval between archival pages |

The working set includes nonterminal cards and terminal identities retained by
the boards' seven-day window. Changed active-job owners and stored active cards
receive separate repair turns. Older business identities advance through a
persisted archival cursor, one bounded page per sweep interval. Active-job
snapshots fail closed above 20,000 rows; fingerprint probes read at most 201 child
versions per dependency and owner, using the extra row to detect an incomplete proof.

Dashboard requests continue reading ClickHouse. They do not launch PostgreSQL
history scans or reconstruct whole boards from transactional tables. Retained
source recovery does not recreate already-lost immutable analytical history;
retain backups and monitor delivery-exhaustion and reconciliation failures separately.

## Finite historical import

`python -m devfeed_core.analytics_worker` checks the installed ClickHouse schema
and imports retained history automatically. It reads at most 1,000 PostgreSQL source rows
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
cycles and ready restarts do not replay immutable analytical history. Bounded
current-state and retained business-metadata sweeps continue independently. Marked import
code is removed in a later release under the
[retirement gates](ANALYTICS_BACKFILL_RETIREMENT.md).

## Compose and rollout

Compose runs persistent ClickHouse and the `analytics-importer` service, which
continues current-state reconciliation after its finite historical stage.
Their default budgets are two CPUs/2 GiB and 0.5 CPU/512 MiB respectively. Application
writers and the importer use bounded ingestion credentials; read and ingestion
profiles reserve separate capacity. Client limits must fit server profile limits.
Current-state analytics use session-level advisory coordination, so that process's
PostgreSQL URL must be direct or use session pooling. In Compose,
`DEVFEED_DIRECT_DATABASE_URL` overrides the application URL for analytics and
migration services when application traffic uses transaction pooling. Other
deployments can set `DEVFEED_DATABASE_URL` directly on these processes.
ClickHouse database ports stay private. CH-UI publishes port 4000 on
`DEVFEED_CH_UI_BIND_IP`; set `DEVFEED_CH_UI_URL` to its browser origin for LAN use.

Prepare ClickHouse and apply PostgreSQL schema `0039` and versioned ClickHouse
migrations through `0004` before starting new application producers. PostgreSQL
`0039` creates the two card dependency lookup indexes concurrently and recovers
interrupted builds. ClickHouse `0003` adds durable reconciliation progress and
fingerprints; `0004` adds the membership user identity used by bounded topic
cohorts. The migration container runs
`devfeed db upgrade` followed by `devfeed analytics upgrade` with schema-owner
credentials after ClickHouse becomes healthy. The latter applies the packaged SQL
history and fixes the reader cutoff under the import advisory lock; it never scans
history. Applied versions and SQL checksums live in
`analytics_schema_migrations` in ClickHouse. Ordinary application processes do not
create or alter tables. See the [schema upgrade guide](../../infra/clickhouse/README.md).
Then deploy
the new applications, drain old application/worker processes and start the
historical importer. New application
requests and direct writes remain available during import; analytical readiness
waits for complete history. The worker proves legacy analytics are frozen; it
cannot prove that an old pod stopped producing business changes after a source
page was scanned. Draining old processes is therefore required for the handover.
Do not put the full historical scan in a synchronous migration or readiness hook.

Permanently retire the Fleet Deployments `devfeed-read-model-pipeline` and
`devfeed-read-model-reports` before starting the importer. Remove their owning
manifests and Kustomize/catalog entries, together with any associated autoscalers,
scheduled hooks, probes and read-model alerts. Remove `DEVFEED_READ_MODEL_*`
configuration and dedicated database credentials, pooler and network policy once
their last consumer is gone. Updating the application image alone does not remove
old Deployments: they must not keep running or be recreated by GitOps. Compose's
equivalent `pipeline-projector` and `report-projector` services are removed; remove
their orphaned containers when updating an existing stack. The new runtime has no
read-model/report projector entrypoint, scheduled refresh job or PostgreSQL
analytical fallback. The analytics process retains bounded current-state and
retained-overview repair after its historical stage reaches the retirement gates above.

Back up ClickHouse together with import progress and readiness. After PostgreSQL
history is removed, an empty ClickHouse database cannot reconstruct deleted
history. Restoring an older ClickHouse snapshot can recover only observations
whose business sources remain retained; later direct-store observations need
another backup. Reconciliation is not a historical archive restore.

For local databases stamped with an earlier undeployed draft revision, follow the
metadata-only development repair in [the migration guide](../../migrations/README.md).
No development repair changes a production database.
