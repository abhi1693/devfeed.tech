# Native ClickHouse reporting comparison

Two million synthetic inference facts were measured on the same disposable
ClickHouse 26.8 server with 2 CPUs and 2 GiB of memory. The baseline is `f60ed15e`;
the candidate uses typed facts, canonical aggregate states, hourly serving
tables, and the complete new inference section. Both use two query threads;
the candidate enforces a 256 MiB query limit and shared reader/exporter budgets.

| Window  | Baseline median | Candidate median | Baseline bytes read | Candidate bytes read |
| ------- | --------------: | ---------------: | ------------------: | -------------------: |
| 1 day   |        192.0 ms |           8.9 ms |            271.9 MB |              10.4 KB |
| 30 days |        277.0 ms |          18.4 ms |            271.9 MB |             760.7 KB |
| 90 days |        424.5 ms |          33.0 ms |            271.9 MB |              1.10 MB |

For 30 days, the candidate is about 15 times faster and reads 357 times fewer
bytes. It reads 21,518 rows per section refresh instead of 4 million. Peak
individual query memory falls from 19.9 MB to 10.8 MB. These are local warm
measurements, using three baseline and five candidate repetitions. The candidate
executes two queries covering totals, time series, task/model/effort breakdowns
and approximate p50/p95; the old report contract differs.

The measured bootstrap includes real production publication and compaction:

| Work                                             | Wall time |     ClickHouse CPU time | Peak query memory |
| ------------------------------------------------ | --------: | ----------------------: | ----------------: |
| Typed insertion plus canonical materialized view |   38.59 s |                 22.07 s |           66.3 MB |
| Initial dirty queue                              |    0.27 s | Not measured separately |                 — |
| Hourly publication and compaction                |   45.95 s |                 25.47 s |           66.2 MB |

The publisher drained 8,761 hourly keys in 274 batches and ran 12 atomic
compactions. It produced 10,201 grouped hourly rows. At the configured 100 ms
bootstrap delay, these publication cycles imply about 27 seconds of polling
delays. The measurement does not include service sleeps or the full
PostgreSQL export. Fixture ingestion uses 91 bounded server-side key ranges;
two million JSON rows were not transferred through Python.

All-history and seven-day fixture aggregation attempts exceeded the enforced
256 MiB query limit. The successful production path rebuilds at most 32 selected
hourly cohorts per batch and one coverage day at a time. Regular changes coalesce
for up to 30 seconds; normal polling remains one second after bootstrap.

A separate current-state publication fixture measured 16 audience shards, with
one account and one feed observation per shard. Across three alternating local
rounds, sequential publication took a median 2.171 seconds; publishing the same
16 shards together took 0.140 seconds, a 15.48-fold improvement. Native calls fell
from 16 queries and 32 durable inserts to one query and two durable inserts.
Both paths used two threads, a 256 MiB query limit, and the default durable
asynchronous insert acknowledgement; the measurement did not shorten insert
durability waits. It measures refresh calls, excluding fixture preparation and
enqueue time. This small gauge fixture is separate from the two-million-fact
historical comparison above.

Fresh PostgreSQL capture measurements used eight alternating, rolled-back
trials on disposable data:

| Operation                                             | Capture disabled/inactive | Capture enabled | Analytical events |
| ----------------------------------------------------- | ------------------------: | --------------: | ----------------: |
| Update 1,000 inference rows, trigger disabled control |                  10.52 ms |        35.85 ms |             1,000 |
| Publish one recommendation generation                 |                  0.075 ms |        0.186 ms |                 1 |
| Replace 500 recommendation edges                      |                  12.62 ms |        14.17 ms |                 0 |
| Replace 500 edges and publish their generation        |                  13.27 ms |        12.95 ms |                 1 |

Inference capture adds about 25 microseconds per updated row in this fixture.
WAL rises from 562 KB to 1.79 MB per 1,000 updates; each event retains about 491
bytes before and 491 bytes after the change. Replacing 500 recommendation edges
and publishing their generation produces one event with 266 bytes before and
266 bytes after the change, with
about 209 KB of total WAL. The small recommendation timing differences are noise
within local samples; they do not establish a speedup. Old and new absolute
trigger timings use different populations and are not a paired comparison.

Full counters, bootstrap measurements, and fixture limits are recorded in
[`clickhouse-native-comparison.json`](clickhouse-native-comparison.json).
[`clickhouse-native-baseline.json`](clickhouse-native-baseline.json) retains the
original baseline measurements. All artifacts contain synthetic metadata only.
