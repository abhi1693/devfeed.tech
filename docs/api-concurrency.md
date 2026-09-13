# Backend API concurrency audit

Audited on 2026-09-13: the public, admin, and user FastAPI applications, their
request dependencies, validators, cache wrapper, middleware, exception handlers,
notification streams, Codex connection monitor, and application lifespans.
Worker/CLI operations run outside the HTTP request event loops.

The six Next.js API route modules were also inspected: the admin and user
catch-all gateways plus the reader feed, search, topics and sources endpoints.
Their network and request-stream operations are awaited; no synchronous filesystem
or process calls were found in those routes or their server-side gateway helpers.
Gateway, public-feed, catalog and search regression tests passed. The table below
counts Python service routes, not the additional Next.js proxy aliases.

| Service | Native async handlers | Thread-pool handlers | HTTP method/path pairs |
| --- | ---: | ---: | ---: |
| Public API | 1 | 15 | 16 |
| Admin API | 6 | 96 | 104 |
| User API | 2 | 28 | 32 |
| Total | 9 | 139 | 152 |

There are **148 handlers**, including two notification proxy handlers that each
accept three HTTP methods. The inventory below comes from registered routes,
including routes excluded from OpenAPI.

## Execution policy

Native async paths use awaited HTTP/WebSocket I/O or return in-memory data.
Database, Redis, RQ and OIDC operations use synchronous clients inside ordinary
FastAPI handlers/dependencies, which FastAPI executes in its worker thread pool.
The public cache explicitly offloads its Redis operations before dependencies run.
URL and input validators are syntactic: they do not resolve DNS or fetch URLs.
Response models on async routes do not receive synchronous SQLAlchemy sessions.

The admin overview coordinates cache refreshes asynchronously. Its database loader
creates, uses and closes its synchronous session within one worker-thread call;
only the materialized response returns to the event loop. Each day range has one
local cache poller, with Redis coordinating refreshes across API replicas. Waiters
yield without occupying request threads or database connections, for at most five
seconds. An abandoned refresh still returns a bounded 503 with `Retry-After`.

Changing these synchronous handlers to `async def` without replacing/offloading
all their synchronous clients would block the event loop. A full native async
migration would require an async database/session layer, Redis and HTTP clients,
and changes to transaction hooks and their callers. This audit preserves the
existing transaction and authorization behavior.

