# Historical importer PostgreSQL work

One paired local x86_64 PostgreSQL 18 run per mode, using 2 CPUs and 2 GiB.
55,000 tags, 4,450 active topics and 600 active article cards in three 200-row pages; cold direct builder versus retained historical importer. Values below are **before → after**.

| Cache mode | Server SQL calls | Server rows | Application transactions | Shared hits/reads | Physical WAL bytes | Server plan + execution ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| disabled | 58 → 58 | 181954 → 181954 | 3 → 3 | 54525/29 → 54520/0 | 8048 → 58 | 575.6 → 578.1 |
| cold | 58 → 58 | 63062 → 63062 | 3 → 3 | 53386/27 → 53362/0 | 146744 → 58 | 446.4 → 401.4 |
| warm | 55 → 55 | 3610 → 3610 | 3 → 3 | 51683/27 → 51664/0 | 7848 → 0 | 291.1 → 287.4 |
| outage | 64 → 58 | 181966 → 63062 | 3 → 3 | 56777/27 → 53362/0 | 146744 → 58 | 573.8 → 399.5 |
| churn | 72 → 72 | 181968 → 181968 | 5 → 5 | 58937/27 → 58834/0 | 148046 → 1611 | 604.7 → 607.8 |
| disabled_churn | 66 → 66 | 181956 → 181956 | 5 → 5 | 56712/27 → 56670/0 | 157460 → 1512 | 601.1 → 591.2 |

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
[`importer-postgres-work.json`](importer-postgres-work.json).

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
There is no zero-WAL or absolute-CPU-neutrality claim.

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

```bash
DEVFEED_CARD_CATALOG_TAGS=55000 DEVFEED_CARD_CATALOG_TOPICS=4450 \
DEVFEED_CARD_CATALOG_ARTICLES=600 DEVFEED_CARD_CATALOG_PAGE_SIZE=200 \
uv run pytest -q -s tests/admin/reporting/test_analytics_card_catalog.py
```
