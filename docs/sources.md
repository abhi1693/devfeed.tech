# Source profiles and review

Sources describe publications or link communities, not individual articles. Source
descriptions, branding and submission attribution are separate from article
metadata and from an aggregator entry's submitter (for example an HN username).
There are no accounts, login endpoints or admin UI in this workflow.

## Admission and trust

| Entry point | Initial decision | Background work |
| --- | --- | --- |
| `POST /v1/sources` | Pending | None until CLI approval |
| CLI `sources add` / `sources import` | Approved | Ingestion and profile enrichment when enabled |
| Existing source preserved by migration | Approved, legacy channel | Existing polling continues; enrich explicitly |

All entry points fetch and validate RSS/Atom before saving. A validation failure
saves nothing. Validation reuses the downloaded feed for the name and initial
profile; it does not ingest articles, fetch website HTML or save HTTP validators.

API callers cannot supply `approval_status`, `submission_channel`, review fields,
`enabled` or polling intervals. Unknown fields are rejected, not silently ignored.
Repeated API submissions return 409 without updating an existing source. Repeating
a CLI addition preserves the original submitter, metadata and review decision;
it never silently approves a pending/rejected source.

`enabled` controls polling separately from approval. Enabling a pending/rejected
source does not permit ingestion. Explicit approval enables polling and queues
ingestion and enrichment. Repeating the same decision is idempotent; it does not
append another review or restart jobs. Rejection requires a reason and disables
polling. Reapproval after rejection is supported and records a new review.

Queued ingestion jobs for a rejected source fail when claimed without contacting
the feed. Workers also recheck approval before committing fetched results. An
already executing HTTP request is not interrupted, but cannot import articles
after a rejection has committed. Existing articles and their source provenance
remain readable: source rejection is not retroactive article moderation.

## API payload and user profiles

Submit using `POST /v1/sources`:

```json
{
  "feed_url": "https://example.com/feed.xml",
  "source_type": "publisher",
  "name": "Example Engineering",
  "description": "Engineering articles from the Example team.",
  "website_url": "https://example.com/engineering",
  "logo_url": "https://example.com/logo.png",
  "image_url": "https://example.com/engineering/cover.png",
  "language": "en",
  "submitted_by": {
    "name": "Contributor",
    "profile_url": "https://example.net/contributor"
  }
}
```

Only `feed_url` and `source_type` are required. Names fall back to the feed title,
then the submitted hostname. Description is plain text, limited to 500 characters;
URLs are HTTP(S), at most 2,048 characters, with the existing public-URL restrictions.
Language codes are normalized to lowercase. `logo_url` is branding/iconography;
`image_url` is a preview/cover image, not an article thumbnail.

The 201 response is a submission receipt containing the ID, source profile,
`feed_url`, `approval_status: pending`, creation time and supplied attribution.
`submitted_by` is either null or `{name, profile_url, verified: false}`. It is a
self-declared claim, not a verified identity or proof of website ownership. Feed
authors are never used as source submitters. Channel (`api`, `cli`, `legacy`) is
assigned internally, independently of any claimed name. No emails/IP addresses are
collected. A future identity integration can add verified attribution explicitly.

`GET /v1/sources` and `GET /v1/sources/{id}` return only approved sources. User
profiles contain ID, name, type, description, website/logo/image URLs, language and
creation time. They omit submitter, feed URL, review notes and operational state;
pending/rejected detail requests return 404. Article source cards include the same
profile fields, while preserving historical provenance even after rejection.
Use the CLI to inspect pending submissions or review history. There is no public
status lookup or notification flow for an anonymous submitter yet.

Source PATCH, manual-fetch and approval/rejection routes are not exposed by the
API. This does not make the entire API safe to publish: taxonomy writes and
operational endpoints still lack authentication. Before exposing submission, use
an explicit route allowlist and edge request/concurrency limits; each validation
request performs bounded external network work. Render plain text as text and treat
external branding URLs as untrusted content in the future UI.

## Metadata discovery

Admission extracts feed subtitle/description, alternate website link, declared
logo/image/icon and language from RSS/Atom. Explicit caller fields take precedence.
New enabled CLI sources and newly approved API sources automatically receive a
separate source-enrichment job.

