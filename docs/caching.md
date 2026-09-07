# Server-side GET caching

Cached reads use the existing `DEVFEED_REDIS_URL`. PostgreSQL remains authoritative;
cached responses are disposable, bounded JSON bytes, never ORM objects or pickle.
No database migration, new service or additional connection variable is required.

## Coverage

| GET routes | Default TTL | Invalidation |
| --- | --- | --- |
| `/v1/feed`, `/v1/articles/{id}` | 5 minutes | Committed reader-data changes |
| `/v1/sources`, `/v1/sources/{id}` | 10 minutes | Committed reader-data changes |
| `/v1/tags`, `/v1/topics` | 10 minutes | Committed reader-data changes |
| Admin API `/v1/admin/*` | Not cached | Session checked on every request |

TTL is a maximum age, not a check for changed data: a response expires even if
nothing changed. The longer reader TTLs are safety bounds for missed invalidation;
actual committed changes invalidate immediately without waiting for expiry.

Only successful JSON responses are stored. Errors (including 404/422), writes,
health checks, OpenAPI/docs, streaming responses and responses with cookies or
explicit cache/Vary policy are not stored. Requests with Authorization headers
bypass shared cache. Incidental browser cookies do not bypass it: the opted-in
endpoints are non-personalized and never read cookies. Future cookie-dependent
or personalized endpoints must not use this shared route cache.
`Cache-Control: no-cache` or `no-store` also bypasses
it for a current database view, useful when inspecting live job progress.

`X-Cache` reports `HIT`, `MISS` or `BYPASS`. Bypasses also return
`X-Cache-Bypass-Reason`: `disabled`, `authorization`, `request_cache_control`,
`query_too_long` or `cache_unavailable`. Both fields appear in request logs without
exposing cookies, tokens or request-header values.
Cache hits run before FastAPI dependency resolution, so they do not open a database
session or issue queries. JSON is returned unchanged, with a fresh request ID;
CORS is evaluated per request rather than replaying the first caller's headers.
Responses use `Cache-Control: no-store`: this feature does not enable independent
browser/CDN caching that could outlive backend invalidation.

## Keys and concurrent misses

The route path and all query parameters are hashed into the key. Parameter names
are sorted, but repeated values retain their order: repeated scalar parameters can
have order-dependent FastAPI semantics. Filters, search, pagination and cursors
cannot share a response accidentally. Equivalent requests with different omitted
defaults or reordered repeated filter values may use separate entries; correctness
is preferred over aggressive canonicalization.

The prefix includes a cache-format version and a hash of `DEVFEED_DATABASE_URL`,
separating environments sharing Redis without putting connection strings or raw
queries in keys. A random generation token is changed by invalidation. Old entries
remain unreachable and expire by TTL; no `KEYS`, `SCAN`, wildcard deletion or
`FLUSHDB` is used. Redis losing/evicting a generation produces a new random token,
never a fixed token that could resurrect older values.

A miss uses an owner-token Redis lock with a 30-second lease. Concurrent requests
wait asynchronously for up to one second for the owner's result, then fall back
to their own database read if necessary. This is best-effort load coalescing, not
an availability lock: slow loaders can still cause multiple reads. Only the owner
may publish, and publication atomically checks both generation and ownership.
A late read cannot restore an invalidated generation or overwrite a newer loader.
Unlocking is also owner-checked. Redis calls run in a thread pool, not on the ASGI
event loop. These follow Redis's documented [owner-token lock pattern](https://redis.io/docs/latest/develop/clients/patterns/distributed-locks/).

## Write invalidation

The application session tracks relevant ORM flushes and direct SQLAlchemy
INSERT/UPDATE/DELETE statements, including ingestion upserts and image updates.
Direct writes mark invalidation only after execution reports affected rows, so
ignored duplicate inserts and conditional updates matching zero rows do not evict
reader data. Unknown driver row counts are conservatively treated as writes.
ORM assignments of unchanged values do not invalidate either.
It changes the public generation only after the outer transaction commits. It
does not invalidate on rollback or a savepoint commit. See SQLAlchemy's
[session events](https://docs.sqlalchemy.org/en/20/orm/events.html) for the underlying hooks.

Tracked changes include articles/provenance, topic/tag associations, taxonomy,
and source profile/approval/enabled fields. They affect multiple representations,
so a single conservative public generation invalidates all related reader routes.
Source polling timestamps, HTTP validators, failures and job-only updates do not
churn reader caches. Operational status/history has a separate generation and short
TTL; bypass caching when an exact current status matters.

API, CLI and workers use the same application session factory, covering manual
source edits/review, successful ingestion, image discovery and source enrichment.
Reload all existing processes when enabling this implementation. An old worker
process without the hooks can only be reflected by TTL expiry. Raw SQL through
external database clients/connections does not trigger these hooks; clear the cache
explicitly after such changes. Future application write paths should continue to
use the shared session factory.

## Failure behavior and limits

Cache connections have 200-ms connect/socket timeouts and no automatic retries.
On a Redis error, a process-local circuit breaker bypasses cache for five seconds,
logging a bounded warning. Reads continue against PostgreSQL, and a committed write
does not become a reported failure because its cache invalidation failed.

Database commit and Redis invalidation are not one atomic transaction. A process
crash or Redis outage between them can leave old values until their TTL expires;
invalidation is best-effort, not a durable outbox or strong-consistency guarantee.
Ingestion still enforces approval in PostgreSQL independently of cache. No stale
cache is deliberately served while Redis is unavailable.

Per-response size limits and TTLs bound individual entries, not total Redis memory
under unlimited distinct queries. Existing RQ data shares this Redis: do not enable
an indiscriminate all-keys eviction policy to accommodate cache traffic. Monitor
memory, apply request limits at a public edge, and disable/reduce caching if needed.
No Redis server configuration is changed by the application. Heavy aggregation may
rotate the conservative public generation often; finer dependency groups can be
added later if measurements show unnecessary cache misses.

## Configuration and operations

```dotenv
DEVFEED_CACHE_ENABLED=true
DEVFEED_CACHE_TTL_SECONDS=300
DEVFEED_CACHE_METADATA_TTL_SECONDS=600
DEVFEED_CACHE_MAX_BYTES=1000000
```

These are optional application settings, not connection defaults. TTL ranges are
1–3,600 seconds for both TTLs; response size is 1,024–5,000,000 bytes.
Disabling caching bypasses reads and automatic invalidation; keep the setting
consistent across process roles, and clear caches when re-enabling after changes.
The existing required database/Redis URLs remain unchanged.

```sh
curl -i 'http://localhost:8000/v1/feed?limit=10'
curl -i 'http://localhost:8000/v1/feed?limit=10'
# Admin diagnostics require a session on the separate admin service; see admin.md.
uv run devfeed status
uv run devfeed cache clear
```

`cache clear` rotates only this database's public and operational generations.
It does not delete RQ jobs, source/article data, or another environment's keys.
No process is started. Redis failure produces a nonzero command exit, rather than
claiming that clearing succeeded. When changing serialized response contracts,
clear the cache or bump the cache-format prefix before serving incompatible values.
