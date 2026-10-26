# ClickHouse native reporting baseline

This records the versioned-row reporting implementation at `f60ed15e`, before the
native domain-stream and hour/day rollup redesign. The synthetic local run used
ClickHouse 26.8.35.10, ClickHouse Connect 1.11.0, a 2 CPU / 2 GiB ClickHouse container,
and a separate 512 MiB PostgreSQL container. It contains no production records.
The complete resource measurements are in
[`clickhouse-native-baseline.json`](clickhouse-native-baseline.json).

The initial fixture used `profile_read_models.fixture_counts(0.1)` with 64 bytes of
historical analysis evidence per row: 40,000 inference calls, 20,000 analysis jobs,
30,000 reviews, 50,000 topic links, and the corresponding related populations.
Each report/window was measured three times on warm data for 1, 30, and 90 days.
`system.query_log.log_comment` associated every completed statement with its
report; SQL comments alone did not survive bound-query rewriting. Resource totals
in the artifact cover three repetitions; latency is the median report wall time.

For the growth test, synthetic inference calls were extended to one million and
two million current rows, spread over 365 days, with stable bounded operation,
model, effort and token fields. Other domain populations stayed at the initial
fixture size.

| Inference data and query                          | 30-day report latency | Rows read per repetition | Bytes read per repetition |
| ------------------------------------------------- | --------------------- | ------------------------ | ------------------------- |
| Versioned facts, 1 million rows                   | 142.6 ms              | 2,000,000                | 135,919,998               |
| Versioned facts, 2 million rows                   | 277.0 ms              | 4,000,000                | 271,919,998               |
| Illustrative daily metrics table, 2 million facts | 4.0 ms                | 606                      | 44,446                    |

The raw report scans inference history twice, including its historical minimum
timestamp query. The illustrative daily table was built once in 613 ms and
returned 89 rows for 30 days. Its SELECT measures the grouped inference metrics
subsection; it excludes the raw report's separate topic outcomes and call-status
subqueries. It demonstrates the effect of date-granular serving tables, rather
than establishing full section equivalence. Peak individual query memory was
19.9 MB for the two-million-row raw report and 7.6 MB for the daily-table SELECT.

The initial export required 245 bounded cycles: 22.6 seconds of database/network
work without service sleeps, plus 245 seconds from the configured one-second busy
delay in the normal service loop. The first ten cycles used default insertion
settings; later cycles used a 50 ms asynchronous insert flush timeout to prepare
the query fixture. Their combined work time must not be described as default
production backfill throughput.

Eight alternating PostgreSQL trials updated the same 1,000 inference rows and
rolled each transaction back. Disabling only the analytical capture trigger
gave a median 17.31 ms and 564 KB of WAL; enabling capture gave 39.19 ms and
1,268 KB of WAL. Captured records averaged 465 bytes. An installed but inactive
capture trigger took 19.54 ms. These are bounded local write costs, not estimates
of production request latency; they motivate measuring before/after capture and
outbox volume alongside ClickHouse reads in the redesigned pipeline.

Use `python -m scripts.profile_analytics_reports --costs` against an explicitly
owned local `_test` ClickHouse database to collect native section latency,
result size, query duration, rows/bytes read and peak individual query memory.
The profiler refuses a cost artifact if query logging missed a submitted query.
