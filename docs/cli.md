# Core CLI

For topic profiles, AI analysis, manual classification, approve/reject and
publish/unpublish commands, see the [editorial operator workflow](editorial.md).

`devfeed` works directly with PostgreSQL and Redis. FastAPI does not need to be
running, and there is no login or account setup. Install it with
`uv sync --all-packages --locked`, then run `uv run devfeed --help`.

The CLI uses [Typer](https://github.com/fastapi/typer), with typed command groups,
UUID/choice/range validation, contextual help, and optional shell completion.
Existing command names, options, JSON output and exit codes are preserved.

```sh
uv run devfeed --help
uv run devfeed sources add --help
uv run devfeed articles --help
uv run devfeed --version
uv run devfeed --show-completion
```

`-h` is also supported. `--show-completion` only prints the completion script for
the detected shell. If you want to install it, explicitly run
`uv run devfeed --install-completion` and follow its shell restart instructions;
installation edits your shell configuration. Nothing installs automatically.

Help, version and completion do not load connection settings or contact services.
Typer's local-variable traceback display is disabled; operational errors keep the
application's sanitized diagnostics on stderr, separate from JSON on stdout.

Every operational command requires explicit `DEVFEED_DATABASE_URL` and
`DEVFEED_REDIS_URL` settings, from the shell environment or `.env`. There are no
fallback connection URLs. Help is available without configuration. Run from the
repository root so the expected `.env` is loaded.

## Start the pipeline

Configure `DEVFEED_DATABASE_URL` and `DEVFEED_REDIS_URL` in `.env` first, pointing
to PostgreSQL and Redis instances you manage. There are no separate connection
variables or automatic service provisioning. The commands below are manual steps
to use when you are ready to run the pipeline.

```sh
uv run devfeed db upgrade
uv run devfeed sources add 'https://blog.python.org/feeds/posts/default?alt=rss' --type publisher
```

Run these in separate terminals:

```sh
uv run devfeed scheduler
uv run devfeed worker
```

The common worker defaults to all enabled queues (ingestion, AI analysis when
enabled, and notifications when enabled), using round-robin fairness. Use
`--queue ingestion`, `--queue analysis`, or `--queue notifications` only when
explicitly dedicating a worker. See [Chimely setup](notifications.md).

These commands stay in the foreground. Use Ctrl+C to stop them. Start additional
worker processes to process multiple feeds concurrently; an optional `--name`
must be unique among running workers. `--max-jobs N` exits after N jobs.
RQ's worker process and shutdown behavior are described in its
[worker documentation](https://python-rq.org/docs/workers/).

The scheduler is required: it polls due sources, publishes durable database jobs
to RQ, and recovers interrupted work. A worker alone does not dispatch submitted
feeds or schedule retries. RQ's own scheduler is not used for feed polling.

For a bounded pass, dispatch first and then drain currently queued work:

```sh
uv run devfeed scheduler --once
uv run devfeed worker --burst
uv run devfeed status
```

A burst worker exits when its queue is empty. It does not wait for future polling
or delayed retries. A successful worker exit does not prove every feed succeeded;
inspect database job status with `devfeed jobs list`.

The Dockerfile includes the same CLI and runtime commands. Its default command
starts the API; `devfeed worker` and `devfeed scheduler` are separate process
commands for a future deployment setup. Container connection URLs must use hosts
reachable from inside the container. No service orchestration is included.
The legacy `devfeed-worker` and `devfeed-scheduler` entrypoints remain available.

## Submit and configure feeds

```sh
uv run devfeed sources add 'https://example.com/rss' --type publisher --name 'Engineering' --poll-interval 1800
uv run devfeed sources add 'https://hnrss.org/frontpage' --type aggregator
uv run devfeed sources import publisher-feeds.txt --type publisher
uv run devfeed sources import - --type aggregator < aggregator-feeds.txt
uv run devfeed sources list --enabled --limit 50 --offset 0
uv run devfeed sources list --type aggregator
uv run devfeed sources show SOURCE_UUID
uv run devfeed sources update SOURCE_UUID --name 'New name' --poll-interval 3600
uv run devfeed sources update SOURCE_UUID --disable
uv run devfeed sources update SOURCE_UUID --enable
uv run devfeed sources fetch SOURCE_UUID
uv run devfeed sources fetch SOURCE_UUID --force
```

`SOURCE_UUID`, `JOB_UUID`, `CATEGORY_UUID` and `TAG_UUID` below are placeholders:
replace them with IDs returned by the commands. Quote URLs containing shell
metacharacters such as `&` or `?`.

`--type` is required for both `add` and `import`: use `publisher` for feeds
describing their own content (DEV.to, engineering blogs), and `aggregator` for
feeds describing submissions linking to resources (HN, Lobsters). The same type
applies to every URL in one import, so split mixed lists by type. Source type is
not inferred from names or hostnames and has no default. Types appear in source
list/show output. Type changes are not supported until reprocessing is available;
`sources update` changes name, profile metadata, interval and enabled state.

Submission normalizes the URL, downloads it and parses the response before storing
a source or creating a durable ingestion job. It requires HTTP 200 and readable
RSS/Atom, rejecting unreachable URLs, HTML pages and feeds with entries but no
usable articles. Valid empty feeds are accepted. Validation uses the same public
network restrictions, redirects, timeouts and size limits as worker ingestion.
An omitted or blank `--name` uses the RSS channel/Atom feed title from that same
validation request; imports resolve each feed's title independently. Feed titles
are cleaned to plain text, whitespace-normalized and limited to 200 characters.
Only a missing or unusable feed title falls back to the submitted URL's hostname.
An explicit name takes precedence. The API accepts omitted, null or blank `name`
on creation with the same behavior. No extra HTTP request is needed for naming.
The polling interval is 300–604800 seconds.

## Enrich aggregator articles

```sh
uv run devfeed articles backfill --limit 100 --dispatch
uv run devfeed articles backfill --source-id SOURCE_UUID --limit 50
uv run devfeed articles enrich ARTICLE_UUID --force
uv run devfeed articles jobs --status failed
uv run devfeed articles jobs --article-id ARTICLE_UUID
uv run devfeed articles show ARTICLE_JOB_UUID
uv run devfeed articles retry FAILED_ARTICLE_JOB_UUID --force
uv run devfeed articles dispatch QUEUED_ARTICLE_JOB_UUID
```

These commands save or dispatch durable RQ work; they never fetch pages in the CLI
or start services. Backfill selects never-attempted articles of either source type, even if
they already have images, and requires an approved source origin. Repeat bounded
batches to cover the backlog. Terminal failures and empty-page results are not
automatically retried by another backfill/RSS poll. Explicit `enrich` requests a
fresh lookup; `retry` only accepts failed jobs and preserves their history.

`--dispatch` publishes a saved backfill immediately. `--force` and `dispatch` make
queued jobs due now, without stealing running leases. Without immediate dispatch,
the scheduler picks them up. Redis failure leaves the database job queued.
Publisher lookups now fill missing fields and collect private analysis evidence
while retaining existing RSS metadata. See [article enrichment](articles.md).

## Detect article languages

```sh
uv run devfeed articles detect-languages --limit 100 --dry-run
uv run devfeed articles detect-languages --limit 100
uv run devfeed articles detect-languages --limit 100 --after ARTICLE_UUID
uv run devfeed articles detect-languages --source-id SOURCE_UUID --dry-run
```

The first command previews inferred languages and changes without writing. The
second applies them to the existing `articles.language` field, including incorrect
non-null RSS hints. This is an offline, bounded maintenance pass, not a service or
an RQ worker. Future ingestion performs the same inference automatically in RQ.
No migration or language-specific settings are needed.

`--limit` is 1–500 (default 100). If `next_after` is non-null, pass it to `--after`
to process the next batch, retaining the source filter if supplied. IDs are ordered
ascending, so null/uncertain records cannot trap repeated backfills on the first
page. New concurrent inserts may require another pass from the beginning. Rerunning
unchanged records writes nothing and does not clear the response cache.

Output includes scanned/detected/unknown counts, `would_change`, `updated`, and
`concurrent_changes_skipped`, plus each article's previous/inferred language,
relative confidence score, text source and reason. The score is not a calibrated
probability. Source profile and origin metadata languages are left alone.
See [language detection](languages.md) for the inference policy and limitations.

Failed validation exits with code 2, writes an error to stderr and creates no source
or job. Submission now waits for the publisher response. The API applies the same
check and returns HTTP 422 with `detail`, `upstream_status` and `retryable` on failure.
Article ingestion and classification still happen in workers; validation does not
save articles or conditional-fetch headers. A successful check proves current
readability, not future availability, so workers retain retry/error handling.

New CLI additions and imports are trusted and approved. API submissions are pending
until reviewed through the CLI. Repeated CLI submissions of the same type validate
the feed again and reuse the existing source without overwriting its profile,
submitter, review decision, interval or enabled state. An active job is reused;
otherwise an enabled, approved source gets a new run. Existing pending or rejected
sources return `job: null`; resubmission is not approval. `created` distinguishes
new from existing sources in the output.
Use `sources update` to change existing settings explicitly.
Submitting an existing URL with a different type exits with code 2, without
changing its type or creating a job. An import containing that conflict rolls back
the whole batch.

`sources add --disabled` also validates the feed, then creates a source without
scheduling ingestion. Disabled sources stay disabled when resubmitted and return
`job: null`. Disabling polling does not delete articles or cancel a job already executing.

Import files contain one URL per line. Blank lines and lines beginning with `#`
are ignored. Imports accept at most 1 MB and 1,000 non-comment lines, normalize and
deduplicate URLs, fetch and parse each unique feed before writing, and commit
atomically. If any feed fails validation, nothing from the batch is saved. Network
checks run sequentially, without holding a database transaction, so large imports
can take time.
Concurrent submissions cannot create duplicate source rows or active jobs. Import
does not support OPML yet. A Redis outage does not prevent durable submission;
the scheduler dispatches pending jobs once connectivity returns.

Plain `sources fetch` queues a new run or returns the active run; it does not reset
that run's retry delay or five-minute redispatch cooldown. For immediate broker
publication use `sources fetch SOURCE_UUID --force`. It reuses a queued job or
creates one if no run is active, then publishes that job to RQ without waiting for
the scheduler. A running job is rejected, not interrupted or duplicated.
This overrides scheduling, not HTTP caching: existing conditional-fetch validators
remain in use.

## Source profiles and review

```sh
uv run devfeed sources list --status pending
uv run devfeed sources approve SOURCE_UUID --by 'Operator' --note 'Relevant engineering content'
uv run devfeed sources reject SOURCE_UUID --by 'Operator' --reason 'Not relevant'
uv run devfeed sources review-history SOURCE_UUID
uv run devfeed sources update SOURCE_UUID --description 'Engineering news' --website-url 'https://example.com'
uv run devfeed sources update SOURCE_UUID --logo-url 'https://example.com/logo.png' --language en
uv run devfeed sources update SOURCE_UUID --clear-image-url
uv run devfeed sources enrich SOURCE_UUID --force
uv run devfeed sources enrichment-jobs --source-id SOURCE_UUID
uv run devfeed sources enrichment-dispatch ENRICHMENT_JOB_UUID
```

Approval enables the source and queues ingestion and profile enrichment. Use
`sources fetch SOURCE_UUID --force` afterward for immediate ingestion dispatch.
Rejection requires a reason, disables the source and prevents future ingestion,
including commits from in-flight fetches. Existing articles are retained.
`--enable` is a polling switch, not an approval shortcut.

Profile flags accepted on both `add` and `update`: `--description`, `--website-url`,
`--logo-url`, `--image-url`, `--language`. Updates also accept their `--clear-*`
counterparts. Additions optionally accept `--submitted-by NAME` and
`--submitter-url URL`; these are unverified attribution, not accounts.

Enrichment fills missing fields only; use `update` to change existing values.
`--force` dispatches a queued job immediately, never overwrites profile data or
steals a running lease. Repeating `enrich` reuses an active job, or creates a new
job after a previous run completed/failed. Pending sources can be enriched through
this explicit operator command without approving or ingesting them; rejected
sources cannot. See [source profiles and review](sources.md) for metadata selection,
review history, migration and public API details.

## Inspect and retry jobs

```sh
uv run devfeed jobs list --status failed
uv run devfeed jobs list --source-id SOURCE_UUID --limit 20
uv run devfeed jobs show JOB_UUID
uv run devfeed jobs retry JOB_UUID
uv run devfeed jobs dispatch JOB_UUID
uv run devfeed status
```

Retry accepts failed jobs only. It preserves the original failure record and
requests a new run for the source, or returns its existing active run. Re-enable
a disabled source before retrying it; pending/rejected sources require explicit
approval. Automatic transient retries remain the
scheduler's responsibility.

`jobs dispatch JOB_UUID` immediately publishes an existing **queued** job. It
overrides `available_at` (including retry/Retry-After delays) and the redispatch
cooldown, preserving the job ID, attempt count and error history. Use this explicit
override deliberately; normal polling/retries retain their backoff. Failed jobs
must first go through `jobs retry`; dispatch the returned new job ID if you also
need immediate publication. Completed and running jobs cannot be dispatched this way.

Both immediate commands commit the durable override before contacting Redis. If
publication fails, the CLI exits nonzero but the job remains ready for the scheduler
or another manual dispatch. Targeted publication never schedules unrelated sources,
starts processes, or marks the scheduler heartbeat healthy. An existing worker must
be running to execute the job; immediate dispatch does not guarantee immediate
execution when workers are busy. Duplicate transport deliveries are safe under the
existing job claim/lease guards. A `queued` response with a new `dispatched_at` means
it has been published but not yet claimed by a worker.

Status reports database/Redis availability, source/article/job counts, source review
counts, separate source-enrichment and article-image job counts, oldest
active-job time, queue depth, scheduler heartbeat freshness and registered worker
names, states and heartbeats. `dependencies_ready` describes dependencies, not
pipeline progress; also inspect `scheduler_healthy` and worker heartbeats. Worker
registration is not proof that an individual ingestion succeeded.

## Article image URLs

Missing images on new articles are discovered automatically in separate RQ jobs.
Existing article data can be backfilled without refetching RSS feeds:

```sh
uv run devfeed images backfill --limit 100
uv run devfeed images fetch ARTICLE_UUID --force
uv run devfeed images jobs --status failed
uv run devfeed images show IMAGE_JOB_UUID
uv run devfeed images retry FAILED_IMAGE_JOB_UUID --force
uv run devfeed images dispatch QUEUED_IMAGE_JOB_UUID
```

Apply migrations and reload existing workers/scheduler before using the new stage.
`backfill` queues at most 500 never-attempted missing images; repeat to process more.
`fetch` uses an article ID; `show`, `retry` and `dispatch` use an image-job ID.
`--force` means immediate RQ dispatch, not image replacement. These commands do
not start any service. See [image discovery](images.md) for full behavior and limits.

## Topics and secondary tags

Topics are the single subject catalog. Names and aliases are never seeded.
Use JSON for explicit topic creation and replacement:

```sh
uv run devfeed topics add --file topic.json
uv run devfeed topics update TOPIC_UUID --file topic.json
uv run devfeed topics list
uv run devfeed topics relate TOPIC_UUID RELATED_UUID --relation part_of
uv run devfeed tags add --name 'Custom runtime' --slug custom-runtime --alias runtime-alias --topic-id TOPIC_UUID
uv run devfeed tags update TAG_UUID --clear-aliases --clear-topic
uv run devfeed tags list
```

Topic files require `name`, `slug`, and `kind`; optional fields include `aliases`,
`keywords`, `description`, `website_url`, `logo_url`, and `facts`. Topic update is
full replacement. Tag updates preserve omitted fields; repeated aliases replace
that list. Value and clearing options are mutually exclusive.

For supervised bulk imports, GitHub discovery and keyword enrichment, use the
admin [Topics workflow](topics.md). Article analysis only selects existing topics;
new topics come from manual additions or reviewed GitHub proposals.

## Schema and output

GET response caching can be invalidated independently of the database and queue:

```sh
uv run devfeed cache clear
```

This rotates the reader/operational cache generations for the configured database.
It does not flush Redis, remove RQ jobs or start any process. Cache values expire
normally. API/CLI/worker writes already invalidate reader responses after commit;
use this command after manual SQL changes or while developing response schemas.
It exits nonzero if Redis is unavailable. See [caching](caching.md).

```sh
uv run devfeed db current
uv run devfeed db upgrade
uv run devfeed db check
uv run devfeed db upgrade --config /path/to/alembic.ini
```

Migration commands search the current directory and parents for `alembic.ini`;
an explicit `--config` path overrides discovery. This config selects migrations,
not application connection settings. `upgrade` applies the existing migrations
through head; it does not reset the database. See the
[existing-database migration warning](development.md#updating-an-existing-development-database)
before upgrading an older checkout. There is no CLI reset, purge or downgrade command.

Record commands emit JSON to stdout. Worker/scheduler and migration commands use
their normal operational logs; `scheduler --once` emits JSON tick counters.
Errors go to stderr. Exit codes are 0 for success, 1 for operational/dependency
failures, 2 for invalid input/configuration/conflicts, and 130 for an unhandled
keyboard interrupt. Help returns 0. `status` still emits its snapshot when a
dependency is down, but returns 1.

Diagnostic logs go to stderr in plain text by default; set `DEVFEED_LOG_FORMAT=json`
for structured logs. `DEVFEED_LOG_LEVEL` controls verbosity. These settings do not
change JSON record output on stdout. See the [logging guide](logging.md).
