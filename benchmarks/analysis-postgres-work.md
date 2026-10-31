# Analysis worker PostgreSQL work

One paired local x86_64 PostgreSQL 18 run per mode, using 2 CPUs and 2 GiB.
55,000 tags and 4,450 active topics; actual ordinary worker with nine specialist calls through guarded final apply, job-scope flag off versus on. Values below are **before → after**.

| Cache mode | Server SQL calls | Server rows | Application transactions | Shared hits/reads | Physical WAL bytes | Server plan + execution ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| disabled | 307 → 307 | 119035 → 119035 | 33 → 33 | 32139/43 → 32220/39 | 216436 → 216228 | 501.1 → 547.7 |
| cold | 306 → 306 | 59587 → 59587 | 33 → 33 | 31595/40 → 31601/39 | 216710 → 216335 | 431.5 → 452.2 |
| warm | 303 → 303 | 135 → 135 | 33 → 33 | 29553/40 → 29493/39 | 88362 → 174515 | 360.0 → 342.6 |
| outage | 309 → 306 | 119039 → 59587 | 33 → 33 | 32633/40 → 31080/39 | 216039 → 224274 | 534.0 → 427.1 |
| churn | 298 → 298 | 119034 → 119034 | 34 → 34 | 32273/6 → 32285/6 | 209685 → 210031 | 495.3 → 480.5 |
| disabled_churn | 296 → 296 | 119030 → 119030 | 34 → 34 | 31509/6 → 31516/6 | 210173 → 311167 | 493.6 → 505.8 |
| early_failure | 64 → 64 | 59481 → 59481 | 7 → 7 | 7268/1 → 7208/72 | 141848 → 141949 | 142.7 → 145.0 |

The tested SQL-call/row/transaction budgets do not increase. Disabled caching also
bypasses the added long-lived scope, so cold/churning/failed work does not add
revision queries merely to reuse Python objects. Existing transactional revision
checks and publication locks remain unchanged. With an enabled but unavailable
cache, retention avoids repeated authoritative catalog reads.

Each card page or worker transaction still reloads current source/job inputs.
RPC/HTTP/ClickHouse waits retain no application SQL connection. Peak checked-out
application connections are one in these serial fixtures; a separate autocommit
observer connection collects statistics. Raw maximum transaction durations,
client SQL time/calls/rows, buffer activity and WAL are retained in
[`analysis-postgres-work.json`](analysis-postgres-work.json).

The server totals include one fixed reset-observer call per sample. Tests assert
nonincreasing SQL calls/rows and transactions, plus matching outputs/outcomes and
no application connection at inference/HTTP boundaries; they do not assert timing
or physical-WAL equality. This is one sequential baseline/candidate pair with
warm-cache effects, without alternating rounds or a confidence interval. No
native ARM64, production CPU or resource-sizing claim follows from it.

Physical WAL bytes are shown even when they increase. These measurements do not
isolate checkpoint/full-page-image/hint-bit effects. PostgreSQL documents that
checksums can cause hint-bit updates to generate WAL; see the
[PostgreSQL 18 WAL documentation](https://www.postgresql.org/docs/18/runtime-config-wal.html).
The analysis fixture logged a time checkpoint at 14:02:35UTC, between its warm-mode
baseline and candidate executions; a second began at 14:07:35UTC. This coincides
with physical-WAL variation, and does not prove that every byte difference is
explained by checkpoints. There is no zero-WAL or absolute-CPU-neutrality claim.

`outage` injects `CacheUnavailable` at the cache transport boundary; database work
remains native. `churn` commits a relevant topic definition change during each
page/inference, including the same editor transaction in both measurements.
`disabled_churn` keeps caching off during that change. Ordinary analysis also
checks an early provider failure with normal durable retry handling.

The compact integration fixture runs in regular CI. Reproduce the server metrics
using explicit disposable `*_test` PostgreSQL and Redis 15 URLs and
`DEVFEED_TEST_POSTGRES_STATS=1` on a dedicated PostgreSQL 18 server preloaded with
`pg_stat_statements`, with planning and I/O timing enabled. Larger fixture sizes
are selected by the environment variables used in the commands below.

A separate existing compact private-maintenance fixture passed both flag modes
through guarded promotion, preserving publication identity and notification
receipts. It observed 539 → 539 server calls, 331 → 331 server rows, and
50 → 50 application transactions.
Every specialist callback asserted zero checked-out application connections.
This compact private result is separate from the 55k/4450 ordinary-worker fixture.

```bash
DEVFEED_ANALYSIS_PG_TAGS=55000 DEVFEED_ANALYSIS_PG_TOPICS=4450 \
uv run pytest -q -s tests/infrastructure/test_analysis_postgres_work.py
```
