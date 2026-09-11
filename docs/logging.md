# Application logging

API, CLI, RQ workers, scheduler and Alembic migrations share the same standard-library
logging configuration. No extra logging service or dependency is required.

Set these optional application settings in `.env` or the process environment:

```dotenv
DEVFEED_LOG_LEVEL=INFO
DEVFEED_LOG_FORMAT=text
```

Plain text is the default for development. Use `DEVFEED_LOG_FORMAT=json` for
structured collection in deployments. Formats are `text` and `json`; levels are
`DEBUG`, `INFO`, `WARNING`, `ERROR` and `CRITICAL` (level names are case-insensitive).
Invalid values fail settings validation. Restart the relevant process after changing
these settings. Database and Redis URLs remain required, without defaults.

Both formats write one event per line to stderr. CLI record output remains JSON on
stdout, independent of the log format. Existing human-readable CLI error messages
also go to stderr. No log files, rotation or external log shipping are configured;
the terminal or deployment runtime owns console retention and collection. Workers
also retain bounded per-job logs in Redis for the administration UI (below).

## Runtime logs on job pages

Every pipeline run page has a **Logs** tab and a **Runtime logs** panel under its
details: ingestion, article enrichment, image discovery, source profile enrichment,
and AI analysis. Logs update while a run is queued/running, with a short final drain
after completion. Search, level filtering, expandable context and plaintext download
operate on the loaded entries. Refresh reloads the retained history. Attempts share
the same durable job ID; their events include attempt numbers wherever available.
Receipt/claim failures and RQ parent-process crash reports may precede a claimed
attempt, so they do not invent one.

Workers capture sanitized application DEBUG and higher events regardless of the
console log level. Console verbosity and text/JSON selection remain unchanged.
This includes fetch/parse diagnostics, results, retry decisions, lease loss, safe
exception stacks, and RQ's unexpected work-horse exit callback. Raw stdout,
publisher text, SQL, prompts, AI responses and exception messages are **not** stored.

```dotenv
DEVFEED_JOB_LOG_MAX_ENTRIES=1000
DEVFEED_JOB_LOG_TTL_SECONDS=604800
```

These optional settings bound each job's Redis stream to the latest 1,000 entries
and expire it seven days after the last write by default. Entry count accepts
100–10,000; retention accepts 3,600–2,592,000 seconds. Each event is capped at 16 KiB.
The same required `DEVFEED_REDIS_URL` is used, in a separate
`devfeed:job-logs:*` namespace; clearing the public response cache does not clear
logs, queues or admin sessions. Retention counts all attempts, not each attempt
separately. Job deletion makes logs inaccessible via the API; Redis expires them
automatically.

Writes happen outside the worker's PostgreSQL transaction and are flushed per
event, so database rollback or a subsequent worker crash does not erase already
written logs. Forked workers use independent Redis connections. Writes have short
timeouts, no automatic Redis retries, and a 30-second outage cooldown; failures
never change a job outcome. Events during a Redis outage can be lost; the console
remains the fallback. The user reports storage outages as HTTP 503, not an empty
history. Redis persistence and eviction policy determine durability: these logs
are operational diagnostics, **not a permanent audit trail**. Configure Redis
persistence and capacity if restart survival is required.

The authenticated admin endpoint is
`GET /v1/admin/jobs/{kind}/{job_id}/logs?limit=100&after=STREAM_ID`. Kinds are
`ingestion`, `article-enrichment`, `images`, `source-enrichment`, and `analysis`.
The response includes entries, an exclusive `next_cursor`, `has_more`, job status,
attempt count, and retention/truncation information. Page size is capped at 500;
the endpoint is admin-only and `Cache-Control: no-store`.

Restart existing ingestion **and analysis workers** yourself to load the capture
handler; reload the admin API/UI if not using auto-reload. No database migration
is required. Runs from before this feature have no captured history, and expired
logs cannot be reconstructed from job rows. The UI explicitly explains this.

## Readable development output

Text mode at INFO/WARNING/ERROR uses local `HH:MM:SS` timestamps, readable messages
and only relevant context. For example:

```text
15:21:18 INFO  [cli] Checking RSS/Atom feed
error: Feed validation failed: HTTP 404 (not found). Check the RSS/Atom URL.
15:22:03 INFO  [worker] Ingested 25 entries: 17 new, 0 skipped (job 7e0277c1; 1.20 s)
15:22:04 INFO  [api] GET http://localhost:8000/v1/feed?limit=30 -> 200 [cache hit] (8 ms)
```

There is no default logger/PID/UUID/context dump. Job/source references are shortened
to eight characters for readability; full IDs remain in CLI/API responses, JSON
logs and DEBUG output. CLI start/success/completion wrappers and rejection logs
that duplicate its error line are hidden. Library startup/housekeeping chatter and
duplicate RQ job-failure reports are also hidden; root errors and independent
library warnings/errors remain visible.

