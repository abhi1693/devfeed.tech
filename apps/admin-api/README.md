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

## Inference reporting totals

Migration `0031` backfills daily invocation totals and maintains them in the same transaction
as inference ledger inserts, updates, deletes and truncation. Full UTC days read those totals;
partial boundary days still read the ledger so arbitrary report windows remain exact. Numeric
validation, token subset accounting and the existing reporting budgets are unchanged.

The migration briefly blocks ledger writes while backfilling and installing statement triggers;
ordinary reads continue. It follows schema `0030`. Deploy the migration before relying on faster
reports; older compatible schemas use the original exact ledger query. Ledger writers incur
additional aggregate updates. Downgrade removes derived totals and triggers, retaining invocation
history. No API response or frontend change is required.

To reproduce report and writer profiles on a migrated disposable local database:

```bash
DEVFEED_TEST_DATABASE_URL=postgresql+psycopg://.../devfeed_profile_test \
  uv run python scripts/profile_inference_reports.py --seed --writes 100
```

`--seed` replaces existing test ledger/proposal/decision rows and their dependents. The
profiler refuses non-local hosts and database names without the `_test` suffix. It compares
complete 30/90-day report payloads and prints timings plus activity-query buffer statistics;
writer samples roll back their rows and temporary trigger changes. These local synthetic
measurements do not establish production latency.

## Completed-job token usage

The automation report aggregates each job's cumulative `usage.totalTokens` once on its
UTC completion date. Two streaming subqueries extract token text and validate its numeric
value once per job. Literal `OFFSET 0` fences prevent PostgreSQL from pulling those
expressions into every aggregate, while avoiding a full-window materialized working set.
Projecting the date before grouping also gives PostgreSQL 17 a small daily group estimate
instead of inheriting the near-unique completion timestamp distribution. This preserves
inclusive time boundaries, succeeded/failed jobs, missing or malformed telemetry,
durations, and the eighteen-digit token validity rule. It requires no migration or
additional writes; rolling back the application restores the previous query.

PostgreSQL maintains `OFFSET 0` as an explicit optimization fence; see the
[upstream planner change](https://www.postgresql.org/message-id/E1pK7VD-0059mE-Jc%40gemulon.postgresql.org).
Actual execution plans verify the extraction and validation projections, daily aggregation,
and temporary writes in both custom and generic plans.

To reproduce the before/after comparison on a migrated disposable local PostgreSQL 17:

```bash
DEVFEED_TEST_DATABASE_URL=postgresql+psycopg://.../devfeed_token_profile_test \
  uv run python scripts/profile_token_usage.py --seed --articles 100000 300000 \
    --output /tmp/devfeed-token-profile.json
```

`--seed` replaces articles, proposals and their dependent histories. Remote hosts,
connection routing overrides and database names without `_test` are refused. The tool
uses actual migrated tables and indexes, two retained article/topic jobs per article,
180 days of skewed completion history, failed/incomplete jobs, mixed telemetry and
incompressible 4 KiB provider metadata on every fifth job. The 100,000-article fixture
has approximately 60,000 article and 160,000 topic completions in a month, plus a
recent article burst. Research histories contain 73,000 and 219,000 jobs. Exact
read-only production counts at 10:05 UTC on 24 October 2026 were 92,598 articles,
156,021 article jobs, 159,575 topic jobs and 67,014 research jobs; planner and live-tuple
statistics are not exact population counts. The profiler records this comparison and
requires every 100,000+ article fixture to cover production's total histories and
qualifying 1/7/30-day counts for each job type. Smaller fixtures remain useful for
checking the full benchmark lifecycle locally.

For 1/7/30 UTC calendar-day windows, the tool checks exact result equality, alternating
warm samples, fully parameterized custom/generic plans, aggregate group estimates,
temporary writes and runtime with a noise allowance. It retains raw `EXPLAIN (ANALYZE,
BUFFERS, SETTINGS)` plans at 4 MiB `work_mem`, JIT enabled and up to two parallel workers.
Concurrent ingestion inserts articles and starts/finishes jobs after the fixed report
cutoff, preserving comparison results; samples report actual write overlap and latency.
Reporting-thread CPU is recorded separately from elapsed time. On Linux cgroup v2,
`--postgres-container CONTAINER` also records database-container CPU, including the
concurrent writer. Use an isolated container for that measurement.

The `Token usage performance` PR workflow runs the complete comparison on native
ARM64 PostgreSQL 17 and retains its JSON evidence for 30 days. Synthetic measurements
establish the query behavior at those fixture sizes; after deployment, compare fresh
production query statistics under matched traffic and watch report freshness, database
CPU and ingestion latency before attributing an improvement to the change.
