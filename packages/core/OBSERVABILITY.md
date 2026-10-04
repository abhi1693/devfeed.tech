# Observability

All three FastAPI services use FastAPI's native OpenTelemetry support:
`devfeed-api`, `devfeed-user-api`, and `devfeed-admin-api`. The shared runtime owns
providers, sampling, exporters, and shutdown. There is one server span per request,
with child spans for dependency resolution, endpoint execution, serialization,
background tasks, SQL operations, and supported external dependencies.

## Configuration

```dotenv
OTEL_EXPORTER_OTLP_ENDPOINT=http://collector.internal:4318
DEVFEED_TRACE_SAMPLE_RATIO=0.1
DEVFEED_TELEMETRY_ENVIRONMENT=production
```

The endpoint is an **HTTP/protobuf base URL**. The runtime exports traces to
`/v1/traces` and metrics to `/v1/metrics`. `DEVFEED_OTLP_ENDPOINT` overrides the
standard endpoint variable when both are present. The SDK accepts standard
`OTEL_EXPORTER_OTLP_HEADERS` for collector authentication; keep it server-only.
FastAPI automatic export is disabled because the application owns these exporters.
Service names are assigned by the application so independently deployed services
remain distinct. `OTEL_SDK_DISABLED=true` disables the Python telemetry runtime.

Metrics export every 60 seconds and are not affected by trace sampling. Traces use
parent-based sampling with a configurable root ratio. Export timeouts are one second;
trace queues and batches are bounded. Exporter failures do not fail requests.
Provider shutdown is bounded to two seconds per provider.

For Prometheus collection, set `DEVFEED_METRICS_ENABLED=true` and configure
`DEVFEED_METRICS_HOST` / `DEVFEED_METRICS_PORT` (default `127.0.0.1:9100`). The private
listener serves the **same OTel instruments**, not another set of counters. Do not
publish this port through an Ingress. Application ports do not expose `/metrics`.
Each process/container needs its own listener. Python global providers are not
replaced, and exporters start during lifespan rather than module import.

## A useful API overview

Start with request rate, server errors, p95/p99 latency, cache efficiency, and
admission pressure. Break down by service first and route only when investigating.
Avoid an alert per route or an alert for every cache miss.

| Instrument                                            | Use                                                                                                                                                 |
| ----------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| `http.server.request.duration` (seconds)              | Its histogram count gives traffic; status attributes give 5xx error ratio; buckets give latency. Separate successful and failed responses.          |
| `http.server.active_requests`                         | Requests still sending responses.                                                                                                                   |
| `devfeed.admission.active`, `devfeed.admission.limit` | Actual occupied handler slots versus the configured concurrency budget, separately for requests and streams. Slots include background/cleanup work. |
| `devfeed.admission.rejections`                        | Overload rejections before database checkout. These also appear as HTTP 503s.                                                                       |
| `devfeed.cache.reads`                                 | Hits, misses, bypasses, and bounded reasons by cache name.                                                                                          |
| `db.client.operation.duration` (seconds)              | SQL operation latency and failures, with operation type and an error marker.                                                                        |
| `db.client.connection.wait_time` (seconds)            | Connection acquisition, including pool wait, connect, and pre-ping; distinguishes `ok`, `timeout`, and `error`.                                     |
| `db.client.connection.count`                          | Checked-out connections (`state=used`).                                                                                                             |
| `devfeed.dependency.duration` (seconds)               | Timing and success/error outcomes for Typesense, OIDC, publisher fetches, and Codex calls where used.                                               |

HTTP dimensions use `http.request.method`, `http.route`,
`http.response.status_code`, and `error.type`. Routes are templates; an unmatched
request has no route label. `devfeed.request.kind` separates normal requests from
notification streams in the duration histogram, including gateways sharing a
`{path:path}` route. A rejection before route resolution is classified as a request.
Exclude `kind=stream` when measuring interactive latency. Duration ends after the
final response body/trailer is sent, before background tasks and cleanup.
Health/live, health/ready, and version requests are excluded.

