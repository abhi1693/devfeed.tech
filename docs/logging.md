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
the terminal or deployment runtime owns retention and collection.

## Readable development output

Text mode at INFO/WARNING/ERROR uses local `HH:MM:SS` timestamps, readable messages
and only relevant context. For example:

```text
15:21:18 INFO  [cli] Checking RSS/Atom feed
error: Feed validation failed: HTTP 404 (not found). Check the RSS/Atom URL.
15:22:03 INFO  [worker] Ingested 25 entries: 17 new, 0 skipped (job 7e0277c1; 1.20 s)
15:22:04 INFO  [api] GET /v1/feed -> 200 [cache hit] (8 ms)
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

- API calls: generated `request_id`, HTTP method, route template, status and
  `duration_ms`, `cache_status` and `cache_bypass_reason` for cache-enabled routes.
  Bypass reasons are fixed labels, not request-header values. The same ID is returned
  in `X-Request-ID`, including on failures and cache hits.
  Client-supplied IDs are not trusted. Context propagates into synchronous endpoints
  and preflight feed validation; concurrent requests remain isolated.
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
- Configuration writes: source/category/tag IDs and changed field names, not input
  values. API success events and CLI successful command results follow commit.

The job ID links scheduler dispatch to worker ingestion. Request and command IDs
are local to their operation; they are not persisted in PostgreSQL or sent through
Redis. A scheduler tick or CLI operation may contain several feed/job events.

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
not an error for every request. Invalidation events are DEBUG. Cache keys, query
values, serialized response bodies and Redis credentials are not logged.

Application events use fixed names and an explicit field allowlist. Logs omit
connection URLs, credentials, request headers/bodies, raw paths/query strings,
publisher URLs, article content and SQL parameters. Unknown routes are recorded as
`<unmatched>`, never their raw path. Strings are bounded and escaped in both formats
so input cannot insert extra log lines.

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
