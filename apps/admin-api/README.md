# DevFeed Admin API

Independent FastAPI administration/OIDC service, importing only the shared core
package. Source files live directly under `src`, mapped to `devfeed_admin_api`.

See the [admin UI guide](../admin/README.md) for supported operator workflows.

The admin home opens the article list. Historical Overview, search analytics, and
warehouse-backed article and source boards have been retired. Their former API
endpoints return HTTP 410. Operational lists, lifecycle details, and actions use
PostgreSQL. Source lifecycle detail and action paths below are relative to
`/v1/admin/source-pipeline`.

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
