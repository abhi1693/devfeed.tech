# Production observability

DevFeed uses the cluster's Prometheus/Grafana, Alloy, Loki, Tempo and Pyroscope stack.
Application instrumentation is optional locally and enabled explicitly in production.
The proposed operational objective is **99.9% HTTP availability over 30 days**, with
p95 interactive response latency below two seconds. These are internal SLOs, not a
contractual SLA. Establish a traffic baseline before changing alert thresholds.

## Daily operations

Open the provisioned Grafana folder **DevFeed**:

- **Availability and capacity** (`/d/devfeed-operations`): request availability,
  error ratios, latency, route/database hotspots, readiness, CPU, memory, restarts,
  scrape health and exporter freshness.
- **Queues, workers and product pipeline** (`/d/devfeed-pipeline`): each isolated
  queue's eligible/delayed work, running leases, live workers, completions, failures,
  retries, execution/turnaround latency, product inventory and search backlog.
- **Errors, logs, traces and profiles** (`/d/devfeed-diagnostics`): failure families,
  dependency timing, database pool use, Node heap/event loop, browser vitals/errors
  and structured logs. Expand `trace_id` to open Tempo; the Profiles link opens
  Pyroscope with DevFeed services selected.

Check availability/probes first, then stale telemetry, queue age and successful
completions, worker memory/restarts, and newly recurring errors. A rising backlog
with continuing completions indicates capacity pressure; a backlog without live
workers or scheduler progress indicates stalled processing. An empty graph may
mean no observations or a failed collector. Check `up` and snapshot age before
interpreting it as healthy.

## Collection and network boundaries

| Signal | Producer | Collection path |
| --- | --- | --- |
| HTTP RED, process, database, dependency and worker execution metrics | Each API, Next server and long-running worker/scheduler/indexer | Prometheus PodMonitor → pod TCP **9100**, `/metrics` |
| Durable job/product/Redis state | One cached read-only exporter | Same private metrics listener; snapshots refreshed every 60 seconds |
| Container CPU/memory, readiness, restarts and limits | Existing kubelet/cAdvisor and kube-state-metrics | Existing cluster Prometheus monitors |
| Availability probes | Two blackbox exporter replicas | Prometheus Probe → private TCP **9115**; public reader HTTPS plus internal API readiness/admin login |
| Structured application logs | Application stdout | Existing Alloy Kubernetes log collection → Loki |
| Sanitized request, database, dependency and RQ traces | Python OTel SDK and Next OTel runtime | Private collector TCP 4318 → existing trace pipeline → Tempo |
| CPU/heap profiles | Python and Node Pyroscope SDKs | Private Pyroscope TCP 4040 |
| Browser errors, vitals, navigation and fetch traces | Grafana Faro, reader and admin | Same-origin `/telemetry/collect` → filtered server receiver → private Alloy Faro TCP 12347 |

Application ports **3000/8000/8001/8002 reject `/metrics` with 404**, with no metrics
Ingress. Frontend proxies reject the path before Next.js can stream a not-found page
with HTTP 200. NetworkPolicies admit Prometheus to port 9100. Alloy Faro can also reach
frontend port 9100 for private source maps; both are trusted internal collectors.
Blackbox port 9115 admits only Prometheus. No application credentials accompany
probes. Internal admin/API probes measure application readiness, not the external
SSO login journey; the public reader probe includes DNS, TLS and Cloudflare access
from the cluster's network. Independent off-site monitoring remains a separate
availability perspective.

The Faro key exists only in an encrypted SOPS secret consumed by the two frontends.
The browser never receives it. The public receiver accepts exact configured origins,
JSON only, at most 64 KiB per request, bounded read/forward deadlines and a bounded
20 requests/second token bucket (burst 40) per process. Alloy applies its additional
per-application rate limit. It forwards only filtered telemetry, without cookies,
authorization, client IP headers or incoming arbitrary headers.

Production browser source maps move from `.next/static` to `.next/faro-sourcemaps`
after building. The private telemetry listener serves them under `/sourcemaps/`;
public static source-map requests return 404. During a rolling release a collector
may reach a replica without an older build's map; stack resolution is best effort.
No public source-map download fallback is enabled in Alloy.

## Data minimization and correlation

Metrics use route templates and source-defined service, queue, operation, status and
error-family labels. Unknown routes become `unmatched`. Query strings, slugs, job IDs,
article bodies, SQL statements/bind values, prompts, credentials and user identities
are excluded. Log request/job identifiers remain fields, never Loki stream labels.
W3C `traceparent` connects request, database and enqueued RQ work; baggage is not
propagated by application instrumentation. Node exported spans strip raw URLs,
headers, exception messages, arbitrary attributes and span events.

Faro samples 10% of ephemeral sessions, uses random in-memory UUIDs, does not persist
sessions, honors Do Not Track, and excludes user metadata, console messages, DOM
text, form values, search terms, arbitrary custom events and exception messages.
Filtering happens before browser transport and again at the receiver. Exceptions
retain an error class and immutable JavaScript stack locations for internal source
map resolution. Browser counts/vitals are sampled diagnostics, not an availability
SLI or a count of all users. First-party fetch propagation stays on the
application origin; arbitrary third-party origins are not enabled. No authenticated actions are performed by monitoring.

