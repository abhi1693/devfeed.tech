# Publication report scaling

Issue [#296](https://github.com/abhi1693/devfeed.tech/issues/296) concerns repeated review-index probes for each publication. The existing two partial review indexes already support the original predicates. The change separates publication totals and duration calculations from autonomous classification. The classification count uses existence predicates in its WHERE clause, allowing PostgreSQL to choose semi/anti joins, scan narrow review indexes once for broad windows or stop at the first match for selective windows. Repeated review facts cannot multiply either count. The publication cohort contains only IDs and timestamps. No historical evidence JSON or analysis job payload enters the report.

Manual evidence still takes precedence, regardless of the review action. Automatic evidence still requires a publish action and nonempty automation. Both kinds of evidence use each article's publication time as their inclusive cutoff; evidence may predate the reporting period. Counts include each article once, including articles with no matching evidence. The median retains its existing nonnegative-duration filter.

## Reproduce

Use a dedicated PostgreSQL 17 instance with a database ending in `_test`. The profiler refuses remote database hosts, connection query overrides and inherited libpq host/service overrides. `--seed` truncates articles and their dependent rows. Apply the repository's migrations to the disposable instance first, then run:

```sh
export DEVFEED_TEST_DATABASE_URL=postgresql+psycopg://profile:profile@127.0.0.1:55496/publication_report_test
export DEVFEED_DATABASE_URL="$DEVFEED_TEST_DATABASE_URL"
export DEVFEED_REDIS_URL=redis://127.0.0.1:6379/15
uv run alembic upgrade head
uv run python scripts/profile_publication_reports.py --seed --rows 100000 --repeats 7 \
  --output reports/publication/100000.json
uv run python scripts/profile_publication_reports.py --seed --rows 300000 --repeats 7 \
  --output reports/publication/300000.json
```

The `Publication report benchmark` workflow runs these populations on native `ubuntu-24.04-arm`, with PostgreSQL 17.11 limited to two CPUs and 3 GiB memory. It runs for relevant pull requests and accepts manual dispatch. Download the `publication-report-*-arm64-*` artifacts within seven days. Each artifact includes the exact SQL, source SHA, PostgreSQL architecture/version and settings, all timing/CPU samples and complete `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON)` plans. Source and report data remain separate: generated reports belong in the ignored `reports/` directory.

## Workload and interpretation

The 24 October 2026 read-only production audit estimated 92,503 articles, 255,529 reviews and 156,002 analysis jobs. The fixed synthetic fixtures contain 100,000 and 300,000 articles, 3.12 reviews per article and 1.65 historical analysis jobs per article. Reviews include manual-only, automatic-only, overlapping facts, reviews after publication, reviews older than the window, duplicate evidence and a tail of 27 reviews on one article in 200. Publications span 60 days, so the 30-day cohort contains approximately half of the population. Article summaries, review notes and job JSON retain payload outside the narrow report columns. All normal database triggers and indexes remain enabled; fixture inserts use batches of at most 5,000 article identities per statement (review fanout produces more review rows). Fixture setup ends with vacuum/analyze.

Each population exercises inclusive 1-, 7- and 30-day periods, both with and without a concurrent ingestion thread. The writer commits new unpublished articles, reviews and queued analysis jobs, with a five millisecond pause between commits. Those articles are outside the fixed report cohort, allowing exact result equality checks while ordinary writes, indexes and triggers compete for resources. Report queries use 4 MiB `work_mem`, disable parallel workers for attributable backend CPU measurements, and retain the server's JIT setting. Each strategy receives one unmeasured warmup, followed by measured samples and an instrumented plan. The baseline reproduces the correlated-existence SQL from commit `a483edec50fd331fea22c665e521e8a849cdf090`; the candidate compiles the actual application helper.

Compare root-plan shared-buffer hits/reads, temp blocks, WAL and review-node loop counts as well as elapsed time. Buffer hits are logical page accesses. PostgreSQL backend CPU comes from user/system ticks in that backend's `/proc/self/stat`, sampled through the disposable superuser connection; resolution is the host's clock tick. Application CPU is Python's calling-thread CPU around statement execution and result consumption, excluding the concurrent writer thread. Neither is an estimate of complete API request CPU. Writer throughput, commit latency and generated WAL are recorded separately; WAL includes ordinary checkpoint/full-page effects in the isolated service. Timing and writer samples are descriptive, without a flaky elapsed-time CI gate. Exact result equality is mandatory for every comparison. At 100,000 articles and above, each 30-day comparison additionally requires candidate buffer accesses below 75% of the baseline, no correlated SubPlan nodes, and total temporary read/write bytes below 512 bytes per publication. These structural budgets check repeated work and bounded intermediate data while allowing normal planner choices. `--inline-comparison` additionally profiles an inlined publication CTE when evaluating planner alternatives.

## Rollout and rollback

This change adds no indexes, triggers, stored facts or migration, so write paths and recovery semantics remain unchanged. Rolling back the application restores the original SQL without a database downgrade. After deployment, compare fresh statement-statistics deltas under matched report periods and ingestion workload; confirm report freshness, review precedence and duration metrics. Check PostgreSQL CPU, buffer work, temporary writes, ingestion latency and queue age independently. Local x86 runs validate the SQL and profiling mechanism; native ARM64 artifacts supply architecture-specific execution evidence. Future production diagnostics must remain bounded and read-only; this benchmark does not execute the changed query against production. Do not run these fixture or `ANALYZE` operations against production.
