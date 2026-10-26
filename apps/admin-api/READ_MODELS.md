# Durable administration read models

Article and source boards read indexed PostgreSQL projections. Overview reads saved
reports. Board and Overview GETs never calculate lifecycle facts, rank the taxonomy, load
article bodies, or reconstruct historical reports. Redis is optional for these
saved reads. Partner reporting keeps its existing daily counters and live account
membership checks.

## ClickHouse historical analytics

ClickHouse is required. Overview fetches its twelve cards independently through
`/v1/admin/overview/charts/{chart}`. Each response contains only that card's series,
rankings, or controls. Six internal saved section documents supply these reads;
related cards share background calculation and refresh demand. Historical HTTP
reads never scan application records or raw ClickHouse facts. Current review and
recovery controls remain transactional.

### Facts, ingestion, and serving

```mermaid
flowchart LR
    app[Application transaction] --> capture[Durable narrow change queue]
    capture --> stream[Async exporter]
    stream --> raw[Typed analytical revisions]
    raw --> canonical[Idempotent canonical states]
    canonical --> buckets[Dirty hour and day summaries]
    buckets --> reports[Six saved section documents]
    reports --> endpoints[Twelve independent chart reads]
    endpoints --> charts[Overview cards]
```

`analytics_capture_events` captures selected fields and before-images atomically
with source writes. It contains no article bodies, prompts, credentials, account
names, or arbitrary inference payloads. The separate exporter acknowledges exact
source IDs only after durable ClickHouse insertion. Failures retain pending facts;
no main request contacts or waits for ClickHouse. Capture still costs PostgreSQL
writes and WAL; a prolonged outage grows this durable queue. Monitor its oldest
event and disk usage.

The analytical schema follows domains, rather than mirroring application tables:

| Data                                                                | Storage and purpose                                                                                 |
| ------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------- |
| Publication, audience, inference, completed attempts, and decisions | Typed revisions in `analytics_fact_events`, partitioned by capture month                            |
| Canonical facts                                                     | `argMaxState` by domain, immutable hour, and fact identity, partitioned by event month              |
| Dashboard history                                                   | Published hour/day summary generations and small manifests                                          |
| Current audience, topic relationships, and dimensions               | Narrow canonical states, grouped into 256 stable shards                                             |
| Current queues                                                      | Eight active-work counts, read through small PostgreSQL partial indexes and published to ClickHouse |
| Previous ClickHouse schema                                          | Cold archives retaining the same physical table UUIDs and available versions                        |

IDs exist for replacement, attribution, and replay; they are not dashboard grouping
dimensions. Task, model, effort, status, outcome, and content type use categorical
columns. Token counters are numeric at ingestion, including exact large charges
and cached/reasoning subsets. No report parses a JSON usage payload repeatedly.
Completed job attempts retain earlier failures when the durable job retries.
Decision-call ledger facts remain separate from inference totals, preventing
usage from being counted twice.

Incremental materialized views produce idempotent canonical states, not additive
counters from retryable inserts. Corrections publish tombstones for old time
cohorts and replacement facts for new cohorts. Dirty buckets coalesce for 30
seconds by default; their deadlines do not slide when more events arrive. A
publisher rebuilds a bounded cohort, writes its complete summary, then publishes
its generation manifest. Readers ignore unpublished and obsolete generations.
Serialized monthly summary compaction removes obsolete serving generations while
retaining raw history; it does not run `OPTIMIZE FINAL` over raw tables.

Summary states use bounded t-digest percentiles and estimated distinct readers.
The UI labels these estimates; exact financial and usage counters remain exact.
Charts return at most 168 hourly, 90 daily, 54 weekly, or 122 monthly points;
ranges longer than 90 days use weeks, and ranges longer than 366 days use months.
Source/topic rankings return
at most ten entries, and AI breakdowns return eight entries plus Other. Imported
history without event detail stays daily. Unknown readers, automatic publication,
and delay observations remain unavailable rather than becoming invented zeros.

