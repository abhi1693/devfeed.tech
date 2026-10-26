# DevFeed Admin API

Independent FastAPI administration/OIDC service, importing only the shared core
package. Source files live directly under `src`, mapped to `devfeed_admin_api`.

See [configuration, authentication, service boundaries and commands](../../docs/admin.md).

Pipeline and Overview pages use [durable read models](READ_MODELS.md). That guide
covers change capture, dedicated workers, freshness, rollout requirements, and
reproducible performance checks above production data counts.

The source lifecycle board is available at `GET /v1/admin/source-pipeline`;
`GET /columns/{stage}` pages one lane with an integer `cursor`. Both accept `q`,
`source_type`, `needs_attention`, `limit` (20 by default, at most 50), and
`terminal_since` (seven days by default, at most 90 days). Pending work and active
jobs remain visible regardless of age. Running cards come first, followed by
oldest waiting work; terminal lanes show the latest durable activity first.
Counts cover the entire filtered projection. Workers load evidence in bounded
batches; board reads do not load article bodies or job history.

Cards use `source:{id}` or `candidate:{id}` keys. Linked discovery candidates appear
under their Source once; unlinked candidates remain visible. Read-only details at
`GET /items/{kind}/{id}` expose current checks, task diagnostics, profile, assessment,
and recent reviews. A linked candidate detail resolves to its Source identity.
Reading any lifecycle endpoint creates no jobs or editorial decisions.

Explicit `POST /items/{kind}/{id}/advance` requires the returned
`expected_snapshot`. `POST /items/{kind}/{id}/retry` additionally requires an exact
`target` and its returned `job_id` (or null for a task with no prior run). Changed
facts return 409; callers reload and use the returned canonical identity. Advance
queues only the current next task or explicitly resumes an eligible paused Source.
An upstream retry pauses an active Source without changing approval or articles;
ingestion retries preserve enablement. Approval remains the attributed Source
review endpoint. The server returns `can_approve`, `can_ingest`, and
`can_advance`/`advance_label` so clients do not infer permissions from a lane name.

Rejected Sources missing current prerequisites offer an explicit "Reopen for review"
advance. It retains review history, requests fresh discovery/profile work, and keeps
the Source disabled until an operator makes a new approval decision. A standalone
rejected discovery candidate does not gain this Source-specific shortcut.

## Historical reporting

ClickHouse is the required store for historical Overview reports. The dashboard retains
publication, audience, personalization, source, processing, and inference panels. PostgreSQL
serves saved report documents and transactional operations; it does not calculate history
for reporting requests.

The analytical exporter captures narrow committed changes and sends them asynchronously.
A bounded initial backfill imports available history, including older aggregate-only reader
metrics. Token reports retain the distinction between cumulative completed-job usage and
individual inference invocations, including unknown and malformed telemetry.

See [durable read models](READ_MODELS.md) for Compose configuration, ingestion, query
budgets, freshness, backups, and rollout. The PostgreSQL report comparison profilers have
been replaced by native ClickHouse diagnostics in `scripts/profile_analytics_reports.py`.