## Metric semantics

| Metric family | Meaning and correct use |
| --- | --- |
| `devfeed_http_requests_total`, `devfeed_http_request_duration_seconds` | Completed HTTP counter/histogram; use `rate()`/`increase()` and `histogram_quantile()`. Python health/version paths and Node static/health/telemetry ingestion paths are excluded. 5xx are availability errors. |
| `devfeed_jobs{queue,kind,status}` | Current durable queued/running rows under current dispatch routing. |
| `devfeed_jobs_due`, `devfeed_jobs_oldest_due_age_seconds` | Eligible queued work and age since eligibility. Deliberately delayed retries do not inflate eligible age. |
| `devfeed_jobs_retrying`, `devfeed_jobs_expired_leases` | Queued rows with prior attempts; running rows whose lease expired. |
| `devfeed_queue_deliveries`, `devfeed_queue_workers` | Redis waiting deliveries and recent-heartbeat live worker registrations. Multi-queue workers appear in each subscribed queue; do not sum across queues to count physical processes. |
| `devfeed_jobs_completed_window`, `devfeed_jobs_retried_window`, `devfeed_job_retry_attempts_window` | **Gauges** over 5m/1h/24h windows of durable terminal rows; never apply `rate()`. Retried completions have attempts >1; extra attempts sum max(attempts−1,0). |
| `devfeed_job_completion_latency_seconds` | Exact windowed p50/p95 created-to-finished latency, including waiting/retries. Missing when no valid completions, rather than a fabricated zero. |
| `devfeed_jobs_retained`, `devfeed_job_failures_24h` | Retained row counts and bounded terminal failure families. Deletion reduces counts; these are not lifetime counters. |
| `devfeed_worker_executions_total`, `devfeed_worker_execution_duration_seconds` | RQ delivery executions returning to the parent and their duration including cleanup. A handler may return normally after recording a durable failure; these are **not business success metrics**. |
| `devfeed_background_*` | Scheduler/indexer cycle success/failure, duration and last successful cycle timestamp. |
| `devfeed_exporter_*` | Last refresh result, last good snapshot timestamp, refresh duration and errors. Failed refreshes retain previous values and explicitly mark them stale/unhealthy. |
| `devfeed_product_*`, `devfeed_search_*`, `devfeed_sources_with_fetch_errors` | Editorial inventory, article discovery freshness, search outbox backlog and sources with fetch failures. |
| `devfeed_database_*`, `devfeed_dependency_request_duration_seconds` | SQL operation timing/errors/pool usage and source-defined dependency call duration. Child-local RQ metrics are not scraped; worker dependency/database diagnosis uses traces and profiles. |
| `devfeed_faro_deliveries_total` | Receiver outcomes; 202 means Alloy accepted the filtered payload. 4xx indicate rejection/rate limiting, 503 indicates collector forwarding failure. |
| `devfeed_telemetry_component_up` | SDK initialization success, not proof of collector delivery. Forking parent workers deliberately do not initialize trace/profiling SDKs. |

Historical completions are grouped by **job kind**, whereas current backlog is
split by **physical queue**. For example, `relationships` can execute topic-analysis
and research-verification kinds; source approval changes can alter current routing.
Historical metrics do not pretend that today's route was necessarily the route at
execution time. Durable results remain authoritative after worker/exporter restarts.

## Runtime and database overhead

Python starts profiling/export threads only **after the final RQ fork**; the parent
exports process/execution metrics without native profiling. No Prometheus
multiprocess files accumulate per job. CPU profiling defaults to 19 Hz; Python and
Node heap sampling uses 1 MiB allocation intervals and bounded stacks. Child cleanup
bounds each SDK shutdown to two seconds. Trace queues are bounded to 512 spans,
batches to 64 and HTTP export timeout to one second. Collector failures cannot fail
application requests or cause unbounded queues.

The exporter uses aggregate/metadata-only, read-only SQL transactions, a 3-second
statement timeout and a 10-second refresh budget checked before statements. An
in-flight statement may use its remaining timeout. Scrapes read cached data only.
Migration `0006` builds eight partial covering completion indexes and four narrow
state-count indexes identified by production EXPLAIN, concurrently preserving
writes and repairing an interrupted invalid index on retry. Its index operations
commit independently; rollback removes them concurrently. No historical job payload
is loaded by the exporter.

A disposable 100,000-completed-job profile reduced the 24-hour completion query from
12.7 ms / 3,704 buffer hits to 2.9 ms / 21 hits. The full 70-query snapshot measured
about 0.34 seconds on the test host. A read-only production profile covered all 67
aggregate SELECTs (about 0.8 seconds combined on a partially warm cache) and identified
wide-table status/inventory scans for the additional narrow indexes. These are synthetic host measurements, not a
production latency guarantee; monitor actual exporter duration and snapshot age.

