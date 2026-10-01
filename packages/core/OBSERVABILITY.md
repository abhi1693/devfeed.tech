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

Text, JSON and stored job logs no longer redact supplied values. Request URLs keep
query values; structured fields, nested values, library messages and exception
messages are preserved. Text summaries remain concise, with full context at DEBUG.
Request URLs and log messages have no string-length cap. Control characters are
escaped; job-stream event size, queue and retention limits remain.
The logger does not automatically capture request bodies, headers or local variables.

Browser telemetry retains original error messages, stack filenames/functions,
console log payloads, event attributes and metadata. Browser and Node trace exports
retain supplied span attributes and events. The browser receiver still validates
origin, content type and size, enforces rate/time limits, and assigns service identity.
Metric routes remain grouped to limit cardinality; Node request logs use concrete URLs.

Restart running services to load the updated logging code. Previously masked values
in retained records cannot be reconstructed. This change needs no database migration.

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
