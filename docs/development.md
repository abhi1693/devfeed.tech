# Development and operations

Setup, maintenance and contributor reference for DevFeed. For an introduction
to the product, see the [DevFeed README](../README.md).

For a complete local stack with managed PostgreSQL and Redis, use the
[Docker Compose guide](compose.md). The commands below describe running individual
services manually with dependencies you supply yourself.

## Monorepo

```text
apps/
  api/src/        FastAPI ingestion, taxonomy and discovery API
  admin-api/src/  Isolated FastAPI administration, OIDC and session service
  admin/src/      Next.js admin UI and same-origin API gateway; atomic shadcn UI
  aggregator/src/ RQ workers and feed scheduler
  notifications/src/ Audience-aware Chimely adapter, run by the common RQ workers
  cli/src/        Typer feed submission, taxonomy and pipeline commands
  web/            Reserved for the reader UI
  extensions/     Reserved for browser extensions
packages/
  core/src/       Shared feed fetching/parsing, validation, services, models and jobs
migrations/       Versioned Alembic schema
tests/            Parser, network, API and real PostgreSQL/Redis tests
docs/             Architecture and product scope
```

The Python packages share one `uv.lock` through a uv workspace. Public API, admin
API, admin webapp and workers are independently runnable. Administration has its
own Dockerfiles and configuration; the root image remains API/CLI/workers only.
See [admin setup and service boundaries](admin.md) for OIDC and local commands.
See [notifications and infrastructure setup](notifications.md) for Chimely,
the admin inbox and the reusable future reader-notification contract.

Python files live directly in each project's `src/` directory, for example
`apps/aggregator/src/worker.py` and `apps/api/src/main.py`. Subdirectories are
reserved for real modules, such as `packages/core/src/feeds/`. Each project's
`pyproject.toml` maps `src/` to its distinct `devfeed_*` Python package name using
setuptools, so imports, CLI commands, the Docker entrypoint and stored RQ task
paths remain unchanged.

`uv sync` uses strict editable installs: ignored `build/` directories hold links
to the source files, allowing Python, type checking and editor navigation to use
the same package names. Source cache keys make the next `uv run` or `uv sync`
refresh these links after adding, removing or renaming Python files, or after
`build/` is removed by a clean/build operation. Do not edit the generated links;
edit the files under `src/`.

## Local Python development

Requires Python 3.12+ and uv, plus PostgreSQL and Redis instances you manage.
Copy `.env.example` to `.env` and set `DEVFEED_DATABASE_URL` and
`DEVFEED_REDIS_URL`. The URLs contain all connection details; there are no separate
database/Redis credentials, host, port or database-name variables.

Both URLs are required for all application entrypoints, including migrations.
Missing, empty or whitespace-only values fail settings validation at startup.
Shell environment variables override `.env`. The repository does not provision
or automatically start dependencies.

When you are ready to run the application:

```sh
uv sync --all-packages --locked
uv run devfeed db upgrade
```

Start these manually in separate terminals:

```sh
uv run uvicorn devfeed_api.main:app --reload --no-proxy-headers
uv run devfeed worker
uv run devfeed scheduler
```

Open <http://localhost:8000/docs> for the API, or use the CLI below. No account
setup is needed. The scheduler checks due sources every 15 seconds and dispatches
queued jobs; workers perform ingestion. Read articles at
<http://localhost:8000/v1/feed>.

Ingestion now creates unpublished candidates. Read the
[editorial, topics and AI workflow](editorial.md) before applying the latest
migration: the pre-release migration history was consolidated, so databases on
the old chain require a reset. New articles start pending/unpublished. Classification, approval
and publication are separate operations. AI remains disabled until you explicitly
configure an existing Codex endpoint and model.

For a bounded pass, run `uv run devfeed scheduler --once` followed by
`uv run devfeed worker --burst`.

## Docker image

```sh
docker build -t devfeed:local .
```

The Dockerfile packages the API, CLI and worker/scheduler commands in one image.
Its default command runs the API; `devfeed worker` and `devfeed scheduler` select
the other process roles. Service orchestration and dependency provisioning are
deferred. Building the image does not start the application or run migrations.

Supply the same two connection variables at runtime, using hosts reachable from
inside the container. A container's `127.0.0.1` refers to that container, not the
host machine. Apply migrations explicitly before starting the API or workers.

## Command-line workflow

The CLI works without FastAPI running. Submit sources for ingestion, then classify
and publish candidates through the editorial commands. Database-managed keyword
rules remain a non-AI fallback; they do not grant publication approval:

