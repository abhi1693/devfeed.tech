# ClickHouse operator serving validation — 2026-10-27

This validates the PostgreSQL read-model retirement on the persistent local
`devfeed_scale_test` stack. It is an AMD64 local result, separate from production
ARM64 behavior and the [earlier PostgreSQL baseline](postgresql-read-model-baseline.md).

## Data and migration

PostgreSQL contains 187,580 articles, 1,063,982 inference calls, 80 accounts,
8,900 active topics, 99,908 tags, 176,594 relationship proposals, 12,022 topic
proposals, 2,812 sources, and 1,424 discovery candidates. These are the retained
production-scale fixture; this validation does not reseed or truncate it.

Before migration, the five legacy derived tables and native control state were
backed up with PostgreSQL 18 `pg_dump`. Migration 0042 removed all five tables,
their triggers and functions, and both projector services. Article/source ID
checksums and article, inference-call, account, and topic counts matched after
migration. Completed historical cursors and ClickHouse facts were preserved.

Both a fresh schema and a 0041 upgrade were tested in separate scratch databases.
The upgrade preserves pending format-1/2 event IDs, contents, and identity sequence;
new format-3 captures use the renamed queue to fence incompatible exporters.

Completed ClickHouse coverage contains 379,720 current cards: all 187,580
articles, 176,594 relationships, 12,022 topic proposals, 2,812 sources, and 712
unpromoted candidates. Promoted candidates are represented by their source cards.
All five backfills completed; pending capture, card refresh and fanout were zero.

## Serving and resource budgets

ClickHouse runs with two CPUs, 2 GiB memory, two query threads, and a 256 MiB
per-query limit. The normal exporter allocation is 0.5 CPU and 512 MiB. Initial
coverage used a temporary two-CPU, 1-GiB native exporter process with a shorter
asynchronous-insert barrier and merge-part backpressure. This uses the same
native lease, durable queues, builders, canonical cards and manifests against
the persistent databases. Bootstrap timings are not steady-state measurements.

Historical HTTP profiling used actual admin handlers, serialization, PostgreSQL,
and ClickHouse, with an explicit local authorization dependency override. It
excludes external OIDC, TLS and the Next.js proxy. Browser validation is separate.

| Historical workload | Requests | p50 | p95 | Maximum | PostgreSQL statements |
| --- | ---: | ---: | ---: | ---: | ---: |
| Cold, 11 independent historical charts | 11 | 276.7 ms | 345.4 ms | 346.1 ms | 0 |
| Cached, 16-client admission | 44 | 43.2 ms | 58.5 ms | 69.1 ms | 0 |

Cold historical requests coalesced into 17 ClickHouse queries. The cached burst
issued none. The shared driver never exceeded two concurrent queries.

The final status-fenced API was then profiled with all current data and optional
Redis report reuse bypassed for the cold-process measurement:

| Complete workload | Requests | p50 | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Cold, all 12 charts | 12 | 250.5 ms | 376.1 ms | 1,382.4 ms |
| Initial ungrouped/grouped/attention/source boards | 4 | 481.1 ms | 535.1 ms | 760.9 ms |
| Mixed charts and boards, 16 clients | 64 | 45.0 ms | 4,223.6 ms | 4,593.6 ms |
| Warm LAN gateway and real session validation, 16 clients | 64 | 73.1 ms | 4,430.7 ms | 4,643.9 ms |

All 80 handler requests and all 64 LAN requests returned 200. Cold charts issued
23 ClickHouse queries and zero PostgreSQL statements. The mixed burst issued 92
ClickHouse queries and 16 bounded PostgreSQL clock/URI metadata statements; board
state, counts, ordering and payloads came from ClickHouse. Driver concurrency
remained two. Tagged handler runs peaked at 16,913,946 bytes of query memory,
1,001,847 rows read, and 818 ms query execution, with no ClickHouse exceptions.

The LAN run used a temporary, valid Redis admin session through the running
Next.js gateway and API. It exercises session validation, not external OIDC login.
The session was revoked after validation. Actual browser checks rendered all 12
charts and both boards with no page errors. Access logs also captured the live
attention chart's automatic 503-to-200 recovery during bootstrap.

## Snapshot renewal lifecycle

