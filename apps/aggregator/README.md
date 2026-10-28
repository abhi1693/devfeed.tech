# Aggregator

## Source processing and rollout

Source profile lookup and source relevance assessment are independent operations in
the existing source-enrichment outbox. Profile work uses guarded feed/website fetches;
assessment uses its saved bounded sample on the source-analysis queue. Retrying a
failed assessment keeps the completed profile. Already-queued assessments obey the
AI kill switch before claiming an attempt. Profile, relevance and logo completion,
plus the pending-review scheduler, reconcile the same admission checks, so completion
order cannot admit a source with unfinished required work.

For migration `0032_source_processing`, quiesce source dispatch and drain old coupled
source workers before migrating and deploying the new workers/API. Resume dispatch
only after all source and image workers understand split operations and admission
reconciliation. Legacy queued jobs are converted to profile-only when the new worker
claims them; existing source decisions and normal approved-source polling remain.
Do not run old coupled workers alongside split-operation deliveries: they can repeat
AI calls and bypass the new completion boundary. Before rollback, drain all queued or
running profile/relevance jobs and resolve explicit paused processing requests; the
migration refuses downgrade while either remains. Deploy old workers only after that
downgrade. The migration follows schema `0031` and must complete before the upgraded API
and workers start.

Explicit Profile, Assessment or Branding retries pause new ingestion on an approved
source until an operator selects Resume after processing settles. Existing articles,
approval and fetch history are retained. A fetch that was already running discards
its results if the source was paused before commit. An ingestion retry only recovers
the feed request and does not pause the source. Ordinary metadata refreshes do not
pause approved polling, and reads never backfill or revoke existing sources.

## Article decisions and recovery

Full automation rejects a pending article when its latest successful, current analysis
explicitly identifies unrelated content or a non-article page. An incomplete analysis can
support that rejection without applying missing publication fields. Content, editorial
revision, source approval and catalog checks still protect the decision. Publication
continues to require a complete applied analysis. Negative classifications do not generate
additional topic research. Explicit publisher paywalls are rejected
in every automation mode, independently of the AI content date window.

Extraction retains up to 60,000 characters, including code, separately from the smaller
language-detection sample. Thin publisher redirect pages require a matching visible link;
each target uses the guarded article fetcher, with a two-hop limit. A page description never
replaces a longer publisher feed summary as analysis evidence.

Article analysis `article-analysis-v9-specialist-steps` runs separate typed tasks for
developer relevance, page eligibility, source language, content type, content format,
topics, tags, title, overview and search description. Each prompt has one job, its
own version and required context. Matching tasks receive their respective catalog;
description generation uses the accepted overview. Identifying source titles skip
title inference. Eligibility checks stop unnecessary work on negative content.

The parent job persists each task's validated result and bounded retry state. A later
failure reuses completed work instead of sending the whole article through every task
again. The worker renews its lease before each call and permits a bounded 1,500-second
RQ run; calls hold no database transaction. Article fields apply together only after
the complete aggregate validates. Admin shows each task and its current retry/error.

The overview task requests concrete source-supported findings, mechanisms, comparisons
and limitations. Its length stays proportionate to the source within 1,200 characters,
without filler or invented facts. Sparse evidence remains insufficient rather than a
reason to expand a teaser. Contract/transport tests use deterministic responses;
semantic output quality still requires reviewing generated samples.

After a deployment, pending unpublished candidates with an older prompt version can
receive a current analysis through the normal scheduler. The prompt change does not
regenerate already-published overviews or itself request a production reanalysis.

The scheduler performs one recovery with the corrected extractor for old description-only,
empty, or 2,500-character extractions whose articles remain incomplete. Enrichment jobs carry
an extraction version from creation, so even terminal failures cannot repeat this recovery.
Legacy title-validation failures similarly receive one run under the corrected validator;
the request records its validation version and reason. Ordinary unchanged insufficient
evidence does not trigger additional analysis.

The administration overview distinguishes paywalls, negative classifications, incomplete
extraction, unresolved evidence, source eligibility and content date exclusions. Topic gaps
require catalog evidence; another analysis with the same catalog does not resolve them.
Missing content and uncertain classifications remain reviewable. Roll out the article workers
before the scheduler so recovery runs use the corrected extractor and validator. Update the
admin API and admin interface with that release for the corresponding blocker descriptions.

