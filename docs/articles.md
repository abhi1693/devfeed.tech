# Original-page article enrichment

Aggregator RSS describes submissions, not the linked article. Ingestion keeps
those details in `origins[].source_metadata`, then saves an `article_enrichment_jobs`
row in the same transaction as the article/origin. New aggregator records do not
also create an automatic image job: the page lookup handles both text and images.

The existing RQ workers fetch `Article.canonical_url`, extract readable content
using [Trafilatura](https://trafilatura.readthedocs.io/en/latest/corefunctions.html),
and infer language with the existing offline detector. Extraction is generic;
there are no per-HN, Lobsters, publisher-domain or language adapters. No additional
connection environment variables or service processes are needed for page fetching.
Optional AI uses a separate queue/client; see [editorial workflow](editorial.md).

## What is saved

- Original page title, short plain-text preview (at most 500 characters), author,
  explicit timezone-aware publication timestamp, detected language and missing image URL.
- Prefer a meaningful page description for the preview; otherwise take an excerpt
  of extracted main text. Language comes from the readable main text rather than
  a translated headline/description when that text is available.
- Use at least 100 alphabetic characters of main text, or a public description
  with at least 40, before promoting text metadata. Language still has its own
  confidence/ambiguity checks. A page with no usable text can supply an image only.
- Exclude comments, code, forms, navigation, footer/sidebar and explicit hidden/
  no-snippet elements from the extraction sample. On pages declaring a paywall,
  use only a public description instead of the full body. These are heuristic
  extraction safeguards, not a universal content-quality classifier.
- Prefer explicit article JSON-LD authors (including graph references), then
  author meta tags, then extractor bylines. Submission handles never become authors.
- Accept `datePublished`/publication meta timestamps only when they have a timezone
  and are not in the future. Do not invent a time zone, use a modification date,
  scrape copyright years, or infer publication dates from URL paths.
- Apply legacy database-managed topic/tag rules only when AI is disabled and
  no reviewed classification exists. AI classification supersedes keyword guesses.

Raw HTML is discarded after extraction. Up to 60,000 characters of extracted text
are retained privately in `article_contents` for analysis, never in public article
responses. Job `result` stores bounded
preview metadata, final fetched URL, method, language score/basis and declared
language as evidence. Only IDs are sent through Redis. No image bytes are downloaded.

## Metadata precedence and consistency

`Article.metadata_source_type` is exposed on article responses:

| Value | Metadata origin | Priority |
| --- | --- | --- |
| `publisher` | Publisher RSS/Atom | Highest |
| `page` | Extracted original HTML | Middle |
| `aggregator` | Discovery-only submission | Lowest |

Source `source_type` still has only two choices. Page enrichment is not a source.
Page workers preserve nonempty publisher RSS metadata and fill missing fields,
even if it arrives during HTTP fetching. Later publisher RSS promotes page records, retaining
page fields where the RSS field is missing. Existing images are never replaced.
The article ID, canonical URL/hash, discovery time, `feed_at` and source-entry
provenance do not change. Redirect/canonical HTML hints do not merge identities.

Language backfill preserves non-null page-body detection; a short stored preview
may be in a different language. To revisit a page result, request another enrichment.
GETs never fetch pages. Changed article/taxonomy data invalidates cached public
responses after commit; job progress and unchanged rechecks do not clear them.

## Operation

Original-page enrichment is included in the consolidated `0001_initial` baseline,
along with [editorial publication](editorial.md#schema-cutover). Old development
databases require an explicit reset before this baseline. Run compatible
scheduler/workers yourself after initializing the schema. Then:

```sh
uv sync --all-packages --locked
uv run devfeed db upgrade
uv run devfeed articles backfill --limit 100 --dispatch
uv run devfeed articles jobs
uv run devfeed articles enrich ARTICLE_UUID --force
uv run devfeed articles retry FAILED_JOB_UUID --force
```

Backfill selects at most 500 (default 100) never-attempted articles of either source type, with
optional `--source-id`. It does not filter on image availability. Coalescing and
an active-job partial unique index prevent duplicate work for the same article.
`--dispatch` sends saved jobs immediately; otherwise the scheduler dispatches them.
An existing worker is required; these commands do not start services.

Inspect through the CLI or GET endpoints:

- `/v1/admin/ingestion/article-jobs?article_id=UUID&status=failed`
- `/v1/admin/ingestion/article-jobs/{job_id}`
- `/v1/admin/ingestion/status` and `devfeed status` include `article_enrichment_jobs` counts.
- `/v1/articles/{article_id}` and `/v1/feed` expose the enriched reader data.

Successful outcomes: `enriched`, `metadata_only` (image but no text), `not_found`,
`superseded` (publisher/identity changed), or `unapproved` (no approved origin remains).
HTTP errors are failed/retrying jobs, not `not_found` successes. Empty or terminally
failed lookups do not repeat automatically; `articles enrich` requests a fresh
lookup, and `articles retry` creates a new job for a failed attempt while retaining
history. `articles dispatch`/`--force` override due time but never steal a running lease.

## Reliability and limits

The guarded HTTP transport retains public DNS pinning, redirect validation, time
and wire/decompressed size limits, and rejection of non-HTML responses.
`DEVFEED_ARTICLE_PAGE_MAX_BYTES` defaults to 10,000,000 bytes and can be configured
between 1,024 and 20,000,000 bytes. The same cap applies to both the transferred
body and decompressed HTML. This accommodates pages with large scripts or embedded
application data without sending those bytes to AI: only extracted text, capped
at 60,000 characters, is retained. Responses over the cap fail without accepting
truncated content; the job error names the limit and setting. Image metadata
lookups keep the separate `DEVFEED_PAGE_MAX_BYTES` budget (2,000,000 by default).
Source website lookups use `DEVFEED_SOURCE_PAGE_MAX_BYTES` (10,000,000 by default).
`DEVFEED_PAGE_TIMEOUT_SECONDS` applies to all these HTML lookups.

After increasing the article budget, use **Retry all failed** on the article enrichment
jobs table or `devfeed articles retry FAILED_JOB_UUID --force`. A retry creates a
new run and keeps the failed run as history. The admin table excludes historical
failures that already have a replacement run from subsequent retries.

No JavaScript, browser/cookie login, anti-bot bypass or remote JSON-LD context fetch
occurs. Soft
login/challenge/error titles are rejected; protection schemes may still require
future extractor improvements. PDF/video/JS-only resources can remain link-only.

No DB connection is held during HTTP, DOM extraction or language inference. A
300-second lease and token fence writes; RQ execution has a 180-second timeout.
Claims and final writes recheck approved source origins. Source locks precede the
article lock to match ingestion, and a source rejected during fetching cannot
authorize the final write. Disabled-but-still-approved sources retain their
historical articles and remain eligible for enrichment.

Transport/429/5xx failures retry up to three attempts, normally after 30/60 seconds,
respecting bounded `Retry-After`. Permanent failures stop immediately. Crashed jobs
are recovered by the scheduler after lease expiry; lost Redis messages can be
redispatched. Page failures never alter RSS validators, source failures or polling.

The scheduler uses a separate batch allowance for page jobs on the existing FIFO
queue. There is no distributed per-host rate limiter or dedicated page-worker pool
yet; keep backfill size and concurrency bounded. Full-text retention/indexing,
browser rendering, relevance/quality ranking and semantic URL merging remain out
of scope. Operator job endpoints remain private/local while access control is deferred.