A separate scratch ClickHouse database copied the actual 379,720 current rows
and retained seven daily shard generations: 2,658,040 rows and 518.2 MB compressed.
The persistent database was only read. Layouts returned identical results for
80 current-board/operations reads at 16 clients with two query slots:

| Layout and selector | p95 | Maximum | Failures |
| --- | ---: | ---: | ---: |
| One generation, original join | 2.861 s | 2.971 s | 0 |
| Seven generations, original join | 6.795 s | 7.648 s | 0 |
| Seven generations, exact tuple filter before merge | 3.345 s | 3.615 s | 0 |
| Seven generations, exact tuple filter after `OPTIMIZE FINAL` | 10.602 s | 11.073 s | 0 |

The final API adds exact `(kind, shard, generation)` tuple membership in the
storage filter, retaining the manifest join and readiness/version fence. Before
merge, `EXPLAIN` selected 55 of 344 granules and the largest query read 427,040
rows instead of 2,658,041. This cut aged burst p95 approximately in half.

After all parts were merged into one, coarse granules again selected all 343
marks. The largest query read 2,658,041 rows and 353.1 MB; peak query memory was
25.16 MB. All 170 sequential/concurrent reads still matched the baseline with no
exceptions. The heaviest concurrent read had only 0.93 seconds of headroom under
the 12-second deadline. Snapshot growth therefore remains a measured cost after
merge, not a constant-time claim.

This validates seven retained generations. Every dirty-shard publication also
creates a generation; the seven-day TTL does not impose a seven-version cap.
Higher write churn and production ARM64 performance are not certified by this
local test. The scratch databases were dropped after measurement.

## Discovered memory and CPU bottlenecks

The first 200-article active-work profile exceeded the original 512-MiB exporter
limit: a retained Docker reproduction exited 137 with `OOMKilled=true`. Reducing
only a global term-cache bound allowed completion but still reached the memory
ceiling, so that was rejected as the final fix.

The catalogue has 108,808 entries. Rebuilding its ranking index took 24.5 of
29.1 profiled seconds in one constrained batch. Prepared ranking is now reused
across batches only while PostgreSQL catalogue revisions match. Sparse positive
scores are sorted separately from lazily selected zero-score fallbacks; seeded
parity cases preserve the complete ranking and prompt-budget behavior.

Prepared indexes stream normalized terms and description vocabulary instead of
duplicating them in a 131,072-entry global memo. Prompt memoization is bounded by
both 2,048 entries and 2 MiB; result memoization remains eight bounded results.
The same source catalogue's prepared-process RSS fell from 460.1 to 376.8 MiB in
a separate read-only host probe. This is a memory observation, not a container
latency comparison.

Four different batches of 200 real unpublished/applied articles subsequently
passed at 0.5 CPU/512 MiB, with no OOM. Their cgroup peak was 475.3 MiB. A second
test forced catalogue replacement in process without changing database records:
weak references proved both old objects were released before replacement, and
the cgroup peak was 472.3 MiB. Direct term-cache occupancy stayed zero. Cold and
warm batches contain different work and are not interchangeable throughput samples.

## Recovery and contract checks

Native tests cover late commits and exact-ID acknowledgement, replay after a
canonical write but before manifest publication, superseded leases, deletions,
source fanout beyond 1,000 IDs, catalogue churn, timed transitions, policy rebuilds,
and TRUNCATE recovery while preserving historical facts. Source polling and
article clock-only updates cannot fan out across the article population.

Real PostgreSQL/ClickHouse integration covers board counts, retention, paging,
grouping, multi-origin filters, exact Unicode/literal search, private URI handling,
action-token guards, all chart contracts, and native historical ingestion. The
frontend suite and built-browser scenarios include automatic retry after a
preparing response. No reader or extension surface changed.

Final immutable native image checks compare installed Python source to the
checkout and exercise actual Alembic migration, both analytics readiness states,
fresh/stale health probes, all ten RQ queues, Typesense/indexer, scheduler, and API
schema rejection. These are local container gates, not a production rollout.

Finally, `docker compose up -d --build` retained all data and left all 17 enabled
services healthy. The normal exporter was restored to 0.5 CPU/512 MiB and remained
healthy with zero restarts. CH-UI returned 200 at the LAN address on port 4000.
