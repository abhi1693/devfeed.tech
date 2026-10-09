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

### Optional adaptive source cadence

Adaptive polling is disabled by default. Configure the shared gate and bounds on
both scheduler and ingestion workers, restart them together, and explicitly opt in
sources through the admin UI or CLI. Apply migration 0023 first. See
[CLI polling controls](../cli/README.md#adaptive-source-polling) for policy tuning,
manual-fetch semantics, gate-off reconciliation bounds and a reproducible workload
comparison. Successful lease-owned ingestion commits source evidence and cadence
atomically; failures and upstream cooldowns retain their existing retry policy.