```sh
uv run devfeed sources add 'https://dev.to/feed' --type publisher
uv run devfeed sources add 'https://hnrss.org/frontpage' --type aggregator
uv run devfeed sources add 'https://lobste.rs/rss' --type aggregator
uv run devfeed sources import publisher-feeds.txt --type publisher
uv run devfeed sources list
uv run devfeed sources list --status pending
uv run devfeed sources approve SOURCE_UUID --by 'Operator'
uv run devfeed sources enrich SOURCE_UUID --force
uv run devfeed jobs list --status failed
uv run devfeed sources fetch SOURCE_UUID --force
uv run devfeed jobs dispatch JOB_UUID
uv run devfeed images backfill --limit 100
uv run devfeed images jobs
uv run devfeed status
```

Submission first fetches and parses the feed, rejecting unreadable or unreachable
sources before saving anything. Valid empty feeds are accepted. Bulk imports check
every unique feed before saving the batch. New CLI sources are approved and queue
durable ingestion and source-profile jobs when enabled; the scheduler dispatches
them to RQ. CLI resubmission is idempotent and never silently approves an existing
pending/rejected source. Both connection
variables remain required, with no defaults. Commands also
cover source enable/disable and refresh, failed-job retries, topic identities and relationships,
dynamic tags, and schema checks. See the [CLI guide](cli.md) for commands,
background operation, import format and exit codes, or `uv run devfeed --help`.

Source names are optional: the CLI, API and bulk imports use the RSS/Atom feed
title from the validation response when no name is supplied. Titles are cleaned
to plain text and limited to 200 characters; untitled feeds fall back to the
submitted URL's hostname. `--name` (or API `name`) overrides the feed title.
Existing source names are preserved on resubmission.

Sources also carry a short description, website, logo, preview image, language and
optional submitter attribution. RSS metadata is captured during validation; a
separate RQ stage fills missing fields from declared website metadata without
overwriting edits. API submissions remain pending until explicitly approved via
the CLI. See [source profiles and review](sources.md) for the API payload,
approval/rejection commands, attribution limits and existing-source enrichment.

`sources fetch --force` publishes a queued refresh immediately; `jobs dispatch`
does the same for a specific queued job. Both bypass retry/redispatch delays while
preserving running-job leases. Neither starts a service or runs a full scheduler
tick. A worker must already be running to consume the RQ message.

Every source requires an explicit `source_type`: `publisher` (its own published
content, such as DEV.to) or `aggregator` (submissions/discovery links, such as HN
and Lobsters). There is no default or hostname-based inference. RSS and Atom work
with either type. Type is immutable for now; changing interpretation requires a
future reprocessing workflow. Resubmitting a URL with a conflicting type is rejected.

## API contract

| Route | Purpose |
| --- | --- |
| `GET /v1/feed` | Paginated, newest-first article discovery and search |
| `GET /v1/articles/{id}` | Article metadata and publisher links |
| `GET /v1/sources`, `/v1/sources/{id}` | Approved source profiles for discovery |
| `POST /v1/sources` | Validate and submit a pending source for review |
| `GET /v1/topics` | Active canonical subjects and reviewed metadata |
| `GET /v1/tags` | Tags, aliases and optional topic links |
| `GET /health/live`, `/health/ready` | Process liveness and database/Redis readiness |

These public endpoints require no login. Taxonomy writes and ingestion diagnostics
now live under `/v1/admin` on the separate **admin API**, requiring an admin session.
Source listings expose only approved profiles, without
submitter details, review notes or polling diagnostics. Approved but paused sources
are included; use `enabled=true` or `enabled=false`, and `source_type=publisher` or
`source_type=aggregator` to filter them. Source edits, review decisions and manual
fetches are CLI-only; the former source PATCH and fetch routes have been removed.
Do not expose the entire API publicly while access control is deferred.

`POST /v1/sources` performs the same fetch-and-parse validation as CLI submission.
It returns a pending receipt and creates no background jobs. Callers cannot set
approval, submission channel, enabled state or polling interval. Failed checks
return HTTP 422 with `detail`,
`upstream_status` (when available) and `retryable`, without saving a source or job.
Actual article ingestion and classification remain background work.

Example source request:

```json
{"feed_url": "https://hnrss.org/frontpage", "source_type": "aggregator"}
```