An enrichment worker retrieves the feed again without changing ingestion's
ETag/Last-Modified values. It uses the saved website or feed website link; if neither
exists, it tries the feed URL's origin homepage. Missing fields are filled from:

- Feed description first, then HTML description, Open Graph or Twitter description.
- Feed-declared logo/image/icon first, then declared Apple touch icon or icon links.
- Open Graph, Twitter or supported JSON-LD image metadata for the website preview.
- Feed language first, then the website's HTML language attribute.

There are no host-specific rules or guessed favicon paths. HTTP fetching shares
the feed transport's public-address checks, DNS pinning, redirect restrictions,
timeouts and response-size limits. Image URLs are stored, not downloaded, proxied
or verified for dimensions/availability. Sites without usable declarations retain
null fields; metadata discovery does not manufacture descriptions or logos.

Source website HTML uses `DEVFEED_SOURCE_PAGE_MAX_BYTES` (10,000,000 bytes by
default, configurable from 1,024 to 20,000,000). This accommodates script-heavy
homepages while retaining only the bounded profile fields. Both transferred and
decompressed bytes must fit; partial HTML is never treated as a complete lookup.
RSS/Atom still uses `DEVFEED_FEED_MAX_BYTES`, and image-only HTML lookups retain
`DEVFEED_PAGE_MAX_BYTES`. Oversized responses identify the failed resource, byte
limit and setting in the saved job error. After updating the worker, use **Retry
all failed** on the source enrichment jobs table to rerun failed lookups.

Workers fill only fields that were missing at claim and remain missing at commit.
They never rename sources or change submitters/review/polling state. A concurrent
website edit invalidates the fetched profile, so stale branding is not applied.
To replace an existing value, use an explicit CLI update; enrichment is not a
periodic metadata synchronization or overwrite operation.

Jobs use the existing RQ queue and PostgreSQL durable outbox, with one active job
per source, exclusive claims, a five-minute lease and stale-owner protection.
Transient failures retry up to three attempts with 30/60-second backoff (respecting
longer Retry-After); permanent failures stop immediately. Feed metadata found before
a website failure is retained. Job status, changed fields and errors are available
through the CLI; source records expose `metadata_enriched_at` and `metadata_error`
for operators. These do not affect ingestion success/failure counters.

## Operator workflow

```sh
uv run devfeed sources list --status pending
uv run devfeed sources show SOURCE_UUID
uv run devfeed sources enrich SOURCE_UUID --force
uv run devfeed sources approve SOURCE_UUID --by 'Operator' --note 'Reviewed'
uv run devfeed sources fetch SOURCE_UUID --force
uv run devfeed sources reject SOURCE_UUID --by 'Operator' --reason 'Not relevant'
uv run devfeed sources review-history SOURCE_UUID
```

`--by` is an optional operator-supplied label, not an authenticated reviewer identity.
Review rows retain decision, actor, note and timestamp. They are append-only through
application commands, not a tamper-proof audit log. No identity is invented when
the actor or original submitter is unknown.

An operator may enrich a pending source to help review; that does not approve or
ingest it. Rejected sources cannot be enriched. `--force` requests immediate RQ
publication, not replacement of metadata or interruption of a running job. It
requires an existing worker; it never starts a process. Without it, the scheduler
publishes the durable job normally. A Redis outage leaves the job ready for later
publication. After failure, repeat `sources enrich` to create a new run while
preserving the failed history.

```sh
uv run devfeed sources update SOURCE_UUID --description 'A short description'
uv run devfeed sources update SOURCE_UUID --logo-url 'https://example.com/logo.png'
uv run devfeed sources update SOURCE_UUID --clear-image-url
uv run devfeed sources enrichment-jobs --source-id SOURCE_UUID --status failed
uv run devfeed sources enrichment-dispatch ENRICHMENT_JOB_UUID
```

For CLI attribution, `sources add` accepts `--submitted-by NAME` and optional
`--submitter-url URL`. Re-adding a URL does not change attribution. Imports remain
trusted but do not invent a person's name from the OS user or feed author.

## Existing database

Source profiles, review history and enrichment jobs are part of `0001_initial`.
This baseline requires an empty database; it does not upgrade the removed
pre-release chain or preserve its sources. Follow the
[reset and migration instructions](../migrations/README.md), then re-add sources.
Future revisions upgrade this baseline incrementally without routine resets.