Current-view cache generations expire after seven days and quiet shards renew
daily. The underlying analytical revisions and cold archives have no retention
TTL. This cache cleanup does not delete retained analytical detail.

Bootstrap coverage and ongoing freshness are separate. Initial publication waits
for source backfill, cold-history import, and initial summary/gauge coverage.
Later changes increase lag without forcing an endless bootstrap under continuous
traffic. Saved reports remain available during exporter or ClickHouse outages;
report workers never fall back to PostgreSQL historical aggregation.

### Resource isolation and Compose

ClickHouse defaults to two CPUs and 2 GiB, independent of PostgreSQL's allocation.
The exporter has its own 0.5 CPU and 512 MiB budget. Shared server profiles bound
all reader replicas together: two reader queries and 512 MiB total query memory;
the separate `devfeed_ingest` account gets three queries and 384 MiB. Individual
queries default to two threads, 256 MiB, and 20 seconds. The server reserves capacity
for ingestion with limits on SELECT, INSERT, total queries, and memory. Client
settings cannot raise the profile ceilings.

Configure these budgets with the `DEVFEED_CLICKHOUSE_*` and
`DEVFEED_ANALYTICS_EXPORTER_*` settings in `.env`. Set a separate
`DEVFEED_CLICKHOUSE_INGEST_PASSWORD`; Compose can initially inherit the normal
ClickHouse password when it is absent. Reader and exporter credentials are kept
separate in their services. ClickHouse has no published database ports; CH-UI uses
the private data network. Persistent database volumes must survive upgrades.

### Rollout

1. Build compatible application, exporter, and report-worker images first. Stop
   the old exporter, then apply migrations through `0041`.
2. Migration `0039` atomically renames capture state, cursors, and the durable queue,
   preserving pending records. An old exporter fails against the retired names
   instead of acknowledging the new format. `0040` adds gauge checkpoints and
   builds active-work covering indexes concurrently, without rebuilding history.
   `0041` replaces only saved-report caches with explicit date-window keys; source
   records, analytical facts, and cold archives remain intact.
3. Start ClickHouse with its resource profiles and the native exporter. Bounded
   keyset pages backfill beside live changes. Existing ClickHouse tables move to
   cold archives with their physical UUIDs preserved; available historical reader
   receipts and closed daily totals populate the native facts and archive tables.
   Current PostgreSQL entities take precedence over stale archived snapshots.
4. Wait for initial coverage before relying on new analytics. Start the operational
   pipeline worker and the report worker, then request the eleven 7/30-day defaults
   (one range-independent operations document and five analytical documents per
   range). Verify counts, lag, resource budgets, and source writes.

`0039` deliberately rejects schema downgrade: native capture and retained history
cannot be safely restored by silently reinstating PostgreSQL aggregation. Keep
PostgreSQL and ClickHouse backups and use a planned forward repair or coordinated
backup restore for rollback. Reverting application code alone does not reverse
this data migration. No production rollout is implied by local validation.

### Database management UI