Unexpected errors show the exception type and relevant code location, not JSON
traceback arrays. Attribute errors also identify the missing attribute and object
type when Python provides them safely; object values and exception messages remain
private. Use `DEVFEED_LOG_LEVEL=DEBUG` for event names, full correlation fields and
safe stack locations in text output. `DEVFEED_LOG_FORMAT=json` retains the full
structured event stream at the selected log level, without text-mode noise filtering.
The CLI picks up changes on its next invocation; already-running API, worker and
scheduler processes must reload/restart to pick up formatter code/config changes.

## Fields and correlation

Every JSON event includes a UTC timestamp, severity, service, logger, event name
and process ID. Full structured/debug context includes these fields when relevant:

- API calls (public and admin): generated `request_id`, HTTP method, concrete
  request path in `route`, full sanitized request URL in `request_url`, status and
  `duration_ms`, `cache_status` and `cache_bypass_reason` for cache-enabled routes.
  Bypass reasons are fixed labels, not request-header values. The same ID is returned
  in `X-Request-ID`, including on failures and cache hits.
  Client-supplied IDs are not trusted. Context propagates into synchronous endpoints
  and preflight feed validation; concurrent requests remain isolated.
  Actual IDs and encoded path segments are retained, including on failures, 404s
  and nested notification inbox routes. Related request events carry the same URL.
  The origin is the one received by the API; proxy headers are not independently
  trusted by the logger. Requests through Next.js can therefore show its upstream
  API origin rather than the browser origin.
- CLI operations: generated `command_id`, command/action, result IDs/counts, exit
  code and duration. Arguments and full command lines are not logged.
- Feed validation: `feed_id` (SHA-256 of the normalized URL), elapsed time, entry
  counts and failure reason codes such as `unreadable_feed`, `http_error` or
  `transport_error`. No source ID exists before a source has been saved.
- Scheduling: `tick_id`, scheduled/dispatched/recovered counts, source/job IDs and
  the RQ job ID at dispatch. Successful dispatch and recovery events follow commit.
- Ingestion: durable `job_id`, `source_id`, `source_type`, attempt number, HTTP status, entry and
  article counts, retry/terminal outcomes and duration. The worker name and PID
  distinguish replicas and forked worker processes. Success is logged after commit.
- Image discovery: durable `job_id`, `article_id`, attempt, outcome and metadata
  method, plus safe failure/retry reasons. Scheduler summaries include separate
  image dispatch/recovery counts. Article and image URLs are never logged.
- Configuration writes: source/topic/tag IDs and changed field names, not input
  values. API success events and CLI successful command results follow commit.

The job ID links scheduler dispatch to worker ingestion. Request and command IDs
are local correlation fields, not part of job rows or RQ message arguments.
If inherited from the worker's CLI invocation, they are also retained in its
runtime log context. A scheduler tick or CLI operation may contain several
feed/job events.

## Levels and failures

`INFO` records lifecycle events, completed requests/commands, successful validation,
active scheduler ticks and ingestion results. Expected input errors, upstream feed
failures, retries and recovered leases use `WARNING`. Unexpected exceptions and
server/dependency failures use `ERROR`, with safe stack locations.

Successful health checks, idle scheduler ticks, duplicate/unclaimable ingestion
deliveries and detailed fetch/parse diagnostics use `DEBUG`. HTTP client and SQL
debugging stays suppressed even when application logging is at DEBUG. The shared
configuration replaces Uvicorn access output to avoid duplicate requests and raw
URL/query-string logging. RQ logs use the shared stderr handler instead of adding
their own stdout handlers; repeated setup does not duplicate application handlers.

## Sensitive data

Cache failures log a readable warning once per five-second circuit-breaker window,
not an error for every request. Invalidation events are DEBUG. Cache keys, private
query values, serialized response bodies and Redis credentials are not logged.

Application events use fixed names and an explicit field allowlist. Logs omit
connection URLs, credentials, request headers/bodies, publisher URLs, article
content and SQL parameters. Request URLs deliberately include actual paths, even
for unknown routes: do not put credentials in path segments. URL userinfo and
fragments are removed. Only known pagination/filter query values are shown;
free-text searches, cursors, OAuth codes/state, tokens and unknown query values
are `[redacted]`. Excessive query lists are omitted. URLs are bounded to 4 KiB
plus a truncation marker; strings are escaped so input cannot insert extra log lines.

Exception diagnostics include exception types, chained causes and bounded stack
locations (filename, function, line), but omit exception messages, source lines and
local variables. Feed failures have safe reason codes and upstream status instead.
Third-party free-form log messages and arguments are deliberately omitted because
they may embed URLs, SQL or complete tracebacks; their logger, level and code
location remain available as `dependency_log` events. RQ exceptions additionally
produce a structured `rq_job_failed` event with safe stack details.

These rules cover the application's logging handler, not shell output, CLI record
data or unrelated handlers installed by an embedding application. Avoid enabling
external SQL/HTTP debug handlers in environments containing real credentials.