Service, pod instance, version, and environment are OTel resource attributes.
The instance ID comes from the container's `HOSTNAME`, which Kubernetes sets to
the pod name. This keeps concurrent replicas' metric series distinct.
Prometheus translates names and attributes to underscores, for example
`http_server_request_duration_seconds_bucket`, with `service_name=devfeed-api`.
The histogram count is the request counter; no second HTTP counter is necessary.

## Cache semantics

`devfeed.cache.reads` uses `cache.name`, `cache.outcome` (`hit`, `miss`, `bypass`),
and `cache.bypass_reason` (`none`, `disabled`, `authorization`,
`request_cache_control`, `query_too_long`, `cache_unavailable`, `invalid_entry`,
`stale`). Names are limited to `public_response`, `public_profile`, `admin_overview`,
`admin_panels`, `admin_snapshot`, `catalog`, `feed_sequence`, and `other`.

Public responses and profiles count the final cache decision once, including when
caching is disabled. Waiting on another public response loader does not multiply
misses. Shared admin Redis reads count lookup attempts; local admin snapshot hits
have their own cache name, with stale served snapshots marked `stale`. Feed sequence
reads count actual lookup attempts. Catalog revision mismatches count as misses;
cache failures fall back to the existing database path and count as bypasses.
Only aggregate outcomes are recorded, never cache keys or user identifiers.

Hit ratio is `hits / (hits + misses)`. Track bypass share separately: including
bypasses in hit ratio hides whether caching is disabled or unavailable. A miss is
normal; investigate a sustained change together with request latency and database
pressure. A cache hit does not bypass authentication or visibility checks.

## Background services

Workers, scheduler, and indexer use the same provider/exporters and a small set of
workload-specific instruments: `devfeed.worker.active`, `devfeed.worker.executions`,
`messaging.process.duration`, `devfeed.background.cycles`,
`devfeed.background.duration`, and `devfeed.background.last_success`.
Search indexing adds `devfeed.search.documents` by outcome, `devfeed.search.outbox`,
and `devfeed.search.oldest_event_age`. Execution counters describe deliveries, not
successful editorial decisions. Existing admin job records remain authoritative for
business outcomes and retries.

RQ parents expose Prometheus observations without starting native profiling,
trace-export threads, or periodic OTLP metric threads before fork. Children create
providers after the final fork and bound their shutdown. Worker health still uses
the confirmed parent heartbeat and `devfeed_core.worker_health` probe. Long-lived
process freshness and queue backlog should be considered together; no traffic
alone is not a failure.

## Alerts and data boundaries

Use a measured service baseline before setting latency targets. Availability
alerts should combine a sustained 5xx ratio with minimum traffic; independently
monitor readiness and scrapes so an outage or zero traffic cannot look healthy.
Watch sustained admission rejection, database acquisition timeouts, dependency
failures, and a search backlog whose age continues growing. Page on user impact or
loss of progress; use cache ratio changes for diagnosis.

Python metrics and traces exclude raw paths, query strings, headers, bodies, SQL text,
bind values, prompts, credentials, cache keys, user identities, and exception
messages. Native exception telemetry logs are disabled; existing structured stderr
logs carry trace IDs. Incoming traces retain only the W3C parent identity, not
baggage or tracestate. Browser/Next telemetry has its own existing implementation;
this change covers the Python services.

## Application log content

Text and JSON logs remove authentication material at their output boundary.
Request URLs retain concrete paths, IDs and ordinary query values, while codes,
state, tokens, client secrets and other authentication parameters become
`[REDACTED]`. Matching is case-insensitive and covers encoded parameter names,
repeated parameters, fragments and encoded nested URLs. URL credentials and
credential fields in structured context, library messages and exception messages
are also removed, including Bearer and Basic authorization values. Ordinary
search/filter values, status codes, request IDs and trace correlation remain
available. Text summaries remain concise, with full context at DEBUG.
Request URLs and log messages have no string-length cap. Control characters are
escaped; job-stream event size, queue and retention limits remain.
The logger does not automatically capture request bodies, headers or local variables.

