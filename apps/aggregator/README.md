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

## Thumbnail encoding and recompression

New article thumbnails use WebP quality 70 at the existing 320/640/960 widths,
under `thumbnails/v2/<original-sha256>/<width>.webp`. The managed-image metadata
schema remains v1; `thumbnail_version` separately identifies the encoder. Legacy
assets/checkpoints without that field retain q78 and `thumbnails/v1/...` keys.
Both namespaces remain readable, with one-year immutable cache headers. Existing
objects are never overwritten or deleted, and source, byte, pixel, animation and
transport limits remain unchanged. Topic-logo encoding is unchanged.

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