## Availability

Fast burn requires both 1h and 5m error ratios above 14.4 times the 0.1% budget;
slow burn requires both 6h and 30m above six times the budget. Both require at least
100 requests in the long window. Zero traffic does not manufacture success or fire
a ratio alert. Independent probe/deployment/metrics-missing alerts cover outages.

For an alert: identify the affected service/instance, inspect readiness and recent
releases, then route errors, DB/dependency timing and logs/traces. Check infrastructure
collector failures separately. Revert application pins through Fleet if the release
caused the regression; never manually mutate managed Deployments.

## Latency and hotspots

Compare route p95 with database and dependency duration, checked-out connections,
worker CPU and container memory. Node event-loop lag/heap profiles distinguish
synchronous CPU work from allocation pressure; Python profiles and DB spans identify
worker hot paths. Do not label metrics with SQL/query text to investigate a slow query.
Profile the specific query read-only with a bounded statement timeout instead.

## Workloads

Check pending/termination reasons and previous container logs. Container memory
includes RQ children; Python parent RSS alone does not. A CPU request is a scheduling
reservation, not a throughput cap. Preserve three workers per each dedicated AI queue
unless changing the corresponding GitOps replicas deliberately. Never replay a
running lease or delete retained storage as a routine recovery step.

## Queues and retries

Check eligible age, live worker state, completed/failed windows, last scheduler cycle
and provider cooldown. Expired leases should recover through the scheduler. Codex
readiness checks authenticate without inference; a provider outage preserves queued
AI work. Investigate recurring failure families in the admin job logs before retrying.
The warning thresholds are 30 minutes of eligible queue age, >20% failures over at
least ten completions, or >50% retried completions. These are operational tuning
thresholds, not publication-time promises.

## Telemetry

Check Prometheus target discovery, the named `metrics` port and both sides of the
NetworkPolicy. Validate `/metrics` remains unavailable on public/application ports.
Check exporter snapshot freshness before interpreting product counts. For browser
collection, inspect receiver outcomes and Alloy accepted/rejected payloads; for
profiles/traces, query actual recent samples, not just SDK initialization gauges.

Metrics retain the existing Prometheus 30-day history. Logs/traces/profiles retain
the cluster's configured retention and storage policies. **Pyroscope currently has
24-hour retention on ephemeral storage**: pod replacement discards profile history.
This integration does not silently replace that existing storage or promise durable
profile archives. Preserve WAL and Raft snapshots together during any later migration.

## Online index rollout

Revision 0006 changes indexes only. Version 0.1.0 explicitly accepts schema 0005
and 0006 in readiness, rejecting older and unknown future revisions. The public
version endpoint's `required_schema_revision` remains the target migration head.
Roll out the new images and compatible init checks while retaining the completed
0007 migration Job. Verify every old API/worker pod has retired, then publish the
new `devfeed-migrate-v0100` Job in a second Fleet commit. Verify schema 0006 and all
12 valid indexes. This sequencing avoids making 0.0.7 API replicas unready during
image pulls. Do not migrate first or roll back to 0.0.7's exact-revision readiness
without a corresponding migration/readiness plan.

## Validation and release gates

- Backend lint/types and unit/integration suites use explicitly disposable `_test`
  PostgreSQL and Redis database 15. Never run truncating fixtures against production.
- Frontend tests check receiver origin/body limits, privacy/cardinality, trace field
  filtering, distinct metrics ports and real HTTP status counts. Build both Next apps.
- `scripts/ci/profile-smoke.py` validates native Python profiling after fork and actual
  HTTP export. Image smoke checks exercise both profilers on Linux/ARM64 Alpine,
  separate metrics access and application-port 404 while collectors are unavailable.
- In home-lab, run `python scripts/check-devfeed-observability.py`: it validates dashboard
  PromQL and alert scenarios. Validate Alloy configuration, manifests and server dry run.
- Publish only after source/image/security gates pass. Pin immutable release digests,
  reconcile through Fleet, verify desired/applied deployment IDs, ready workloads,
  real Prometheus targets, probes, Loki logs, Tempo traces, Pyroscope profiles and Faro
  browser events. Verify public/source-map/metrics boundaries again after rollout.

## References

Design follows [Prometheus instrumentation guidance](https://prometheus.io/docs/practices/instrumentation/),
[metric cardinality principles](https://prometheus.io/docs/practices/the_zen/),
[Google SRE multiwindow alerting](https://sre.google/workbook/alerting-on-slos/),
[Grafana's Python profiling lifecycle](https://grafana.com/docs/pyroscope/latest/configure-client/language-sdks/python/),
[supported profiler platforms](https://grafana.com/docs/pyroscope/latest/configure-client/supported-platforms/),
[Faro tracing](https://grafana.com/docs/grafana-cloud/observe-and-act/monitor-applications/frontend-observability/instrument/tracing-instrumentation/),
and [Alloy Faro receiver boundaries](https://grafana.com/docs/alloy/latest/reference/components/faro/faro.receiver/).