Browser telemetry removes the same authentication material from error messages,
stack filenames, log payloads, event attributes, metadata and OTLP attributes.
Sanitization runs before browser transport and again at the receiver, including
when a client skips browser normalization. Node request logs use the same URL policy.
Other diagnostic content is preserved. The browser receiver still validates
origin, content type and size, enforces rate/time limits, and assigns service identity.
Metric routes remain grouped to limit cardinality; Node request logs use concrete URLs.

Restart running services to load the updated logging code. Redaction applies to new
records; it does not remove authentication material from existing retained logs.

## Frontend telemetry

Web and admin Faro metadata uses the version embedded in the Next build
(`DEVFEED_BUILD_VERSION`), then `DEVFEED_VERSION`, then `development`.
`DEVFEED_TELEMETRY_ENVIRONMENT` supplies the environment independently. The receiver
assigns these values again when forwarding a payload; client metadata is not authoritative.

`devfeed_faro_deliveries_total{service,status,outcome}` counts receiver outcomes once.
`accepted` / 202 means the upstream collector accepted the batch, not that every
event was stored. The bounded outcomes distinguish missing, mismatched or unconfigured
origins, content encoding, invalid bodies, size/rate/time limits, disabled or
unconfigured collectors, upstream rejection, and upstream transport errors. They
contain no origin values, URLs, payloads or user/session identities.

```promql
sum by (service, status, outcome) (
  increase(devfeed_faro_deliveries_total{namespace="devfeed"}[24h])
)
```

A high 403 fraction alone cannot establish genuine browser data loss. Compare the
rejection reasons with a sampled browser's network response and collector ingestion
in the same window. Faro samples 10% of sessions, honors Do Not Track, and batches
events for up to five seconds. Unsampled sessions and unfinished page-lifecycle vitals
are not evidence of delivery failure. Do not weaken origin validation to improve the
acceptance ratio.

Node emits `http_server_request_duration_seconds` with `http_request_method`,
`http_route`, `http_response_status_code`, and `devfeed_request_kind` labels. Known
Chimely inbox stream paths (including the `/api` proxy prefix) and responses with
`Content-Type: text/event-stream` have kind `stream`; ordinary chunked HTML has kind
`request`. Disconnections are recorded once as 499. Measure request latency and error
ratios with `devfeed_request_kind="request"` to exclude stream lifetimes.

This replaces the Node `devfeed_http_request_duration_seconds` histogram. The
existing GitOps `devfeed:http_duration_seconds_*` rules already accept the new
histogram and exclude streams; update any direct queries of the old metric.
`devfeed_http_requests_total` remains available. During a rolling update, older
replicas still expose the previous histogram without status or stream classification;
confirm all replicas have updated before judging the corrected SLOs. New outcome
labels also start new counter series; older events have no reason and cannot be
classified retroactively. Let the selected rate windows age past the rollout.

After building both Next applications, run the real Faro browser/receiver regression
with `node apps/web/tests/browser/telemetry.mjs` and
`node apps/web/tests/browser/telemetry.mjs admin`. These use a local collector fixture
and force a sampled session only inside the test; they do not send production events.

## Migration

The old Python `devfeed_http_*`, database, dependency, worker, and search Prometheus
instrument definitions and manual HTTP tracing middleware are removed. There are
no legacy aliases. Update queries to the OTel instrument names above. The old
`devfeed_core.observability_exporter` aggregate exporter is retired; remove its
GitOps workload and its old aggregate dashboards/alerts before releasing this
change. This repository change does not modify cluster resources. Existing database
indexes remain; no schema migration is needed.

## References