See [FastAPI's concurrency guide](https://fastapi.tiangolo.com/async/) and
[Starlette's thread-pool documentation](https://starlette.dev/threadpool/).

## Fixes

- API stderr output uses a dedicated daemon thread and a bounded 1,024-entry queue.
  Records are formatted and sanitized with the originating request context before
  enqueueing. A stalled collector cannot block HTTP requests. When full, new logs
  are dropped and the handler's `dropped` counter increases; memory remains bounded.
  Explicit draining and shutdown waits are bounded. CLI/workers retain synchronous
  logging for their existing output/fork behavior.
- Database/Redis/cache client cleanup runs in a worker thread during API shutdown.
- Notification settings initialization and HTTP client construction run in worker
  threads: environment-file and TLS CA-bundle reads are synchronous even though the
  subsequent HTTP transport is async. Streams still close their clients asynchronously.
- The admin Codex monitor creates its verified TLS context off the event loop and
  reuses it across reconnects. Certificate and hostname verification remain enabled.
- Ruff's `ASYNC2` rules catch common blocking HTTP, process, file and sleep calls
  inside async functions. These checks supplement runtime tests; they cannot prove
  an arbitrary helper's entire call graph is nonblocking.

## Validation and limits

`tests/test_api_async_safety.py` checks all registered async route dependencies and
exercises each real API with its request thread pool reduced to one occupied slot.
An intentionally stalled readiness database operation does not prevent liveness
from responding. Other tests assert that cache, notification initialization, Codex
TLS initialization and shutdown cleanup execute outside the event-loop thread.
`tests/test_queued_logging.py` stalls stderr, fills the queue, and verifies bounded
logging with preserved request context and redaction. Existing auth, cache,
notification-stream and logging tests cover response/security compatibility.

`tests/test_admin_overview_concurrency.py` exercises 51 dashboard requests sharing
one refresh with a one-connection database pool. While cache publication is stalled,
settings, readiness and liveness must still respond. It also covers authentication
on cache hits, local/distributed refresh timeout handling, cache fallback, and
connection/lock cleanup after a failed calculation. Settings reads release their
connection before serialization; admin readiness releases its database connection
before checking Redis.

Thread-pool and database capacity still constrain throughput. Async declarations
do not fix slow SQL, pool exhaustion, upstream latency, or CPU-heavy work. Existing
SQL, pool acquisition, Redis socket, HTTP and WebSocket timeouts remain in force;
HTTP socket timeouts are not universal end-to-end request deadlines. This is a
source/runtime-test audit, not a production load test or a native-async migration.

## Handler inventory

This is a dated snapshot; route additions should be reviewed with the same policy.

### Public API

| Method | Path | Execution |
| --- | --- | --- |
| GET | `/health/live` | Native async |
| GET | `/health/ready` | Thread pool |
| GET | `/v1/articles/{article_id}` | Thread pool |
| GET | `/v1/feed` | Thread pool |
| GET | `/v1/feed/options` | Thread pool |
| GET | `/v1/search` | Thread pool |
| GET | `/v1/sitemaps` | Thread pool |
| GET | `/v1/sitemaps/{kind}/{page}` | Thread pool |
| GET | `/v1/sources` | Thread pool |
| GET | `/v1/sources/{source_id}` | Thread pool |
| GET | `/v1/tags` | Thread pool |
| GET | `/v1/tags/{slug}` | Thread pool |
| GET | `/v1/topics` | Thread pool |
| GET | `/v1/topics/{slug}` | Thread pool |
| GET | `/v1/topics/{slug}/relations` | Thread pool |
| GET | `/version` | Thread pool |

### Admin API

| Method | Path | Execution |
| --- | --- | --- |
| GET | `/health/live` | Native async |
| GET | `/health/ready` | Thread pool |
| GET | `/v1/admin/ai/connection` | Native async |
| POST | `/v1/admin/ai/connection/login` | Native async |
| POST | `/v1/admin/ai/connection/login/cancel` | Native async |
| GET | `/v1/admin/articles` | Thread pool |
| POST | `/v1/admin/articles` | Thread pool |
| DELETE | `/v1/admin/articles/{article_id}` | Thread pool |
| GET | `/v1/admin/articles/{article_id}` | Thread pool |
| PUT | `/v1/admin/articles/{article_id}` | Thread pool |
| POST | `/v1/admin/articles/{article_id}/classify` | Thread pool |
| GET | `/v1/admin/articles/{article_id}/content` | Thread pool |
| POST | `/v1/admin/articles/{article_id}/review` | Thread pool |
| GET | `/v1/admin/articles/{article_id}/reviews` | Thread pool |
| GET | `/v1/admin/auth/callback` | Thread pool |
| GET | `/v1/admin/auth/config` | Thread pool |
| GET | `/v1/admin/auth/login` | Thread pool |
| POST | `/v1/admin/auth/logout` | Thread pool |
| GET | `/v1/admin/auth/me` | Thread pool |
| GET | `/v1/admin/automation/articles/{article_id}/decisions` | Thread pool |
| POST | `/v1/admin/automation/articles/{article_id}/{action}` | Thread pool |
| GET | `/v1/admin/automation/sources/{source_id}/policies` | Thread pool |
| GET | `/v1/admin/ingestion/article-jobs` | Thread pool |
| GET | `/v1/admin/ingestion/article-jobs/{job_id}` | Thread pool |
| GET | `/v1/admin/ingestion/jobs` | Thread pool |
| GET | `/v1/admin/ingestion/jobs/{job_id}` | Thread pool |
| GET | `/v1/admin/ingestion/status` | Thread pool |
| GET | `/v1/admin/jobs/ai-analysis` | Thread pool |
| GET | `/v1/admin/jobs/{kind}` | Thread pool |
| GET | `/v1/admin/jobs/{kind}/{job_id}` | Thread pool |
| GET | `/v1/admin/jobs/{kind}/{job_id}/logs` | Thread pool |
| POST | `/v1/admin/jobs/{kind}/{job_id}/retry` | Thread pool |
| GET | `/v1/admin/knowledge/graph` | Thread pool |
| GET | `/v1/admin/knowledge/path` | Thread pool |
| GET | `/v1/admin/knowledge/search` | Thread pool |
| GET, POST, PUT | `/v1/admin/notifications/chimely/v1/inbox/{path:path}` | Native async |
| GET | `/v1/admin/notifications/config` | Thread pool |
| POST | `/v1/admin/notifications/deliveries/{identifier}/retry` | Thread pool |
| GET | `/v1/admin/overview` | Native async |
| GET | `/v1/admin/settings` | Thread pool |
| PUT | `/v1/admin/settings/appearance` | Thread pool |
| PUT | `/v1/admin/settings/defaults` | Thread pool |
| PUT | `/v1/admin/settings/notifications` | Thread pool |
| PUT | `/v1/admin/settings/profile` | Thread pool |
| DELETE | `/v1/admin/settings/tables` | Thread pool |
| PATCH | `/v1/admin/settings/tables/{table}` | Thread pool |
| GET | `/v1/admin/sources` | Thread pool |
| POST | `/v1/admin/sources` | Thread pool |
| POST | `/v1/admin/sources/preview` | Thread pool |
| DELETE | `/v1/admin/sources/{source_id}` | Thread pool |
| GET | `/v1/admin/sources/{source_id}` | Thread pool |
| PATCH | `/v1/admin/sources/{source_id}` | Thread pool |
| POST | `/v1/admin/sources/{source_id}/fetch` | Thread pool |
| PUT | `/v1/admin/sources/{source_id}/publication-policy` | Thread pool |
| POST | `/v1/admin/sources/{source_id}/review` | Thread pool |
| GET | `/v1/admin/sources/{source_id}/reviews` | Thread pool |
| GET | `/v1/admin/tags` | Thread pool |
| POST | `/v1/admin/tags` | Thread pool |
| DELETE | `/v1/admin/tags/{tag_id}` | Thread pool |
| GET | `/v1/admin/tags/{tag_id}` | Thread pool |
| PATCH | `/v1/admin/tags/{tag_id}` | Thread pool |
| PUT | `/v1/admin/tags/{tag_id}` | Thread pool |
| POST | `/v1/admin/topic-discovery/github` | Thread pool |
| POST | `/v1/admin/topic-imports` | Thread pool |
| POST | `/v1/admin/topic-imports/preview` | Thread pool |
| GET | `/v1/admin/topic-proposals` | Thread pool |
| POST | `/v1/admin/topic-proposals/analysis` | Thread pool |
| GET | `/v1/admin/topic-proposals/filters` | Thread pool |
| DELETE | `/v1/admin/topic-proposals/{proposal_id}` | Thread pool |
| GET | `/v1/admin/topic-proposals/{proposal_id}` | Thread pool |
| POST | `/v1/admin/topic-proposals/{proposal_id}/analysis` | Thread pool |
| POST | `/v1/admin/topic-proposals/{proposal_id}/review` | Thread pool |
| GET | `/v1/admin/topic-relations` | Thread pool |
| POST | `/v1/admin/topic-relations` | Thread pool |
| DELETE | `/v1/admin/topic-relations/{topic_id}/{related_topic_id}/{relation}` | Thread pool |
| GET | `/v1/admin/topic-relations/{topic_id}/{related_topic_id}/{relation}` | Thread pool |
| PUT | `/v1/admin/topic-relations/{topic_id}/{related_topic_id}/{relation}` | Thread pool |
| GET | `/v1/admin/topic-relationship-proposals` | Thread pool |
| DELETE | `/v1/admin/topic-relationship-proposals/{proposal_id}` | Thread pool |
| GET | `/v1/admin/topic-relationship-proposals/{proposal_id}` | Thread pool |
| POST | `/v1/admin/topic-relationship-proposals/{proposal_id}/review` | Thread pool |
| GET | `/v1/admin/topic-relationships` | Thread pool |
| GET | `/v1/admin/topic-replacements` | Thread pool |
| GET | `/v1/admin/topics` | Thread pool |
| POST | `/v1/admin/topics` | Thread pool |
| DELETE | `/v1/admin/topics/{topic_id}` | Thread pool |
| GET | `/v1/admin/topics/{topic_id}` | Thread pool |
| PUT | `/v1/admin/topics/{topic_id}` | Thread pool |
| GET | `/v1/admin/topics/{topic_id}/delete-preview` | Thread pool |
| POST | `/v1/admin/topics/{topic_id}/enrichment` | Thread pool |
| POST | `/v1/admin/topics/{topic_id}/enrichment/preview` | Thread pool |
| POST | `/v1/admin/topics/{topic_id}/relationships/analysis` | Thread pool |
| GET | `/v1/admin/users` | Thread pool |
| GET | `/v1/admin/users/{user_id}` | Thread pool |
| POST | `/v1/admin/users/{user_id}/analysis` | Thread pool |
| GET | `/v1/admin/users/{user_id}/interests` | Thread pool |
| GET | `/v1/admin/users/{user_id}/likes` | Thread pool |
| GET | `/v1/admin/users/{user_id}/recommendations` | Thread pool |
| GET | `/v1/admin/users/{user_id}/sources` | Thread pool |
| GET | `/v1/admin/users/{user_id}/topics` | Thread pool |
| GET | `/v1/admin/workers` | Thread pool |
| GET | `/v1/admin/workers/{name}` | Thread pool |

### User API

| Method | Path | Execution |
| --- | --- | --- |
| GET | `/health/live` | Native async |
| GET | `/health/ready` | Thread pool |
| PUT | `/v1/user/articles/{article_id}/like` | Thread pool |
| POST | `/v1/user/articles/{article_id}/open` | Thread pool |
| GET | `/v1/user/auth/callback` | Thread pool |
| GET | `/v1/user/auth/config` | Thread pool |
| GET | `/v1/user/auth/login` | Thread pool |
| POST | `/v1/user/auth/logout` | Thread pool |
| GET | `/v1/user/auth/me` | Thread pool |
| GET | `/v1/user/engagement` | Thread pool |
| GET | `/v1/user/feed` | Thread pool |
| GET, POST, PUT | `/v1/user/notifications/chimely/v1/inbox/{path:path}` | Native async |
| GET | `/v1/user/notifications/config` | Thread pool |
| GET | `/v1/user/preferences` | Thread pool |
| PUT | `/v1/user/preferences` | Thread pool |
| GET | `/v1/user/preferences/sources` | Thread pool |
| PUT | `/v1/user/preferences/sources` | Thread pool |
| PUT | `/v1/user/preferences/sources/{source_id}` | Thread pool |
| PUT | `/v1/user/preferences/topics/{topic_id}` | Thread pool |
| GET | `/v1/user/settings/appearance` | Thread pool |
| PUT | `/v1/user/settings/appearance` | Thread pool |
| GET | `/v1/user/settings/feed` | Thread pool |
| PUT | `/v1/user/settings/feed` | Thread pool |
| GET | `/v1/user/settings/notifications` | Thread pool |
| PUT | `/v1/user/settings/notifications` | Thread pool |
| GET | `/v1/user/settings/profile` | Thread pool |
| PUT | `/v1/user/settings/profile` | Thread pool |
| POST | `/v1/user/sources/suggestions` | Thread pool |
| POST | `/v1/user/sources/suggestions/preview` | Thread pool |
| GET | `/v1/user/trending` | Thread pool |