Article responses include each source's type and an `origins` list with bounded
`source_metadata` evidence: descriptions, tags, discussion links, and publication
or submission fields. Aggregator submitters and submission dates are not article
authors or publication dates. `published_at` is nullable; `feed_at` is the stable
ordering timestamp. Aggregator records start as link-only discoveries, then an
independent RQ job fetches the linked page for its original title, short preview,
author, explicit publication timestamp, image and detected language. Original
submission evidence is preserved. `metadata_source_type` distinguishes `aggregator`
placeholders, extracted `page` metadata, and higher-priority `publisher` RSS metadata;
this does not introduce a third source type. No full article body is republished.

For existing aggregator records:

```sh
uv run devfeed db upgrade
uv run devfeed articles backfill --limit 100 --dispatch
uv run devfeed articles jobs
```

Use your existing workers; these commands do not start services. Reload the scheduler
and workers when updating their code. See [article enrichment](articles.md) for
retry commands, metadata precedence, page restrictions and job inspection endpoints.

Publisher article languages are detected offline by the ingestion worker using
[Lingua](https://github.com/pemistahl/lingua-py), independently of the source's
declared language. Detection uses the excerpt, with the title as supporting text
for short excerpts. Uncertain or insufficient text stays null; discovery-only
HN/Lobsters titles are not evidence of the linked article's language. Source
languages and `origins[].source_metadata.language` keep their declared values.

Correct existing language hints, including non-null but wrong values, with:

```sh
uv run devfeed articles detect-languages --limit 100 --dry-run
uv run devfeed articles detect-languages --limit 100
```

This bounded maintenance command runs inference locally without fetching pages or
starting workers. New ingestion uses the same detector inside RQ. No migration or
new environment variable is required. See [language detection](languages.md)
for confidence handling, pagination and backfill behavior.

Newly ingested publisher articles get image lookups automatically when their feed
does not provide an image. For existing articles, apply `uv run devfeed db upgrade`,
reload workers/scheduler, then queue `uv run devfeed images backfill --limit 100`.
The additive migration preserves your data; no database reset is needed. See
[image discovery](images.md) for selection rules, retries and manual commands.
Aggregator page enrichment discovers images in the same fetch instead of creating
a separate automatic image job.

Feed parameters include `limit` (1–100), `cursor`, `q`, `topic`, repeated `tag`,
repeated `exclude_tag`, `source_id`, repeated `exclude_source`, `content_type` and
`language`. Tags within an include list use **any-match** semantics; other filter
groups combine with AND. Excluding a source removes articles with any attribution
to that source, including syndicated duplicates. Keep filters the same when using
the returned `next_cursor`. Language matches the article's code exactly; detection
emits base ISO 639-1 codes (`en`, `ja`, etc.), without guessing regional variants.
The `topic` filter matches direct primary/supporting assignments only. Topic
relationships and tag-to-topic links do not add implicit matches. `/v1/tags`
returns IDs, names, slugs, aliases and optional `topic_id`; article tags are slugs.

```sh
curl 'http://localhost:8000/v1/feed?tag=python&tag=fastapi&exclude_tag=llm&content_type=tutorial&limit=20'
curl 'http://localhost:8000/v1/feed?q=postgresql&topic=backend'
```

The future web app and extension can persist these choices locally. There is no
reader registration, profile, tracking, social graph or account requirement.

## Taxonomy management

Topics are the single subject catalog, covering disciplines and specific technologies.
Use the admin **Topics** page to create a reviewed topic deliberately, import JSON/CSV,
discover GitHub curated topics, or propose keyword enrichment. Imported suggestions
stay in a separate review queue until an administrator approves them, unless the
app's [automatic approval policy](topics.md#automatic-approval) is enabled for
successful topic research. Article analysis
only assigns existing active topics and cannot suggest new topics.
See [topic management](topics.md) for formats and the review workflow.

Topics have unique identities (names, slugs, aliases), a kind, sourced metadata,
and separate matching keywords. Relationships do not imply article relevance.
Tags remain secondary facets and may link to a topic without automatically
assigning articles. Content type and format remain independent fields.

An empty catalog does not stop ingestion. Publisher labels never create topics
or tags automatically. With AI disabled, workers use reviewed topic keywords and
existing tag aliases as a conservative fallback. AI analysis uses only active topic
IDs and sends unknown subjects for review. Approval of a topic does not classify
or publish articles; reanalysis or explicit classification does that separately.

## Updating an existing development database

Apply `uv run devfeed db upgrade` explicitly after updating, before starting the API
or workers. The Dockerfile does not run migrations automatically.

The initial release consolidates all pre-release revisions into
`migrations/versions/0001_initial_schema.py` (`0001_initial`). It creates the baseline
schema directly, with no former account/login tables or historical data transforms.
It requires an empty database. **Databases on the removed revision chain must be
backed up and reset/recreated explicitly before upgrading.** Do not stamp an old
database as current to skip this requirement; the application does not reset or
stamp databases for you. Retained RQ jobs may reference removed database rows;
stop your processes and handle those old jobs explicitly during your reset.

After the reset, run `uv run devfeed db upgrade`, `uv run devfeed db check`, and
`uv run devfeed cache clear` to invalidate retained GET responses. Do not flush a
shared Redis instance. Restart your processes yourself and re-add sources. Future
schema changes append new Alembic revision files; the baseline is now frozen.
See [migrations](../migrations/README.md) for the incremental workflow.

Databases already on `0001_initial` do **not** need another reset. Apply
`uv run devfeed db upgrade` to add the incremental `0002_notifications` outbox.

## Application versions

All Python projects share one release version, currently `0.0.1`. Runtime version
reporting reads installed package metadata, not an environment override.

```sh
uv run devfeed --version
uv run python scripts/version.py check
uv run python scripts/version.py bump patch --dry-run
```

The API exposes `/version`, OpenAPI `info.version`, and `X-DevFeed-Version` response
headers. See [release/version instructions](releases.md) for synchronized
bumps, changelog entries and annotated Git tags. These commands do not commit,
tag, push or publish a release automatically.

## GET response caching

Redis caches feed/article responses for 5 minutes, source/taxonomy profiles for
10 minutes by default. Admin ingestion diagnostics are not cached. Cache hits skip
database dependencies and queries. Health checks, writes, errors, and requests with
authorization are not cached. Incidental browser cookies do not affect these
non-personalized responses. `X-Cache: HIT`, `MISS` or `BYPASS` makes caching visible;
`X-Cache-Bypass-Reason` explains bypasses, also recorded in request logs.

Committed source, taxonomy, article and enrichment changes invalidate reader caches
across API, CLI and workers. Redis failures fall back to the database without failing
the request or an already committed write. If invalidation fails, TTL bounds how
long old responses can remain after Redis recovers. No migration is needed; reload
existing processes so API and workers both use the cache hooks.

TTL expiry can produce a `MISS` even when data is unchanged; it is a safety bound,
not evidence of a write. Zero-row upserts/conditional writes do not invalidate the
cache. Relevant data changes still invalidate before the TTL expires.

The cache uses the existing required `DEVFEED_REDIS_URL`; no extra connection
variables. Optional TTLs, size limit and `DEVFEED_CACHE_ENABLED` are documented in
[caching](caching.md). For development:

```sh
curl -i 'http://localhost:8000/v1/feed?limit=10'
curl -i 'http://localhost:8000/v1/feed?limit=10'
curl -H 'Cache-Control: no-cache' 'http://localhost:8000/v1/feed?limit=10'
uv run devfeed cache clear
```

The clear command invalidates only this database's GET cache, never RQ jobs or
other Redis data. Old cache values expire naturally.

## Logging

Plain-text development logs are enabled by default. Set `DEVFEED_LOG_FORMAT=json`
for structured output and `DEVFEED_LOG_LEVEL=DEBUG` for more detail. API, CLI,
scheduler, workers and migrations share the configuration. Logs go to stderr;
CLI record output remains JSON on stdout. See the [logging guide](logging.md)
for request/job correlation, log levels and sensitive-data handling.

## Verification

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest -m 'not integration'
bash scripts/test.sh -m 'not integration'
```

The ordinary test runner never starts services. CI creates disposable services
for integration tests and gates image publication on tests and security checks.
Tests cover application behavior, not generated migration code. Integration setup
applies migrations only to prepare the disposable test database.
Integration tests require externally supplied PostgreSQL (not SQLite) and Redis,
because they exercise partial indexes, upserts, search and row locks.

To use your own **disposable** test services, set `DEVFEED_TEST_DATABASE_URL`
(database name must end in `_test`) and `DEVFEED_TEST_REDIS_URL` (database `/15`),
then run `bash scripts/test.sh`. These tests truncate application tables and flush that
Redis database. Without these variables integration tests are explicitly skipped.

See [architecture](architecture.md) for failure recovery and operational
limits, and [product scope](product-scope.md) for the daily.dev comparison.

## Continuous integration

See [CI and verified container images](ci.md) for the test/security gates,
AMD64/ARM64 images, immutable digest manifest, and future deployment contract.
