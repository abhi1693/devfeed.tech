# Article image discovery

New publisher articles without a feed-supplied image get an independent, durable
`article_image_jobs` record in the same transaction as ingestion. The scheduler
dispatches it to RQ; existing workers process both feed and image jobs. No new
service, connection setting, login or source-specific adapter is required.

For aggregator sources, lookup targets the article's linked `canonical_url`, not
the HN/Lobsters discussion URL. Submission thumbnails remain origin evidence.
New aggregator records now use [article enrichment](articles.md), which discovers
image metadata in the same page fetch as text/author/language. It does not schedule
a second automatic image job. Existing/manual image jobs remain supported.

## Image selection and safety

1. Preserve any image already stored on the article or supplied by a publisher feed.
2. For missing images, fetch the linked HTML and inspect Open Graph image metadata,
   preserving declared image order and preferring its HTTPS alternative when supplied.
3. Fall back to Twitter-card images, then Article/BlogPosting/NewsArticle or WebPage
   JSON-LD image properties: URL, list, ImageObject and graph references.

Relative and protocol-relative URLs resolve against the final redirected page and
a usable HTML base URL. CDN query signatures are preserved. Unsupported schemes,
credentials, nonpublic literal addresses, local hostnames and unsafe ports are
rejected. Random body images, organization logos, author photos and favicons are
not used as automatic fallbacks.

HTML downloads reuse RSS protections: public DNS validation and pinning, checked
redirects, no HTTPS downgrade/environment proxies, compressed and decompressed size
limits, timeouts and a total deadline. Explicit non-HTML responses are rejected
before reading their bodies. Optional `DEVFEED_PAGE_MAX_BYTES` (default 2,000,000)
and `DEVFEED_PAGE_TIMEOUT_SECONDS` (default 15) independently bound page downloads.
The configured feed user agent is reused. JavaScript is not executed and remote
JSON-LD contexts are not fetched.

With image storage disabled, only the image URL is saved. We do not download, proxy, resize or verify image
bytes, dimensions, availability or display rights. No supported metadata means
`image_url: null`, not an invented placeholder. JavaScript-only or access-denied
pages may stay without images. Original-page preview/byline/publication-date
enrichment for aggregator records is handled by the separate article stage.