Compose includes [CH-UI](https://github.com/caioricciuti/ch-ui) for database and
table browsing, SQL queries, saved queries, and dashboards. Open
`http://localhost:4000`. It connects to the existing ClickHouse privately at
`http://clickhouse:8123`; it does not introduce another ClickHouse instance. The
server's built-in query page remains private because ClickHouse ports are not
published. CH-UI availability does not gate the DevFeed APIs, workers, or migrations.

Sign in with your ClickHouse account. The first successful ClickHouse administrator
becomes the CH-UI administrator, and query permissions follow that account's
ClickHouse grants. CH-UI receives no DevFeed database, Redis, OIDC, or application
ClickHouse password through Compose; it verifies the credentials entered at login
on the server and retains encrypted sessions in its own SQLite database. The
Community edition provides the SQL editor, explorer, and dashboards. OIDC sign-in
and some operational/governance features require the upstream Pro edition.

`DEVFEED_CH_UI_BIND_IP`, `DEVFEED_CH_UI_PORT`, and `DEVFEED_CH_UI_URL` configure the
loopback binding, published port, and browser origin. The defaults bind only to
`127.0.0.1` on port `4000`. When using another origin, update `DEVFEED_CH_UI_URL`
and terminate HTTPS at the chosen reverse proxy. The UI's saved database endpoint
is configured on the server; arbitrary database URLs on the login page are disabled.

The `ch-ui-data` volume retains `/app/data/ch-ui.db`, dashboards, saved queries,
sessions, and the encryption key when it is generated automatically. A separate
`DEVFEED_CH_UI_SECRET_KEY` can supply the stable encryption key; it is never sent
to frontend services. Keep that key alongside a consistent SQLite backup made
with the upstream `ch-ui backup` command. Do not replace or discard it during an
upgrade. CH-UI runs as the image's non-root user with a read-only root filesystem,
a temporary PID file, and default limits of 0.5 CPU and 512 MiB. The image is pinned
to upstream v2.31.0 with an AMD64/ARM64 manifest digest.

## Change capture and publication

Migration `0034` adds pipeline projections, transactional events, worker state,
report snapshots, and refresh demand. Database triggers cover ORM, raw SQL, bulk
writes, owner changes, cascades, and truncation. Source jobs invalidate their own
source; actual source name/approval/enablement changes separately request bounded
article fanout.

Source-owned logo jobs capture every update, including queue dispatch and lease
renewals, because source action tokens include the full job. Article image jobs
retain selective capture so dispatch and lease bookkeeping do not rebuild cards.

The pipeline worker coalesces IDs within bounded batches and uses the existing
lifecycle functions. Article evidence connections close before Python evaluation.
Unchanged saved cards are not deleted and reinserted: conflict updates compare
their complete saved facts, avoiding unnecessary row, index, and TOAST rewrites.
Missing cards are still removed. Genuine coverage passes advance evaluation
timestamps even for unchanged cards, preserving the orphan-cleanup cutoff.
Projection writes, fanout continuations, and acknowledgement of the exact captured
event IDs commit together. Event IDs are not treated as commit order. A publisher
lease prevents an expired worker from overwriting its replacement. Crashes leave
unacknowledged events retryable.

Each worker retains one immutable catalog and its ranking indexes between batches,
checking transactional database revisions before reuse even with Redis caching
disabled. A changed revision replaces the previous generation. Candidate results
remain bounded to eight inputs; publication decisions are always derived again.

Queued retry timestamps schedule future projection changes. Running lease expiry
continues through existing scheduler recovery. Catalog invalidations use bounded
keyset fanout over saved article cards with catalog-dependent analysis or topic
links. Each catalog page limits examined saved cards before testing dependency
metadata, and advances past the last examined identity even if every card is
independent. It uses the existing policy/kind/identity primary key, without an
offset or a sensitivity filter that can scan an entire sparse population before
returning a small page. It does not scan historical articles or rebuild sources
and candidates.
Rejected cards and cards without either dependency are skipped; legacy cards
without dependency metadata are conservatively refreshed. Recent published cards
remain included because their retry controls depend on current analysis validity.
New matching terms, selected identities, and ranking changes can affect candidate
selection beyond existing assignments, so assignment-only invalidation is unsafe.
The authoritative candidate and lifecycle checks remain unchanged.

Edits during a catalog refresh are coalesced without rewinding its cursor. A
revision change at completion queues another bounded dependent-card refresh.
Tokenized continuations cannot revive a refresh superseded by a policy rebuild.
Code, prompt, image/logo, source-policy, and settings fingerprints cause policy rebuilds beside
the previous complete policy. Cutover follows complete entity coverage and bounded
orphan cleanup. Catalog edits during initial coverage queue a dependent-card
refresh after cutover, rather than restarting population-wide coverage. A global
invalidation after coverage has begun also schedules a follow-up pass, so concurrent truncation cannot
leave deleted cards behind. The initial policy event is covered by the first pass.
Obsolete policy rows and expired article terminal rows are pruned in bounded batches.

Dependency metadata is additive and does not change lifecycle policy or require
a schema migration. The first catalog refresh conservatively includes existing
saved cards without metadata and records their dependencies as they are evaluated;
it does not require another population-wide rebuild. Catalog refreshes retain
completed coverage and report `catching_up`, independently of true policy rebuilds.
Article and source views track their own last card-update times and relevant queued work; article catalog
refreshes do not mark source views rebuilding. Publisher inactivity still marks
saved views stale. The admin UI checks response age separately from the displayed
last card-update time, so healthy idle boards do not acquire a stale warning.

Increment `PIPELINE_POLICY_VERSION` when changing lifecycle or card derivation
without a corresponding prompt/settings version change. Increment the report
policy version when changing saved report semantics.

One board response uses a read-only repeatable-read transaction for counts and
cards. Source groups for all stages share one aggregate and windowed page query.
Workers normalize article search fields with PostgreSQL once, retaining separate
field boundaries and literal punctuation behavior; GETs do not normalize the
entire population. Pagination retains its existing live offset semantics. Article
`snapshot.generation` identifies the serving policy and a monotonically increasing
committed article observation; source generations retain their policy identity.
Neither is an immutable historical pagination snapshot. Publication,
retries, and source advancement still lock and validate authoritative records.
Item detail endpoints continue to load current authoritative evidence.

## Reports and freshness

Overview accepts `from` and `to` using Grafana-style date math or inclusive UTC
dates. The default is `from=now-29d/d&to=now`; for example,
`from=now-90d&to=now`, `from=now-2y&to=now`, or
`from=2024-11-01&to=2024-11-16`. Date math supports day/week/month/year offsets and
rounding. Responses identify inclusive display dates, derived day count, and
chart granularity. Windows contain at most 3,660 days and cannot include future
UTC dates. The operations section always represents current state and uses one
independent `current` key.

Saved analytical documents use resolved UTC date bounds, so equal-length custom
windows cannot overwrite each other and rolling presets receive a new key when
the UTC date changes. Summary queries filter the exact window before grouping;
weekly charts do not include counts from outside a custom interval.

Each chart GET loads the last complete saved document and returns its narrow card
payload. Requests for related cards coalesce demand for one internal section;
fetching twelve cards does not cause twelve ClickHouse calculations.
Cold reads immediately return structured `report_refreshing`; they never launch a
calculation. Workers calculate only requested reports; saved date ranges stop
refreshing when reads stop requesting updates. Persistent leases and failure
cooldowns survive restart. Complete
results survive Redis loss, failed refreshes, and API restart.

Custom windows do not accumulate permanent report caches. Demand receipts update
at most hourly. Every five minutes, bounded maintenance expires documents and
pending demand idle for seven days and evicts older keys toward a soft allowance
of 1,024 entries per table. Each pass deletes at most 100 entries per table,
protecting active leases and the current operations/7-day/30-day defaults.
Maintenance touches only indexed saved-report metadata; retained ClickHouse facts
and archives have no new retention limit.

ClickHouse processing charts aggregate exported job facts. The operations section
read exact classifications from pipeline projections, preserving overlapping
counts and recovery targets. Reports can publish after complete entity coverage
while catalog catchup continues. Each calculation records the observed article
generation, policy, completed catalog-pass counter/revision, and latest observed
catalog target. Counts and bounded revision-bearing targets come from that same
database snapshot. A completed catalog pass is a coverage watermark: batches use
the catalog current when evaluated, so it does not promise that all cards used one
historical taxonomy revision. During a scan or a queued successor, results remain
explicitly `catching_up`; they are never labelled catalog-complete or `fresh`.

The operational freshness budget is 60 seconds. `lag_seconds` covers unapplied
article/source-fanout/policy events, due transitions, publisher inactivity, and the
time since an active catalog scan last committed a page. A queued scan must also
start within this budget. These conditions defer operations publication when over
budget. `catalog_lag_seconds` separately measures unfinished catalog work from its
queued/active start; a large progressing scan can exceed 60 seconds without
starving reports. At the default one-report-per-cycle, report work remains bounded
by the finite group/range demand queue. Subsequent reads request expired snapshots;
scheduling and calculation time is additional, so freshness is a refresh
target, not a hard latency SLA under saturation. The API exposes this through snapshot metadata and response headers; the Overview
keeps per-panel freshness timestamps out of the chart layout. Unavailable initial coverage remains a structured 503;
incompatible coverage remains rebuilding. Source freshness is independent of
article catalog scans.

Expected dependency waits retry after five seconds and reset the failure counter;
actual calculation errors retain persistent exponential cooldowns up to 900
seconds. An expired report lease cannot publish. Publication retains the policy
captured before calculation and rejects policy changes, older article observations,
and older calculation timestamps without requiring equality with a moving current
article generation. The latter would recreate starvation under ingestion. Reads
retain the saved generation identity, age operational/catalog lag separately, and
never relabel an older report as a newly observed generation. A failed refresh
keeps the last complete result marked stale. Recovery actions still lock and check
the live editorial revision and evaluate current authoritative policy.

Responses expose calculation time, observed generation, freshness status, and lag.
Operational blocker reads use a repeatable-read PostgreSQL transaction so a concurrent
policy cutover cannot mix a serving pointer with another generation's rows.
ClickHouse reports expose an exporter observation watermark; independently
replicated tables do not form a serializable PostgreSQL transaction snapshot.

Partner identity registration occurs at login or once for an older session. Its
atomic Redis receipt preserves expiry and cannot recreate a revoked session.
Account authorization and session revocation remain live; saved analytics never
grant access.

## Workers and capacity

Run separate processes so historical export and reports cannot delay pipeline updates:

```sh
uv run python -m devfeed_core.analytics_worker
uv run python -m devfeed_admin_api.projection_worker --kind pipeline
uv run python -m devfeed_admin_api.projection_worker --kind reports
```

Compose includes both workers using the admin API image, without public ports or
identity-provider credentials. Match their AI/image/publication settings to the API.
`DEVFEED_READ_MODEL_DATABASE_URL` can select a separately budgeted database login
and PgBouncer pool. Production must reserve interactive capacity, accounting for
replicas, rollout surge, and failover. A second URL into the same saturated pool
does not reserve capacity. Each worker performs one calculation at a time using
short-lived connections.

If the exporter uses a separate PostgreSQL login, grant source-table reads for
backfill and the source-table `SHARE` locks used during capture activation. It
also needs exporter-state/cursor updates, outbox reads and exact-ID deletes, and
usage of `analytics_export_generation`. Source writers need outbox inserts,
usage of the outbox identity sequence, and reads of the capture-state row because
capture executes inside their transaction. The Compose migration owner already
has these privileges. ClickHouse schema changes must accompany explicit analytical
allowlist changes; application migrations do not automatically infer future
ClickHouse columns from arbitrary source fields.

Batch size defaults to 200 and polling to one second. Keep batches below the
120-second publisher lease. Worker health requires a recent successful heartbeat.
Passing 100,000 historical articles does not change this algorithm or trigger a
rebuild. Catalog refresh cost follows retained article cards: nonterminal work
plus seven days of terminal cards. It examines those identities once per pass
and recalculates only potentially dependent cards. Nonterminal backlog and the
taxonomy can still grow; sustained catalog changes may require consecutive
passes. Batching bounds individual work, rather than promising constant total
refresh time or unlimited throughput. Source-change fanout retains its existing
source-origin keyset and is separate from catalog refresh.
Each publisher atomically writes its own marker in the shared runtime helper's
owned, private directory (`devfeed-runtime-<uid>` under the system temporary
directory). Existing links are replaced without touching their targets; public
or unowned runtime directories are rejected by both the publisher and its probe.
OTel exposes `devfeed.read_model.pending`, `devfeed.read_model.oldest_event_age`,
and background duration/failure/last-success metrics. Exact queue aggregates are
sampled at startup and at most every 30 seconds, independently of per-cycle
publication and health heartbeats. Completed coverage also skips the queue-wide
backfill-presence query. Alert on lag even with Ready HTTP pods. The exporter samples the indexed oldest
outbox age as `devfeed.read_model.oldest_event_age{kind="analytics"}` without counting
the backlog, including while ClickHouse is unavailable. Its generic background
cycle is `analytics.export`; metric dimensions do not contain entity identifiers.

## Migration and rollout

The ClickHouse rollout above is the historical-reporting cutover. Existing
pipeline projections still use the earlier operational migrations; migrate the
whole database to the application-required head before starting compatible images.
Ordinary login, source writes, approvals, and targeted recovery do not wait for
ClickHouse network operations. Only Overview analytical preparation depends on
initial native coverage.

## Reproducible scale checks

The `Catalog refresh scale` workflow measures the current architecture on a native
ARM64 runner with PostgreSQL 17 and a 150,000-article fixture. It
checks report publication during catalog catchup, recovery, lifecycle parity, and
bounded workload evidence. The summary reports current measurements and links the
exact commit, run attempt, JSON, and SQL plans; it does not claim a paired baseline
speedup. Missing or partial results fail the check and remain unavailable. The
renderer can still read saved historical paired artifacts as data.

The native report profiler runs the five analytical sections directly against a
prepared disposable ClickHouse fixture, without connecting to PostgreSQL:

```sh
DEVFEED_TEST_CLICKHOUSE_URL=http://127.0.0.1:8123 \
DEVFEED_TEST_CLICKHOUSE_DATABASE=devfeed_profile_test \
DEVFEED_TEST_CLICKHOUSE_USERNAME=ci \
DEVFEED_TEST_CLICKHOUSE_PASSWORD=fixture-password \
uv run python -m scripts.profile_analytics_reports --group inference --group processing \
  --from now-3M --to now --repeats 5 --concurrency 2 --max-queries 200
```

It emits report durations, response sizes, query counts, and, with `--costs`, read rows,
read bytes, memory, and server durations, with bounded dates,
repeats, concurrency, and total queries. These are synthetic local measurements,
not production latency claims.

`scripts/profile_read_models.py` refuses non-local databases, database names without
the `_test` suffix, Redis databases other than local database 15, and ClickHouse
origins/databases outside explicitly local `DEVFEED_TEST_CLICKHOUSE_*` targets. `--seed`
replaces only the explicitly configured disposable database. Fixtures contain no
production records, sessions, or credentials. Fixture UUIDs use the same SHA-256
prefix in Python and PostgreSQL; reseed older MD5-based fixtures before profiling.

```sh
DEVFEED_TEST_DATABASE_URL=postgresql+psycopg://devfeed:password@127.0.0.1:55439/devfeed_profile_test \
DEVFEED_TEST_REDIS_URL=redis://127.0.0.1:56391/15 \
DEVFEED_TEST_CLICKHOUSE_URL=http://127.0.0.1:8123 \
DEVFEED_TEST_CLICKHOUSE_DATABASE=devfeed_profile_test \
DEVFEED_TEST_CLICKHOUSE_USERNAME=ci \
DEVFEED_TEST_CLICKHOUSE_PASSWORD=fixture-password \
uv run python scripts/profile_read_models.py --seed --scale 1 \
  --history-bytes 8192 --repeats 50 --output /tmp/read-model-profile.json
```

Defaults exceed the measured production population: 150,000 articles, at least
30,000 active articles, 200,000 analysis jobs, 300,000 reviews, 500,000 topic links,
one million tag links, 60,000 tags, and 400,000 inference calls. Fixtures include
current/old analysis, delayed/running work, multiple origins, and large evidence.

The script builds real projections, checks sampled lifecycle parity, and measures
authenticated ASGI endpoints with caching disabled at concurrency 8 and 16. Output
includes p50/p95/p99, SQL counts, errors, response sizes, dataset/payload sizes,
backfill time, and worker-pause/replay behavior. Cold unpublished reports are
reported separately. Local measurements exclude ARM64 production hardware,
ingress, browser rendering, network, and session-pooler failover; live verification
remains a separate release gate.

Published phases fail the script if any response fails, a route exceeds its SQL
query ceiling, or p95 exceeds 1,000 ms (`--p95-budget-ms` overrides this budget).
Fifty samples per route cover positive/empty article searches, source filters,
attention filters, a deeper lane page, source search/type filters, all six Overview
sections and the partner dashboard. Latency includes admitted
ASGI, thread-pool, and database waits; it excludes the load generator's concurrency
queue. PostgreSQL buffers are warmed by backfill and oracle checks.

The first two published phases pause workers after publication. A separate phase
runs independent pipeline and report processes alongside up to 400 ordinary
article edits, each using its own database engine. This exercises competition in
the local database; production connection reservation still needs its own proof.

The 2026-10-21 local fixture uses the following populations. Production numbers
come from the read-only incident audit; historical-table figures include planner
estimates. Synthetic counts are checked directly, and a smaller `--scale` is
reported as a functional smoke check instead of production-scale evidence.

| Population            | Production audit | Synthetic fixture |
| --------------------- | ---------------: | ----------------: |
| Articles              |           86,248 |           150,000 |
| Active articles       |           15,492 |            30,000 |
| Analysis jobs         |          141,103 |           200,000 |
| Enrichment jobs       |           98,244 |           150,000 |
| Image jobs            |           86,929 |           150,000 |
| Reviews               |          236,064 |           300,000 |
| Origins               |           87,615 |           151,000 |
| Ingestion jobs        |           90,914 |           150,000 |
| Article content       |           83,251 |           100,000 |
| Topics                |            4,450 |             5,000 |
| Tags                  |           46,162 |            60,000 |
| Article-topic links   |          341,241 |           500,640 |
| Article-tag links     |          808,914 |         1,000,000 |
| Inference calls       |          328,015 |           400,000 |
| Sources               |            1,385 |             2,000 |
| Source candidates     |              712 |             1,000 |
| Partner assets        |              456 |               500 |
| Tracked daily metrics |              689 |             1,000 |

The local database occupies about 5 GiB including indexes. A 256-row analysis
sample averages 7,451 bytes of input JSON and 7,492 bytes of catalog JSON; 640
articles use current analysis identities alongside the older job history.
The host is an x86_64 VMware guest with 16 exposed Ryzen Threadripper 3970X
vCPUs and 30 GiB RAM, running PostgreSQL 18.6 with 128 MiB shared buffers.

## 2026-10-21 local results

These are historical measurements of the previous read-model contracts; the
Overview routes below have since been replaced. Current native ClickHouse
measurements live in [the native benchmark](../../benchmarks/clickhouse-native-baseline.md).

The complete run passed all 4,350 published requests: 50 samples for each of
29 routes in three phases. Every route met the 1,000 ms p95 budget and its query
ceiling. There were no HTTP errors in those phases. Values below are milliseconds;
the 16-concurrency column gives p50 / p95 / p99 for each route.

| Route                             | Concurrency 8 p95 | Concurrency 16 p50 / p95 / p99 | With publishers p95 | Max SQL |
| --------------------------------- | ----------------: | -----------------------------: | ------------------: | ------: |
| `article-attention`               |               532 |                473 / 751 / 785 |                 864 |      13 |
| `article-cards`                   |               459 |                427 / 628 / 775 |                 812 |      13 |
| `article-column`                  |               156 |                241 / 497 / 546 |                 400 |       6 |
| `article-deep-column`             |               155 |                277 / 526 / 625 |                 734 |       6 |
| `article-grouped`                 |               481 |                458 / 750 / 759 |                 745 |       6 |
| `article-search`                  |               491 |                459 / 777 / 802 |                 860 |      13 |
| `article-search-empty`            |               554 |                481 / 771 / 831 |                 897 |      13 |
| `article-source`                  |               550 |                487 / 756 / 834 |                 899 |      13 |
| `overview-adoption`               |               107 |                197 / 453 / 522 |                 544 |       5 |
| `overview-attention`              |               119 |                188 / 511 / 557 |                 312 |       5 |
| `overview-blockers`               |               116 |                216 / 271 / 443 |                 542 |       5 |
| `overview-decision-efficiency`    |               106 |                153 / 232 / 523 |                 238 |       5 |
| `overview-feed-health`            |               106 |                172 / 483 / 504 |                 517 |       5 |
| `overview-inference-tokens`       |               109 |                162 / 234 / 498 |                 237 |       5 |
| `overview-job-tokens`             |                99 |                150 / 228 / 431 |                 250 |       5 |
| `overview-main`                   |               129 |                188 / 522 / 566 |                 551 |       5 |
| `overview-popular-articles`       |               106 |                151 / 414 / 476 |                 240 |       5 |
| `overview-publication-automation` |               107 |                148 / 242 / 441 |                 255 |       5 |
| `overview-publication-time`       |               112 |                194 / 504 / 533 |                 512 |       5 |
| `overview-publications`           |               111 |                211 / 255 / 586 |                 532 |       5 |
| `overview-sources`                |                93 |                155 / 435 / 466 |                 264 |       5 |
| `overview-throughput`             |               117 |                160 / 208 / 417 |                 241 |       5 |
| `overview-topic-coverage`         |               103 |                164 / 453 / 462 |                 285 |       5 |
| `overview-workload`               |               116 |                193 / 321 / 536 |                 509 |       5 |
| `partner-dashboard`               |               182 |                251 / 543 / 553 |                 471 |       6 |
| `source-attention`                |               243 |                390 / 689 / 723 |                 765 |      14 |
| `source-board`                    |               235 |                421 / 712 / 744 |                 794 |      14 |
| `source-search`                   |               301 |                438 / 730 / 772 |                 825 |      14 |
| `source-type`                     |               247 |                407 / 718 / 741 |                 760 |      14 |

SQL counts use SQLAlchemy statement events; connection initialization, pre-ping,
and transaction protocol are excluded from that count and included in latency.
The worst route p95 was 554.385 ms at concurrency 8, 776.530 ms at concurrency 16,
and 898.757 ms with publishers. The competition phase reached 1,046.945 ms p99;
the configured gate applies to p95.

A clean rebuild produced 55,000 retained article rows, 2,000 source rows, and
500 unlinked-candidate rows in 390.857 seconds. The worker published all 16
requested reports in 7.461 seconds. The competition phase committed 400 ordinary
article edits, acknowledged all 400 events, and published 146 reports. During the
separate pause check, 100 duplicate events remained queued after all GETs and
drained in 1.600 seconds. Before and after replay, 32 sampled cards across 16
kind/stage groups matched the independent legacy board/lifecycle reference.

The profile process peaked at 370.4 MiB RSS; publisher subprocess high-water
marks may include process-launch inheritance and are not steady-state memory claims.
The final database was 5.18 GiB; pipeline projections occupied 150.1 MiB including
indexes and all report snapshots occupied 160 KiB. The original real-trigger
fixture load recorded 928.093 seconds across 23 table insert stages, excluding
commit/current-analysis setup; fixture setup is outside API latency measurements.

The reproducible local artifact is `/tmp/devfeed-read-models-profile-final.json`
with `complete: true` and `gates.passed: true`; its companion `.log` retains
per-request progress. These disposable artifacts are intentionally untracked.
This validates the local synthetic workload; production ARM64, PgBouncer capacity,
ingress, and browser performance remain deployment verification gates.
