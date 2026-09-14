# Source discovery

All entry points use the same source lifecycle:

`Add Source / user suggestion / CLI / import → pending Source → feed discovery or validation → source review → approved or rejected`

Every new source is stored in `sources` with `approval_status=pending`. A publisher without a
known feed has a nullable `feed_url` and stays disabled until a feed is found and the source
is approved. Import is only another discovery input, not a separate review system.
OPML feed URLs are validated as hints. Discovery prefers a usable Atom feed, then RSS,
and saves the selected URL on the pending source for the common detail and edit views.
A feed URL remains empty until discovery succeeds; finding a feed does not approve the source.

Apply migration `0010` with `uv run devfeed db upgrade` before starting this version. It creates
the discovery tables, supports sources without a known feed, and queues existing pending sources
for review. The unreleased migrations have been consolidated into this single revision.
A development database already at an earlier draft of `0010`–`0013` must be checked against the
final schema before stamping it to `0010`, or recreated from scratch. Rebuilding containers alone
does not reset a persistent database volume.
Downgrade requires completing or deleting sources that still have no feed URL.

## Common administration

Use **Content → Sources → Add** for a feed URL or publisher website. **Import** accepts a collection
URL, uploaded file, or pasted OPML, JSON, Markdown, or URL list. Duplicate entries are ignored.
The import form returns to the normal Sources table; imported sources use the same details,
Pending filter, edit, review, row deletion, bulk selection, and delete confirmation as every source.
Deleting a source also removes its discovery work and history; approved duplicate sources are
preserved when discovery finds an already-known feed.

No entry point grants approval merely because a feed is valid. In full-auto mode, the existing
source-analysis worker validates the feed and vets its recent content. Approval requires at least
three sampled entries, confidence of 0.9 or higher, at least 80% developer-relevant entries,
complete grounded evidence, and no uncertain entries. Sparse or inconclusive evidence stays
pending for manual review. Outside full-auto mode, approval is manual. AI failures retain the
normal source-review retry and capacity handling. Turning full-auto on resumes pending reviews;
repeated scheduler passes neither duplicate active work nor restart exhausted failures.

Keep the scheduler, ingestion workers, and source-analysis workers running. Discovery only finds
feeds; source enrichment/review handles every validated source. CLI discovery runs may also be
processed explicitly. The crawler selects the first discovered valid feed in discovery order
(hints, advertised feeds, then conventional paths); alternate feeds remain diagnostic evidence.

## Operator workflow

```sh
uv run devfeed discovery add https://go.dev/blog/ --name 'Go Blog'
uv run devfeed discovery import publishers.opml --format opml --origin engineering-list
uv run devfeed discovery run --limit 10
uv run devfeed sources list --status pending
uv run devfeed discovery show CANDIDATE_UUID
```

Source UUIDs are available from `discovery show` as `source_id`. Use the normal source review
commands or admin interface. The compatibility `discovery approve` and `discovery reject`
commands delegate to the same source review operation. `discovery assess` queues the normal
source-review job; AI execution follows the system mode. Existing source settings and review
decisions are preserved when discovery finds a duplicate feed. Article publication remains its
own workflow; source approval does not directly publish articles or create taxonomy.

`run` prints each candidate's name, stage, attempt, completion state, feed count,
and elapsed time to stderr. A heartbeat appears every ten seconds while a job is
running. Final JSON remains on stdout, so it can be redirected separately:

```sh
docker compose exec -T api devfeed discovery run --limit 20 > discovery-results.json
```

Use `--quiet` to suppress progress. Existing running commands keep their original
behavior; progress is available on subsequent runs using the updated container.

## Seed collections and imports

```sh
uv run devfeed discovery seeds add https://example.org/publishers.opml --format opml
uv run devfeed discovery seeds list
uv run devfeed discovery seeds sync SEED_UUID
```

Supported formats are `opml`, `urls`, `json`, and `markdown`, limited to 1 MB and
1,000 publisher entries per import. JSON accepts an array of objects with
`homepage_url`, optional `name`, and optional `feed_url`. Markdown extracts explicit
HTTP(S) links, excluding GitHub navigation links; inspect the resulting candidates.
OPML rejects DTDs and entities. Neither repository files nor publisher scripts run.

Feed URLs are hints: the crawler validates them rather than trusting the collection.
Publication identity retains the full host and path, preserving separate Medium or
other hosted publications. An OPML entry without `htmlUrl` retains its exact feed
URL as candidate identity. This conservative approach can produce duplicate
candidates for one publisher; admission deduplicates on the final feed URL.
HTTP/HTTPS aliases and different feeds for one publisher are not automatically merged.

Repeated imports preserve provenance without creating duplicate active jobs or
reopening rejected candidates. `--origin` identifies the collection; seed imports
also record its ID and content checksum. Seed sync is explicit, not scheduled.

## Crawl behavior and recovery

Each candidate gets at most 20 actual HTTP requests, including robots requests and
redirects, at most 14 candidate URLs, and a 120-second crawl budget. The shared
transport applies connection/read timeouts and compressed/decompressed body limits
(2 MB for discovery responses, 1 MB for robots). It retains DNS rebinding, private
address, redirect, credential, and HTTPS downgrade protections. There is no solver
fallback or JavaScript execution.

The crawler inspects up to five imported feed hints, homepage RSS/Atom alternate
links, explicit feed links, and at most two same-host blog/news pages. It tries a
small conventional-path list only while no valid feed has been found. It retains
multiple valid feeds instead of arbitrarily admitting the first one.

Robots matching uses [Protego](https://github.com/scrapy/protego) for wildcards,
end anchors, and longest-match precedence, with allow winning equal-length ties.
Robots rules apply to content requests and redirected targets, using the DevFeed
agent token. Requests wait at least one second per host and respect crawl delay
and request rate. Missing robots files (404/410) allow access; denial or inaccessible
robots prevents content access. Rate limits stop requests to that origin for the
remaining crawl. Retry-After contributes to the durable retry delay.

Only one crawler runs at a time across CLI processes. Source review uses its own existing worker queue.
A lease lasts 30 minutes so interrupted processes can recover; normal network and
AI timeouts are much shorter. Automatic transient retries use exponential backoff,
up to five attempts. AI timeouts, unavailable transports, rate limits, usage limits,
and server overload also receive durable retries, retaining safe error codes and
provider retry delays. Invalid output and policy violations are terminal.
`retry` brings a queued attempt forward or creates a new job
when the previous job finished. A running job must finish before selection, retry,
or admission. Rejection is immediate and prevents a running result overwriting it.

`show` returns candidate state, feed evidence, provenance, and up to 100 recent
assessments/jobs per collection. `list` supports bounded `--limit` and `--offset`.
The tables are `discovery_seeds`, `source_candidates`, `candidate_discoveries`,
`candidate_feeds`, `candidate_assessments`, and `source_discovery_jobs`.

## Discovery evidence and remaining scope

Discovery retains feed URLs, article samples, recency, and publisher-ownership diagnostics.
These are evidence, not a separate approval status. Historical diagnostic quality assessments
remain readable; new review requests use the common source-review workflow described above.

GitHub topic enumeration, ecosystem adapters, blogroll traversal, outbound-article graph mining,
and automatic seed schedules remain future work. Broad recursive crawling is not enabled.