Metadata references: [Open Graph](https://ogp.me/),
[Schema.org Article](https://schema.org/Article),
[Schema.org ImageObject](https://schema.org/ImageObject).

## Setup and backfill

Stop old workers/scheduler before updating them. For a database on the removed
pre-release revision chain, follow the explicit reset instructions below first.
Apply the baseline and restart processes yourself. These commands do not start services:

```sh
uv run devfeed db upgrade
uv run devfeed images backfill --limit 100
uv run devfeed images jobs
```

The consolidated `0001` baseline includes the image-job table/indexes.
Old pre-release databases need an explicit reset before applying this baseline;
see the [migration workflow](../migrations/README.md). Schema creation does not fetch
pages, enqueue historical work or change existing article data. Backfill queues a
bounded batch (1–500), excluding articles with existing images or any previous
image job. Repeat to walk the remaining backlog. It works independently of RSS 304
responses and entries that have disappeared from a feed. Backfill does no network
I/O and does not need Redis to be available.

For individual articles and failed jobs:

```sh
uv run devfeed images fetch ARTICLE_UUID
uv run devfeed images fetch ARTICLE_UUID --force
uv run devfeed images jobs --article-id ARTICLE_UUID
uv run devfeed images jobs --status failed
uv run devfeed images show IMAGE_JOB_UUID
uv run devfeed images retry FAILED_IMAGE_JOB_UUID --force
uv run devfeed images dispatch QUEUED_IMAGE_JOB_UUID
```

`fetch` queues a lookup or reuses an active one. Existing images produce
`already_present: true`, `job: null`. `retry` only accepts failed jobs and preserves
their history. Use `fetch` to revisit a completed `not_found` lookup.

`--force`/`dispatch` publish a single queued job immediately, bypassing retry and
redispatch delays without stealing running leases or replacing images. They never
start workers or run a scheduler tick. Redis failure leaves a durable ready job.
An existing worker must consume the RQ message for work to execute.

Job output includes status, attempts, safe error, HTTP status, outcome, candidate
image URL and extraction method. Outcomes are `found`, `not_found`, and
`already_present`. In the latter race, the job may retain its candidate while the
article keeps the image another worker/publisher stored during HTTP lookup.
CLI `status` and API `/v1/admin/ingestion/status` include separate image-job counts/age;
`jobs` commands remain specific to RSS runs.

## Reliability and limitations

Page failures never change feed validators, RSS success state or source backoff.
Success only fills an empty `Article.image_url`; identity, metadata and feed order
are preserved. The result appears in existing `/v1/feed` and `/v1/articles/{id}`
responses. Reads never fetch pages.

One queued/running job per article is enforced by row locks and a partial unique
index. HTTP holds no database connection; lease ownership is checked before
writing. Duplicate delivery and expired-worker recovery reuse the ingestion
pipeline's 180-second RQ timeout, 300-second lease/message TTL/redispatch window,
three-attempt limit and 30/60-second delays. Transient transport, 429 and 5xx errors
respect bounded `Retry-After`; permanent failures stop immediately. No metadata
found succeeds without retry. RSS polls/backfill do not automatically repeat
completed or terminally failed image lookups.

The scheduler dispatches up to its batch limit separately for feeds and images
per tick, using the existing FIFO `ingestion` queue and workers. Slow image tasks
can delay later feed tasks. There is no per-host rate limiter or separate image worker pool. Recognized
challenge retries use the optional dedicated [solver worker](solvers.md). Bound
backfills and concurrency appropriately. Only job IDs go through Redis JSON serialization. PostgreSQL job
state is authoritative, including handled failures that return successfully to RQ.

Text logs report concise lookup/results with a short job ID; retry logs include
the due time. Logs never contain page bodies, article/image URLs or query tokens.


## Managed thumbnails in R2

Enable `DEVFEED_IMAGE_STORAGE_ENABLED` to extend the existing **Images** pipeline
with a storage stage. The existing `article_image_jobs` table, `images` RQ queue,
worker leases, retries and administration history handle both `discover` and
`store` operations. Migration `0013` adds operation/progress fields and ready-image
metadata to articles. Apply migrations before starting the updated services.

New feed-supplied images and images found by page enrichment automatically queue
storage jobs. Discovery still preserves publisher URLs. A storage job downloads
with the existing public-address DNS pinning, redirect checks and byte limits,
validates a supported raster image (up to 40 megapixels), and uploads its original.
Signed imgproxy requests generate WebP thumbnails at 320, 640 and 960 pixels,
capped at the original width. All files use content-addressed keys in **one bucket**:

- `originals/<sha256>`
- `thumbnails/v1/<sha256>/<width>.webp`

Originals and thumbnails in this public bucket are both publicly accessible,
matching the Shipyard storage model. Metadata records object keys instead of a
fixed public hostname. Changing `DEVFEED_IMAGE_PUBLIC_URL` does not require moving
objects; existing API caches expire normally. Production should use the bucket's
custom domain; the supplied `r2.dev` URL is for development.

The worker records the original and each completed variant in the existing job,
so retries resume after successful uploads. The article changes to managed URLs
only after every required size is ready and its publisher URL still matches.
The reader receives actual pixel widths through `image_variants`, using shared
components on the website and both extensions. Request handling never contacts
R2 or imgproxy. Turning storage off returns publisher URLs immediately after API
caches expire. Publisher URLs are always retained in the database.

### Development configuration

Set the image variables shown in `.env.example` in the ignored `.env`. The S3
endpoint is the account origin **without the bucket suffix**; bucket name is a
separate setting. Only the dedicated `images-worker` receives R2 credentials.
When storage is enabled, `all` and `background` workers leave the images queue to
that worker. Run with the `images` Compose profile (or start `images-worker`
explicitly with an external imgproxy endpoint). The bundled imgproxy has no host
port and only permits the configured bucket's `/originals/` source prefix.

Start with one Images worker. Backfills use the same queue, so enqueue small
batches between normal jobs to avoid delaying new images. Increasing worker count
also increases publisher/R2/proxy traffic; there is no cross-worker host limiter.

### Resumable backfill

```sh
uv run devfeed images backfill --store --limit 100
uv run devfeed images jobs
uv run devfeed images retry FAILED_JOB_UUID --force
```

Repeat the bounded backfill command to walk all existing article image URLs,
prioritizing published/recent articles. Active jobs and previous storage attempts
for the same URL are skipped, including terminal failures; retry failed jobs
explicitly. Redis loss does not erase progress: normal dispatch/recovery republishes
durable jobs. A new publisher URL is eligible for a new storage job.

This command imports article images; `images backfill` without `--store` continues
to discover missing article image URLs. Source/topic logos are not rewritten by
this article migration. Job cleanup may delete terminal progress according to the
existing retention policy; ready article metadata continues to prevent reuploads.

No automatic object deletion is performed. Old originals/variants can be shared
across articles; future garbage collection must check references before removing
objects. Review representative text-heavy thumbnails before changing `v1` quality
or size settings; a transform change needs a new version and explicit reprocessing.