## Analysis payload retention

The scheduler maintains one durable sweep per article/topic analysis history. Ordinary
ticks read two checkpoint rows; only a due sweep reads job history. Each page first reads
up to `DEVFEED_JOB_PAYLOAD_SCAN_BATCH_SIZE` old successful unpruned identities (1,000 by
default), then checks review/provenance/verification protections for those identities.
It redacts at most `DEVFEED_JOB_PAYLOAD_PRUNE_BATCH_SIZE` payloads per history (100 by
default). Job rows, results, hashes and accounting remain; only duplicate input payloads,
article catalogs and specialist output bodies are removed.

The checkpoint advances through protected rows as well as removable rows. Full pages
continue after `DEVFEED_JOB_PAYLOAD_PAGE_INTERVAL_SECONDS` (30 seconds); an exhausted
sweep starts again after `DEVFEED_JOB_PAYLOAD_SWEEP_INTERVAL_SECONDS` (one hour).
`DEVFEED_JOB_PAYLOAD_RETENTION_DAYS` remains 30 days. The age cutoff is fixed for each
sweep so new completions cannot extend a pass indefinitely; increasing retention days
immediately tightens that cutoff on a resumed page. A row completed late behind
the cursor, a locked row, or a newly eligible reviewed row is reconsidered next sweep.
Revisit delay therefore includes the remaining full pass and its cooldown.

Each page locks its checkpoint and commits payload rewrites and progress together.
Concurrent schedulers skip an owned page; a crash or timeout rolls it back for retry.
The page uses a two-second statement timeout, 100-millisecond lock timeout and
five-second transaction timeout on PostgreSQL 17+. Review/provenance rows are locked
before checking protections, and history locks skip active work.
New foreign-key references from ingestion can still use the locked parent; writes
to its review/provenance fields remain serialized. Queued, running,
failed, leased and young jobs remain intact, as does each subject's latest retained
successful result, even if a newer attempt failed. Pending article/proposal reviews,
current article provenance, queued/running research verification and pending relation
proposals also retain their evidence.

