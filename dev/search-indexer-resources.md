# Search indexer connection ownership

`DEVFEED_SEARCH_INDEXER_CONNECTION_REUSE_ENABLED=false` preserves the configured
connection policy. The opt-in optimization applies to a NullPool baseline: reuse
one indexer-owned connection for consecutive active batches, with zero overflow.
Release it before every one-second idle wait, five-second error backoff, and
reconciliation's Typesense count calls. Release again after at most 20 active
batches so a busy indexer yields its session-pooler slot. Every process exit also
disposes the owned engine. Existing explicitly enabled generic pooling is preserved;
this change does not force extra disconnects on that configuration.

Sessions, transactions, batch sizes, advisory ownership, remote-write ordering,
fanout cursors, acknowledgement and reconciliation freshness retain their existing
boundaries. SQLAlchemy pre-ping, recycling and TCP deadlines still handle backend
loss. Reuse no longer retains a new PgBouncer backend assignment across idle waits.
The earlier persistent-idle-pool experiment is not the implemented behavior, and its
20-idle-connections-to-one result is not a performance claim for this version.

## PostgreSQL impact

Local x86-64, PostgreSQL 18, three repetitions per mode with alternating order.
The active fixture processes 200 visible tag documents in 20 ten-event batches;
the idle fixture performs 20 empty turns. Poll sleeps are excluded from CPU timing.
A disposable session-mode PgBouncer matches the production pool shape: maximum
10 backend connections, minimum one, 60 clients, 120-second server idle timeout,
and `DISCARD ALL` reset. The pool is warmed before measurement. No production
state was changed. Rows mean PostgreSQL rows returned or affected, including deletes.

A separate observer role reads `pg_stat_statements` with utility/nested statements,
planning and I/O timing enabled. Server logs count backend startups; a psycopg
cursor observes driver traffic omitted by SQLAlchemy application SQL events.
The installed SQLAlchemy psycopg dialect pings with an **empty `;` command**, not
`SELECT 1`; PostgreSQL statement statistics omit that command. The totals below
add those protocol round trips explicitly. Planning plus execution elapsed time
is not database CPU. Container CPU comes from cumulative cgroup counters and
includes background processes, logging and measurement probes.

| Metric | Before | After | Measurement basis |
| --- | ---: | ---: | --- |
| Active client process CPU | 0.1282 s | 0.1152 s | Median of three complete 20-batch session-pooler fixtures. |
| Active frontend connections | 20 | 1 | Same 200 documents; backend startups remain 0/0 in the warmed pool. |
| Active application statements | 80 | 80 | Same outbox, hydration and acknowledgement work. |
| Active recorded PostgreSQL commands + empty pings | 169 + 0 = 169 | 131 + 19 = 150 | Includes connection setup, transaction commands and pooler resets. |
| Active driver cursor commands | 104 | 104 | Configuration SQL and empty pings included; server statistics additionally capture implicit/utility work. |
| Active pooler resets | 20 | 1 | `DISCARD ALL` calls. |
| Active rows returned/affected | 622 | 622 | Includes 200 event rows, 200 documents and 200 acknowledged deletes. |
| Active shared buffer hits | 1,497 | 1,017 | Medians; no physical reads or temporary spill in these fixtures. |
| Active planning + execution elapsed | 35.242 ms | 23.049 ms | Medians, PostgreSQL statement statistics. |
| Active PostgreSQL-container CPU | 93.326 ms | 75.772 ms | Medians; includes probes/background work, not isolated SQL CPU. |
| Active delete WAL | 10,800 B | 10,800 B | Same 200 row deletes; total observed WAL including order-dependent heap pruning ranged 10,890–11,036 B in both modes. |
| Active transactions | 21 BEGIN / 20 COMMIT / 1 ROLLBACK | Same | Includes initial driver setup; transaction ownership is unchanged. |
| Idle frontend connections / recorded PG commands | 20 / 129 | 20 / 129 | Empty turns release before waiting; no idle connection saving is claimed. |
| Idle pooler clients/backends assigned to indexer | 0 / 0 | 0 / 0 | Sampled at all 20 wait boundaries; the pool retains one available backend in both modes. |
| Remaining locks / idle-in-transaction / lock waiters | 0 / 0 / 0 | 0 / 0 / 0 | Observed after each measured workload. |

Direct PostgreSQL controls also ran: active backend starts fell 20 to one, recorded
commands plus empty pings stayed 149 in both modes, and PostgreSQL-container CPU
medians fell 535.854 to 95.971 ms. That larger startup benefit does not describe the
production session-pooler topology. Direct idle turns retain 20 connections and
109 recorded commands in both modes. There is no migration or per-document SQL
added by this PR.

The regression suite checks real driver command budgets for idle/active work,
releases before waiting/backoff/remote counts, the 20-batch cap, configured-policy
preservation and recovery after terminating only the owned disposable backend:

```sh
uv run pytest -q -s tests/search/test_search_indexer.py \
  tests/search/test_search_indexer_database.py
```

Use only disposable `*_test` databases and Redis DB15. Keep the gate off until
native ARM64, shared-pool contention, remote latency, outbox age and throughput are
checked under representative load. Active bursts still own one backend while
working and can delay other waiting clients until the burst yields; the cap is not
a production pool-budget guarantee. No production requests, limits or manifests
change in this PR.

## Negative capacity result

A deeper session-pooler check sampled `SHOW POOLS` every 5 ms through a persistent
admin connection (`prepare_threshold=None`). Five paired active-burst comparisons
processed the same 200 documents in 20 batches. Empty-query pings and pooler resets
were still included in the PostgreSQL-work audit. Assigned slot-seconds estimate
the time a PgBouncer backend cannot serve another client; they are not CPU.
Sampling can miss short assignments, so these are estimates rather than exact
capacity accounting.

| Assigned backend occupancy | Before | After | Measurement basis |
| --- | ---: | ---: | --- |
| No extra remote delay | 0.2794 slot-s | 0.2830 slot-s | Median of five paired active-burst samples; mixed directions between pairs. |
| Fixed 5 ms remote sync delay | 0.3250 slot-s | 0.3648 slot-s | Median of five pairs; candidate higher in four pairs. Delay occurs inside the existing batch transaction in both modes. |
| Peak assigned backends / assigned backends during idle waits | 1 / 0 | 1 / 0 | Same disposable session-mode pooler. |

The pool can reduce SQL setup/reset work and database CPU while increasing backend
assignment time between active batches. The evidence does not establish neutral
shared-pool capacity. Do not promote this SQL-reuse proposal under a requirement to
avoid shifting pressure to PostgreSQL; the default-off gate alone is not proof that
the enabled behavior satisfies that requirement.