- [FastAPI native OpenTelemetry](https://fastapi.tiangolo.com/advanced/opentelemetry/)
- [OpenTelemetry HTTP metric conventions](https://opentelemetry.io/docs/specs/semconv/http/http-metrics/)
- [Google SRE: monitoring distributed systems](https://sre.google/sre-book/monitoring-distributed-systems/)
- [OpenTelemetry Prometheus exporter](https://opentelemetry-python.readthedocs.io/en/latest/exporter/prometheus/prometheus.html)

## Investigating database query totals

The slow-query dashboard reports accumulated execution time over the selected
range, not the duration of a single call. Compare call counts and mean latency,
and use bounded read-only execution plans before changing a query. Search-index
reconciliation intentionally counts visible articles and tags exactly; those
recurring aggregate queries are distinct from reader request latency.

Tag reconciliation counts distinct tag UUIDs from visible article assignments.
It avoids building a semi-join hash table over every assignment, while preserving
publication, review and approved-origin checks. Publication reporting projects
review UUIDs and automatic/manual boolean flags before grouping. Keep its `OFFSET 0`
projection boundary: removing it can let PostgreSQL sort full automation JSON.
Reviews after publication remain excluded, and any manual review before publication
still excludes the article from the autonomous count.

The recurring-query regression uses approximately production cardinalities: 60,340
articles, 34,074 tags, 603,400 assignments and 153,680 reviews. It compares exact
application SQL with the previous queries at `work_mem=4MB`, including 1-, 7-, 30-
and 90-day reporting windows. Plan budgets enforce zero tag-count temporary writes
and substantially smaller reporting spills; elapsed times are reported rather than
asserted because they depend on the machine and cache state. Run it with disposable
test URLs configured as described in `tests/conftest.py`:

```sh
DEVFEED_RECURRING_PROFILE_REPORT=/tmp/devfeed-recurring-queries.json \
  uv run --locked pytest -q tests/test_recurring_query_budgets.py
```

Compare post-deployment deltas in `pg_stat_statements` calls, execution time and
temporary blocks per call, plus the DevFeed database's 24-hour `temp_bytes` increase.
The daily database total includes other workloads; improvements in these queries
alone do not establish how much of that total has been removed.

Topic-catalog lock waits can come from application work in the transaction holding
the lock. Inspect `pg_locks` together with `pg_stat_activity`, including transactions
waiting on `ClientRead`. Candidate selection reuses immutable normalized identities
and description vocabulary; it still recomputes eligibility against the current
catalog. Catalog edits and ordering changes cannot reuse stale retrieval indexes.

Article automation reuses one catalog snapshot and its retrieval indexes during a
batch. PostgreSQL revisions are checked before every reuse and again under the
publication locks. Ranking runs after the preparation transaction closes; the
locked checks reuse at most eight article candidate results. Content or catalog
changes between preparation and application leave the article due for the next
tick. Source eligibility, editorial revisions and job state are checked under
their existing locks. Publication evaluations are reused only within that same
locked transaction.

Migration `0022` makes topic description and AI description edits advance the
catalog revision, because both supply ranking vocabulary. Apply it before running
the updated services. The migration and its downgrade invalidate existing topic
snapshots; downgrading restores the previous description-invalidation behavior.
Compare `devfeed_background_duration_seconds` for `scheduler.tick`, scheduler CPU,
and topic-lock wait latency over matching windows after rollout. Local catalog
benchmarks do not establish production tick latency.

Analysis workers also prepare current catalog candidates and identity membership
before acquiring source, article and topic locks. A revision or source-content
change after preparation rolls back the application transaction and prepares the
new inputs outside the locks. Up to three preparations can reuse the same
inference result; continued churn uses the existing durable dependency retry
policy. Reanalysis of superseded content receives that validated catalog rather
than loading and ranking the entire catalog inside the application transaction.
Candidate scopes begin after inference, so they do not retain a full catalog
through the external model wait. Evidence and publication guards remain required.

The discovery profile covers feed facets with language, content-type and source
filters. Broad browsing uses early-exit probes; selective topic, tag and text
searches resolve their matching articles once. It checks two SQL queries and bounded article visits on a larger fixture:

```sh
DEVFEED_PROFILE_SUITE=discovery DEVFEED_DISCOVERY_PROFILE_ROWS=10000 \
  DEVFEED_PROFILE_REPEATS=3 bash scripts/profile-api.sh reports/discovery.json
```