At defaults the maximum redaction rate is 288,000 jobs per history per day before
scheduler delay and contention. A pass over permanently protected history examines
at most 1,000 identities per history every 30 seconds. Monitor committed checkpoint
progress and `job_payload_retention_page` counts, database latency/WAL and ingestion
freshness under the deployed workload. These are capacity limits, not a measured
production throughput guarantee. Increase budgets only after representative measurement;
changing intervals affects subsequent committed pages. Reducing retention days applies
on the next sweep; increasing them also protects inputs during the current sweep.
See [migration and rollback](../../migrations/README.md#analysis-payload-retention-0037).

## Personal feed preparation

The scheduler runs a lightweight recommendation dispatcher independently of its
main ingestion cycle. It checks durable due work every second, prioritizes first
feeds, and enqueues those first builds ahead of routine ingestion. Existing feeds
retain their recurring refresh cadence. Both dispatcher paths use the same row
locks and delivery IDs, so concurrent scheduling coalesces safely; failed dispatch
leaves durable work available for retry.

`recommendation_refresh_dispatched` reports due-to-dispatch `wait_ms`, and
`recommendation_refresh_started` reports `queue_wait_ms`. Completion reports
`duration_ms` and article count in `recommendation_refresh_completed`. These
separate scheduling and queue delays from computation; they are not a promised
user-facing ETA. End-to-end production latency still requires observing these
logs alongside browser requests after deployment.

### Optional adaptive source cadence

Adaptive polling is disabled by default. Configure the shared gate and bounds on
both scheduler and ingestion workers, restart them together, and explicitly opt in
sources through the admin UI or CLI. Apply migration 0025 first. See
[CLI polling controls](../cli/README.md#adaptive-source-polling) for policy tuning,
manual-fetch semantics, gate-off reconciliation bounds and a reproducible workload
comparison. Successful lease-owned ingestion commits source evidence and cadence
atomically; failures and upstream cooldowns retain their existing retry policy.

## Partner catalog processing

A separate one-second dispatcher refills partner import, qualification and evaluation work
without waiting for the main scheduler's ingestion cycle. Each physical queue has its own
`DEVFEED_SCHEDULER_BATCH_SIZE` publication budget (100 by default). Partner publication pauses
when a queue has two batches waiting and resumes as workers consume them; other publishers
can also add work to these shared queues. Worker concurrency controls actual execution.

Qualification and evaluation alternate first access to their shared analysis queue. Each
refill reserves a quarter of its slots for delivery recovery, with the rest for new work;
either path can use the other's empty share. Redis connections are reused within a batch.
Queue-specific publication locks and job row locks protect retries without holding the
catalog lock during Redis I/O. Failed
publication remains in the PostgreSQL outbox and unique delivery IDs prevent duplicate work.
The main scheduler retains bounded dispatch as a fallback and schedules source imports and
qualification renewal separately from the fast refill loop.

`partner_pipeline_dispatched` and `partner_evaluations_dispatched` count fallback publication
in scheduler tick logs; `partner_pipeline_refilled` and `partner_evaluation_refilled` report
the independent dispatcher. These counts measure publication, not completed imports or a
promised processing time for a large catalog.

## Managed publisher logos

Publisher and topic logos share a guarded importer: bounded public-URL fetching,
sanitation and rasterization of SVG, a normalized 96px transparent master, and lossless
WebP canvases at 16/32/64/96px. Content hashes identify immutable objects; changing a
source's logo URL schedules a replacement. Readers retain the last completed managed
logo while a replacement fails or retries. Clearing the URL removes the logo. Sources
not yet imported retain their publisher URL and its third-party cache policy.

Apply migration 0023 and deploy all Images workers with source-logo support before
queuing these jobs. Enable the existing image-storage/imgproxy configuration, then run
`uv run devfeed images backfill --sources --limit 100` in bounded batches. Repeating the
backfill skips completed assets and URLs already attempted; retry terminal failures with
`images retry JOB_ID`, or request a source explicitly with `images source-logo SOURCE_ID`.
Use `images source-logo SOURCE_ID --refresh` when the artwork changes at the same URL;
content hashes give changed bytes a new identity while readers retain the previous asset.
Existing dispatch, leases, checkpointing, and retry limits apply. Do not roll workers back
while source-logo jobs remain queued/running; drain them first. Migration downgrade removes
source-logo jobs and metadata, while stored immutable objects remain harmless.

The shared reader supplies each rendered logo size (12px feed cards, 23px previews,
30px catalogs/source pages) so browsers choose by DPR without publisher preconnects.
Run `tests/benchmarks/source_logo_encoding.py` against saved publisher PNGs with local
imgproxy, then `node tests/benchmarks/source-logo-delivery.mjs` for matched input,
viewport/DPR, byte-transfer, and warm-cache reports. Optional input sidecar JSON records
`source_url` and the measured `cache_control`; absent TTLs use an uncached baseline. Set
`DEVFEED_LOGO_LIGHTHOUSE=1` for matching mobile/desktop Lighthouse JSON and HTML reports. Review-only inputs, reports, and
screenshots stay under ignored `reports/source-logos`.

## Thumbnail encoding and recompression

New article thumbnails use WebP quality 70 at 320/480/640/768/960px widths,
under `thumbnails/v2/<original-sha256>/<width>.webp`. The managed-image metadata
schema remains v1; `thumbnail_version` separately identifies the encoder. Legacy
assets/checkpoints without that field retain q78 and `thumbnails/v1/...` keys.
Both namespaces remain readable, with one-year immutable cache headers. Existing
objects are never overwritten or deleted, and source, byte, pixel, animation and
transport limits remain unchanged. Topic-logo encoding is unchanged.

The 480px and 768px variants fill common card-width/DPR gaps; the maximum stays
960px. Small originals are never enlarged, and duplicate capped widths are omitted.
Product thumbnails retain their existing three-width policy. Legacy v1 jobs retain
their original sizes and encoder. Existing v2 objects keep their immutable keys
and bytes; the additional sizes use new keys in the same encoding namespace.

After deploying **all Images workers**, upgrade stored v2 articles in bounded batches:

```bash
uv run devfeed images resize --limit 100
```

This command reuses stored originals and completed variants, queues at most 500
articles per invocation, and performs no publisher HTTP or storage I/O in the CLI.
Published/recent articles are processed first. Active and previously attempted
resize jobs are skipped; retry terminal failures explicitly with
`uv run devfeed images retry <job-id>`. The worker adds missing widths and publishes
the sorted variant list with `thumbnail_layout: cards-v1` only after all sizes
finish. Processing or failure leaves the published list intact. A changed source
URL prevents the old job from replacing the image. Do not run the resize command
against workers that predate this policy. No resize batch is scheduled automatically.

The reader sizing change also works with older three-width lists immediately;
the additional widths become available only as workers process new or upgraded
articles. Reader rollback can consume either list. Do not delete saved originals
or existing thumbnail objects as part of this upgrade.

Deploy the Images worker changes completely before scheduling an upgrade:

```bash
uv run devfeed images recompress --limit 100
```

This bounded command queues eligible v1 articles with saved originals, prioritizes
published/recent articles, skips active and previously attempted v2 jobs, and performs
no HTTP/storage work in the CLI. Workers resume from saved originals/checkpoints
and publish v2 metadata only after all widths are ready. Readers retain v1 during
processing or failure. Use `images retry <failed-job-id>` for an explicit retry;
completed widths remain checkpointed. Repeating the batch drains eligible work
without automatically retrying terminal failures. A changed source URL cannot be
replaced by an old recompression job. Do not roll back to workers that lack v2
support while v2 jobs remain queued or running. Reader rollback can still consume
both URL versions because the metadata schema is unchanged.

The article pipeline board can explicitly retry image discovery or storage even
when an article already has a usable image. These jobs carry an `article_refresh`
marker in their existing storage metadata. Workers preserve the current URL and
managed variants until a current discovery result or complete replacement asset
can apply; captured URL checks prevent late results replacing a newer image.
Failed refreshes remain publication blockers and use the existing finite retries
and checkpoints. Deploy the updated image workers before using the board's forced
image retries: older workers ignore this marker and can short-circuit the request
as `already_present`. This change adds no queue or database schema.

For a repeatable encoder comparison, mount representative local inputs read-only
into the pinned Compose imgproxy image with `IMGPROXY_LOCAL_FILESYSTEM_ROOT`,
allow `local:///` only in that isolated benchmark container, and run
`uv run python tests/benchmarks/thumbnail_encoding.py`. It compares q78/74/70/66
at identical widths using the actual imgproxy encoder, writing byte counts,
encoding wall time (including transport), decoded-image error and review outputs
under ignored `reports/thumbnail-encoding/`. Keep production source restrictions
and signed URLs unchanged. `node tests/benchmarks/thumbnail-delivery.mjs` compares
q78/v1 and q70/v2 with fresh Lighthouse mobile and desktop profiles; it expects
`photo`, `screenshot`, and `text` outputs from the encoder benchmark. These isolated
fixtures measure compression independently of responsive variant selection, not
production page performance. The photographic benchmark used
[an Unsplash landscape](https://images.unsplash.com/photo-1501785888041-af3ef285b470?w=1600&q=95);
the UI screenshot and text example were captured locally. Review artifacts are
not repository assets and must stay untracked.

## Cached sitemaps

The scheduler starts a dedicated sitemap refresher immediately and checks for
due work every minute, independently of ingestion cycles. Keep the scheduler
running alongside the public API. Crawler requests only read completed snapshots
from shared Redis/Valkey; they never scan PostgreSQL or wait for generation.

`DEVFEED_SITEMAP_REFRESH_SECONDS` defaults to 900 (15 minutes).
`DEVFEED_SITEMAP_RETENTION_SECONDS` defaults to 86400 (24 hours): a failed refresh
keeps the last completed inventory available until that retention expires. A shared
lease allows only one builder across scheduler replicas, and publication verifies
all parts before atomically replacing the manifest. Visibility changes appear on
the next successful refresh; article pages still enforce current public visibility.

After successful publication, the previous generation's parts remain readable for
five minutes. Unpublished parts expire after six minutes. This bounds storage
without retaining every generation for a full day. Active parts outlive the
manifest by one minute. Redis operations use a dedicated Sentinel-aware pool,
three-second operation timeout, and independent circuit breaker.

A completely cold cache, incompatible snapshot format, or sitemap-cache outage
returns a retryable 503 until background generation succeeds. During rollout,
verify `/sitemap.xml` and a listed part return 200 before declaring the sitemap
ready. No schema migration is required.

## Partner assessment diagnostics

Qualification validation failures retain fixed messages for malformed responses, missing
capability evidence, evidence URL or quotation mismatches, generic technology categories,
and technologies absent from the evidence. The durable job error and terminal product
assessment reason carry the safe message. Job payloads retain `diagnostic.code` and
`diagnostic.stage`; retry/failure logs include `diagnostic_code` and `assessment_stage`
alongside the existing job, product and attempt identifiers. Successful retries clear
the stored diagnostic.

Unknown failures retain a generic message and stage. Raw model responses, page text,
credentials and arbitrary exception messages are excluded from these diagnostics. Evidence
rules, qualification gates and retry timing/limits are unchanged. Existing failed jobs
are not rewritten or retried automatically.

## Shared worker pools

`compose.workers.yaml` opts into fewer worker processes without combining the
busy article, notification, image or solver workloads. Use images built from
this revision or later; older images do not recognize the new queue groups.
The original Compose topology remains available when the override is omitted.

```sh
docker compose -f compose.yaml -f compose.workers.yaml --profile workers --profile ai up -d
```

For local source builds, append `-f compose.build.yaml` before the profile
options, then build before starting the services. Both pools use the full backend
image, which contains the enrichment and discovery dependencies. Do not substitute
the smaller dedicated discovery image for the pipeline pool.

The override stops the generic and superseded dedicated consumers and starts
four article-analysis workers, one AI-maintenance worker, one relationships
worker, two pipeline workers, one Images worker and one notifications worker:
ten consumers in Compose. Keep the existing isolated solver worker in Kubernetes
for an eleven-pod production topology, compared with fifteen previously.
The scheduler, search indexer and temporary analytics importer are separate.

- `devfeed-worker --queue ai-maintenance` consumes topic analysis, research
  verification and source analysis. It never consumes the article backlog.
- `devfeed-worker --queue pipeline` consumes ingestion, source discovery, both
  fresh/ordinary article-enrichment lanes and source enrichment. It never
  consumes AI analysis, notification, solver or image deliveries.
- Both pools use the existing round-robin dequeue policy, durable deliveries,
  retry behavior, health markers and graceful shutdown. Four article-analysis
  consumers preserve the three dedicated slots plus the former general AI slot;
  fresh and ordinary lanes retain round-robin service.

Only the Images worker receives image-storage credentials. Notification management
credentials and solver endpoints stay on their existing consumers. Pooling does
not require broader credentials or a new physical queue; pending deliveries remain
on their original queue and can be consumed by either topology during transition.
Topic admission counts a live maintenance pool while busy, but does not reserve a
busy worker shared between article and topic analysis for both families.

For Kubernetes, release and verify the new backend image first, then update Fleet:
set article-analysis to four replicas; replace the general AI and three quiet AI
specialists with one `ai-maintenance` consumer; replace ingestion, discovery and
article/source enrichment with two `pipeline` consumers. Keep relationships,
images, notifications and solver at one replica each. Carry over each pool's
existing network grants for PostgreSQL, Valkey, ClickHouse, Codex or public fetches
as applicable; retain the native worker-health probes and 1,560-second drain grace.
Remove credentials and network grants belonging only to superseded workloads.
This application PR does not change the live Kubernetes deployments.

Baseline completed jobs per hour, oldest queued-job age, fresh-article publication
latency, retries, resource use and provider cooldowns before changing the topology.
Compare representative ingestion and AI bursts after rollout; fewer pods alone is
not proof of equal throughput. Keep article capacity at four, and restore separate
consumers or raise pool replicas if queue age or publication latency regresses.
A rollback restores the original deployments and their replica counts; queue names,
job identities, leases and retry routing do not change. This is static consolidation;
autoscaling is a separate change requiring burst and scale-down validation.
