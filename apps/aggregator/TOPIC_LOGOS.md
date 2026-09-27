# Managed topic logos

Migration `0020` adds managed topic assets and topic targets to the existing Images
jobs. Set the same R2/imgproxy settings used for article images on the Images worker;
public/user APIs need `DEVFEED_IMAGE_PUBLIC_URL`. Saving or researching a changed
`logo_url` schedules processing transactionally when storage is enabled. Reads never
fetch third-party logos. The admin keeps the original URL editable and shows the
processed logo, processing status, and related Images jobs for inspection/retry.

Raster inputs are orientation-corrected, stripped of metadata, and bounded to a
96-pixel master. SVG inputs are sanitized: scripts, external references, embedded
images, DTDs/entities and unsupported active elements are rejected. CairoSVG converts
sanitized SVGs to PNG before upload. Explicit renderer dimensions enforce the
96-pixel bound even with CSS size overrides. The production image includes Cairo
and fonts; local Images workers also need the Cairo system library. Both paths save a bounded
PNG master with transparency and aspect ratio preserved. A later job delivery uses
imgproxy to read that saved master, center it on a square transparent canvas, and
encode lossless WebP at **32, 64, 96**
pixels. These cover 22–30 CSS pixel list icons at normal/high pixel density and
36-pixel card icons plus exports (up to 96 pixels). No 128/256 variants are stored.
Reader `srcSet`/`sizes` select the small variants; cards choose 96. Server card exports
embed the finished WebP bytes without resizing or recompressing logos.

- `originals/topic-logos/v1/<sha256>.png` (normalized, bounded master)
- `topic-logos/v1/<sha256>/{32,64,96}.webp`

Identical normalized content shares objects even across different source URLs.
Uploads skip existing keys and retries resume checkpointed variants. Originals
remain available for retries without repeatedly contacting the upstream host.
After the original is saved, the job is requeued with a fresh due time and a separate
variant retry budget. Older due imports in a bounded backfill run before requeued
variant work; retries and concurrent workers do not impose a global stage barrier.
The saved original is displayed if no variants are available. PNG and previously
saved sanitized SVG originals can also be embedded directly in card exports. A completed
previous logo stays visible during replacement processing.
Objects have immutable one-year cache headers. No automatic object deletion is
performed; reference-aware cleanup is needed before removing shared old objects.

Recognized browser challenges use the isolated solver queue when
`DEVFEED_SOLVER_QUEUE_ENABLED` is enabled on the Images worker. Configure
`DEVFEED_SOLVER_SERVICES` on the solver worker; R2 credentials stay on the Images
worker. For FlareSolverr, the solver worker retrieves the binary image using the
returned browser cookies and user agent. It must share FlareSolverr's public egress
for IP-bound clearance. Failed challenges remain visible in Images jobs; solving is
not guaranteed. Clearance cookies are never stored in job results or image metadata.

### Public assets and cutover

Use a Cloudflare R2 custom domain for CDN caching. Configure **public read CORS** on
this image bucket so the website and browser extensions can include logos in canvas
exports. For example, apply [`image-bucket-cors.json`](image-bucket-cors.json) through R2's S3 API or
Dashboard. It permits unauthenticated GET/HEAD from any origin; it does not grant
write access. Purge any already-cached responses after changing CORS. Verify a logo
response includes `Access-Control-Allow-Origin` with an Origin request header.

```sh
uv run devfeed db upgrade
uv run devfeed images backfill --topics --limit 100
uv run devfeed images jobs
uv run devfeed images retry FAILED_JOB_UUID --force
uv run devfeed images topic-logo TOPIC_UUID
```

Run the bounded backfill repeatedly until no jobs are queued, and inspect failed
jobs explicitly. Start updated Images workers/scheduler before backfilling. Configure
CORS and finish this backfill before rolling out the reader that removes the old logo
export proxy. Until an original is saved, topics show their normal fallback icon;
original third-party URLs are never returned to readers. A failed replacement keeps
the last completed logo visible; clearing the source removes it. Turning storage off
stops new jobs but keeps serving already-completed logos.
