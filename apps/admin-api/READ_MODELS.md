# Durable administration read models

Article and source boards read indexed PostgreSQL projections. Overview reads saved
reports. Board and Overview GETs never calculate lifecycle facts, rank the taxonomy, load
article bodies, or reconstruct historical reports. Redis is optional for these
saved reads. Partner reporting keeps its existing daily counters and live account
membership checks.

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

Overview GETs load the last complete saved result and coalesce refresh demand.
Cold reads immediately return structured `report_refreshing`; they never launch a
calculation. Workers refresh default and previously requested ranges independently
of viewers. Persistent leases and failure cooldowns survive restart. Complete
results survive Redis loss, failed refreshes, and API restart.

Processing charts use maintained daily job facts and active counts. Blocker reports
aggregate exact classifications from pipeline projections, preserving overlapping
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
start within this budget. These conditions defer blocker publication when over
budget. `catalog_lag_seconds` separately measures unfinished catalog work from its
queued/active start; a large progressing scan can exceed 60 seconds without
starving reports. At the default one-report-per-cycle, report work remains bounded
by the finite group/range demand queue. The report worker requests known snapshots
again at age 60 seconds; scheduling/SQL time is additional, so this is a refresh
target, not a hard latency SLA under saturation. Exceeding it is visible as stale
or refreshing saved data. Unavailable initial coverage remains a structured 503;
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
Report calculations use read-only repeatable-read transactions so a concurrent
policy cutover cannot mix a serving pointer with another generation's rows.

Partner identity registration occurs at login or once for an older session. Its
atomic Redis receipt preserves expiry and cannot recreate a revoked session.
Account authorization and session revocation remain live; saved analytics never
grant access.

## Workers and capacity

Run separate processes so slow reports cannot delay pipeline updates:

```sh
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
backfill-presence query. Alert on lag even with Ready HTTP pods.

## Migration and rollout

1. Apply additive migration `0034`. It installs capture and queues a bounded initial
   rebuild without enumerating the population in the migration. Two narrow review
   indexes support exact automatic/manual publication counts without detoasting
   review evidence on every report refresh. Their standard index builds scan
   existing evidence and briefly block writes to `article_reviews`; plan that
   migration window explicitly. The local 300,000-review fixture took about six
   seconds for both indexes; production build time is unverified.
2. Start both workers with reserved connection budgets. Complete the pipeline
   backfill and publish required reports before promoting the new readers. The
   pipeline worker's `--drain` option finishes initial work or reports a busy lease.
3. Compare stages, counts, grouping, ordering, retry controls, and blocker targets
   against the current lifecycle functions.
4. Deploy immutable images through Fleet and verify freshness, latency, database
   waits, errors, and partner authorization.

Schema `0034` is required. Previous `0.1.1` images use strict readiness checks and
do not accept this revision; release preparation must plan that compatibility
boundary. Do not assume an automatic zero-downtime schema rollout. Downgrade drops
derived state and its queue; keep capture enabled during backfill. This application
PR does not change production images, pooler roles, or Fleet workloads.

The catalog/report generation fix adds JSON state metadata and the `catching_up`
snapshot status without a migration or population-wide policy rebuild. Deploy the
pipeline worker before or with the report worker; an existing active scan is
stale until its first committed page records progress. Report policy `overview-v4`
requests replacement saved documents through the existing bounded demand queue.
Deploy regenerated admin clients/UI with the API to display the new status.
Rolling back to `overview-v3` retains last saved documents, which older readers
mark rebuilding until older workers replace them. It does not require deleting
derived rows. After deployment compare matched workload windows for publication
age, catalog progress/lag, report retries, latency, and write amplification; complete
native ARM64 PostgreSQL 17 validation before claiming production capacity.

## Reproducible scale checks

`scripts/profile_read_models.py` refuses non-local databases, database names without
the `_test` suffix, and Redis databases other than local database 15. `--seed`
replaces only the explicitly configured disposable database. Fixtures contain no
production records, sessions, or credentials. Fixture UUIDs use the same SHA-256
prefix in Python and PostgreSQL; reseed older MD5-based fixtures before profiling.

```sh
DEVFEED_TEST_DATABASE_URL=postgresql+psycopg://devfeed:password@127.0.0.1:55439/devfeed_profile_test \
DEVFEED_TEST_REDIS_URL=redis://127.0.0.1:56391/15 \
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
attention filters, a deeper lane page, source search/type filters, all 15 Overview
panel groups, main Overview, and the partner dashboard. Latency includes admitted
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
