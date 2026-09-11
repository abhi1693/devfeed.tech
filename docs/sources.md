# Source profiles and review

Sources describe publications or link communities, not individual articles. Source
descriptions, branding and submission attribution are separate from article
metadata and from an aggregator entry's submitter (for example an HN username).
Readers suggest sources from `/sources/suggest` after signing in. Administrators review
them in the existing Sources section.

## Admission and trust

| Entry point | Initial decision | Background work |
| --- | --- | --- |
| `POST /v1/user/sources/suggestions` | Pending; polling on, awaiting approval | Profile enrichment; relevance assessment in full automation |
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

Signed-in users submit using `POST /v1/user/sources/suggestions` through the
same-origin user gateway, with their session cookie, Origin and CSRF token:

```json
{
  "feed_url": "https://example.com/feed.xml",
  "source_type": "publisher",
  "name": "Example Engineering"
}
```

The form looks up the feed name using authenticated `POST /v1/user/sources/suggestions/preview`
and leaves it editable before submission. Preview uses the same feed validation, creates
no source or job, and has a separate 20-lookups-per-hour limit. Changing the URL cancels
stale lookups; a completed lookup never overwrites a manually entered name.

Only `feed_url` and `source_type` are required. The optional name is limited to 200
characters and otherwise comes from the feed. HTTP(S) feed URLs are normalized and
limited to 2,048 characters. Validation reuses the bounded RSS/Atom fetcher, including
DNS, redirect, private-address, response-size, and parser protections. Invalid feeds
are rejected before persistence. Existing URLs return 409 without changing the source;
the database unique constraint covers concurrent submissions. Each account may make
five syntactically valid attempts per hour, enforced atomically in Redis before network
work. Redis failure closes admission temporarily.

Users cannot submit approval, polling, publication policy, profile metadata, or
attribution fields. Attribution comes from the session (user ID and name, no email).
The 201 receipt contains only ID, name, creation time and pending status. Anonymous
`POST /v1/sources` has been removed; public source reads remain available.

Suggestions start pending with polling enabled by default; ingestion still requires
approval. They use the existing source enrichment
outbox. With full automation and AI enabled, pending suggestions are dispatched to
the analysis queue, where the Codex worker checks readiness before taking work. Other
source profile jobs use the ingestion queue. The AI worker assesses up to ten recent feed
entries against DevFeed's shared developer scope. Automatic approval requires at least
three entries, confidence of at least 0.9, at least 80% relevant entries, no uncertain
entries, and valid verbatim evidence for every relevant classification. Sparse feeds,
unrelated content, uncertain results, malformed output and inference failures remain
pending. This is a model assessment, not proof of relevance; administrators can review
the stored sample, verdict and reason in Source details. No taxonomy is auto-created.

The general full-automation admission scan explicitly excludes user suggestions.
Enabling polling cannot bypass the relevance assessment. Assessment results are applied
only by the owning worker while the source is still pending and automation is enabled;
a concurrent rejection or manual decision wins. Approval records an attributed review
and queues ingestion through the existing review operation. When automation is enabled
later, unassessed suggestions are queued; failed jobs use existing retry controls.

Apply migration `0001` before starting the updated services.

`GET /v1/sources` and `GET /v1/sources/{id}` return only approved sources. User
profiles contain ID, name, type, description, website/logo/image URLs, language and
creation time. They omit submitter, feed URL, review notes and operational state;
pending/rejected detail requests return 404. Article source cards include the same
profile fields, while preserving historical provenance even after rejection.
Use the admin Sources section to inspect pending suggestions, relevance evidence,
and review history. The public source projections omit attribution and assessment data.

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

Source profiles, review history and enrichment jobs are part of `0001`.
This baseline requires an empty database; it does not upgrade the removed
pre-release chain or preserve its sources. Follow the
[reset and migration instructions](../migrations/README.md), then re-add sources.
Future revisions upgrade this baseline incrementally without routine resets.

Deleting a source also removes its profile jobs, including queued or running relevance
checks. Queued deliveries become no-ops and in-flight results are discarded after deletion.
Linked articles and active ingestion runs retain their existing deletion protections.
