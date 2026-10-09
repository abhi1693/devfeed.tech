# Aggregator

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

## Managed publisher logos

Publisher logos reuse the guarded topic-logo importer: bounded public-URL fetching,
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
