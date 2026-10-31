# Notification PostgreSQL work: experimental gate failed

Supported configuration accepts only batch size **1**. The controlled fixture
injects size 10 directly into the test settings to measure the prototype; operators
cannot enable it through environment/CLI startup. This draft is not ready for
activation. No production resource or worker setting was changed.

Each row below is one paired local x86_64 PostgreSQL 18 run with 2 CPUs/2GiB and
exactly 100 HTTPattempts in each mode. Values are **legacy1 → experimental10**.

| Scenario | Server SQL calls | Updated delivery rows | Application transactions | Shared buffer hits | WAL bytes | Server plan + execution ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| success | 1507 → 1507 | 300 → 300 | 201 → 201 | 59856 → 59859 | 189171 → 189171 | 631.2 → 630.2 |
| partial | 1507 → 1507 | 300 → 300 | 201 → 201 | 59863 → 59863 | 189343 → 189343 | 631.5 → 641.8 |
| http_outage | 1507 → 1507 | 300 → 300 | 201 → 201 | 60494 → 60494 | 191258 → 191258 | 630.5 → 637.5 |
| soft_timeout | 1507 → 2229 | 300 → 1004 | 201 → 262 | 59859 → 81578 | 189171 → 302982 | 647.0 → 882.9 |
| slow_outage | 1507 → 2229 | 300 → 1004 | 201 → 262 | 60494 → 83082 | 191258 → 305166 | 647.1 → 867.6 |
| redis_repair | 1613 → 1613 | 400 → 400 | 202 → 202 | 61706 → 61706 | 251873 → 251873 | 650.3 → 654.3 |
| rollback | 1511 → 1511 | 300 → 300 | 202 → 202 | 60197 → 60200 | 194571 → 194571 | 639.7 → 645.4 |
| redis_outage | 1511 → 1511 | 300 → 300 | 202 → 202 | 60200 → 60200 | 194571 → 194571 | 645.5 → 634.0 |
| rollback_regroup | 1511 → 1911 | 300 → 300 | 202 → 302 | 60234 → 80389 | 193376 → 198888 | 620.3 → 815.4 |

Soft-budget stops and slow destination outages repeatedly release and re-stamp
untouched rows. This adds PostgreSQL writes, transactions and WAL. A regrouped
rollback also adds duplicate-carrier lookups. Fewer Python process/client lifecycles
do not compensate for that database pressure. Accepted size one keeps the exact
legacy handler, transport identity and SQL path; higher values fail clearly at
startup. No SQL/lease/redispatch correctness check was weakened.

The fixture uses native RQ publication, list/intermediate membership, and durable
PostgreSQL rows. `partial` returns HTTP429 for every fifth attempt; `http_outage`
raises an immediate transport error. `soft_timeout` and `slow_outage` advance the
monotonic test clock 11 seconds per HTTP attempt; the latter also returns a transport
error. No wall-clock sleep is included. `rollback` follows the actual dispatcher
ordering; `rollback_regroup` changes the cohort ordering after rollback to expose
composition changes. All paths preserve 100 individual idempotency keys, retries
and attempts. Every HTTP callback asserts zero checked-out application database
connections; peak application checkouts stay at one.

`pg_stat_statements` supplies calls/rows/buffers/WAL/planning/execution. SQLAlchemy
hooks count application transactions, client calls, row counts and maximum
transaction duration. Server totals include one fixed reset-observer call in each
sample. The complete raw report preserves statements and connection/timing data:
[`notification-postgres-work.json`](notification-postgres-work.json). Timings are
one sequential local sample, without a confidence interval or production/ARM64
claim. Physical WAL and buffer differences are reported, not discarded.

Run the integration case using explicit disposable `*_test` PostgreSQL and Redis 15
URLs. CI runs a 20-delivery fixture without optional server-stat collection. For
these 100-delivery observations set `DEVFEED_PG_WORK_ITEMS=100` and
`DEVFEED_TEST_POSTGRES_STATS=1` on a dedicated PostgreSQL 18 server preloaded with
`pg_stat_statements`, with planning and I/O timing enabled and autovacuum disabled
for this owned benchmark only:

```bash
uv run pytest -q -s tests/notifications/test_notification_postgres_work.py
```

A future design should reuse a bounded notification-only child/runtime while
consuming existing individual RQ jobs, preserving their deadlines and identities.
It must pass the same success, partial-failure, slow-outage, repair and rollback
PostgreSQL budgets before any activation or resource-sizing recommendation.
