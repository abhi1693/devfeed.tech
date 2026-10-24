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
uv run python scripts/profile_publication_reports.py --seed --rows 100000 --repeats 7 --prepared \
  --output reports/publication/100000.json
uv run python scripts/profile_publication_reports.py --seed --rows 300000 --repeats 7 --prepared \
  --output reports/publication/300000.json
```

The `Publication report benchmark` workflow runs these populations on native `ubuntu-24.04-arm`, with PostgreSQL 17.11 limited to two CPUs and 3 GiB memory. It runs for relevant pull requests and accepts manual dispatch. Download the `publication-report-*-arm64-*` artifacts within seven days. Each artifact includes the exact SQL, source SHA, PostgreSQL architecture/version and settings, all timing/CPU samples and complete `EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON)` plans. Source and report data remain separate: generated reports belong in the ignored `reports/` directory.

## Workload and interpretation

The 24 October 2026 read-only production audit estimated 92,503 articles, 255,529 reviews and 156,002 analysis jobs. The fixed synthetic fixtures contain 100,000 and 300,000 articles, 3.12 reviews per article and 1.65 historical analysis jobs per article. Job timestamps follow the owning article's discovery time and include retained input JSON; job rows do not enter either report query. Reviews include manual-only, automatic-only, overlapping facts, reviews after publication, reviews older than the window, duplicate evidence and a tail of 27 reviews on one article in 201. Those extra reviews are matching publish facts, so the tail exercises repeated qualifying evidence. Publication ages use `(n / 10) % 60` while review categories use `n % 10`, keeping the evidence mix independent of the reporting period. The tail interval is coprime with the ten review categories. Publications span 60 days, so the 30-day cohort contains approximately half of the population. Article summaries, review notes and job JSON retain payload outside the narrow report columns. All normal database triggers and indexes remain enabled; fixture inserts use batches of at most 5,000 article identities per statement (review fanout produces more review rows). Fixture setup ends with vacuum/analyze.

Each population exercises inclusive 1-, 7- and 30-day periods, both with and without a concurrent ingestion thread. The writer commits new unpublished articles, reviews and queued analysis jobs, with a five millisecond pause between commits. Those articles are outside the fixed report cohort, allowing exact result equality checks while ordinary writes, indexes and triggers compete for resources. Report queries use 4 MiB `work_mem`, disable parallel workers for attributable backend CPU measurements, and retain the server's JIT setting. Each ordinary strategy receives one unmeasured warmup, followed by measured samples and an instrumented plan. The concurrent writer must complete its first transaction before report measurement starts. The baseline reproduces the correlated-existence SQL from commit `a483edec50fd331fea22c665e521e8a849cdf090`; the candidate compiles the actual application helper.

Compare root-plan shared-buffer hits/reads, temp blocks, WAL and review-node loop counts as well as elapsed time. Buffer hits are logical page accesses. PostgreSQL backend CPU comes from user/system ticks in that backend's `/proc/self/stat`, sampled through the disposable superuser connection; resolution is the host's clock tick. Application CPU is Python's calling-thread CPU around statement execution and result consumption, excluding the concurrent writer thread. Neither is an estimate of complete API request CPU. Writer throughput, commit latency and generated WAL are recorded separately; WAL includes ordinary checkpoint/full-page effects in the isolated service. Timing and writer samples are descriptive, without a flaky elapsed-time CI gate. Exact result equality is mandatory for every comparison. At 100,000 articles and above, each 30-day comparison additionally requires candidate buffer accesses below 75% of the baseline, no correlated SubPlan nodes, and total temporary read/write bytes below 512 bytes per publication. These structural budgets check repeated work and bounded intermediate data while allowing normal planner choices. `--inline-comparison` additionally profiles an inlined publication CTE when evaluating planner alternatives.

## Prepared-plan stability

Unknown date bounds can make PostgreSQL estimate a broad reporting window as a selective one when reusing a generic prepared plan. A disposable 100,000-article experiment reproduced that regression: the set-based query returned to per-article review probes under a generic plan. The application renders only its datetime window bounds through SQLAlchemy's typed `literal_execute` mechanism, keeping those constants visible to the planner while ordinary values such as the percentile remain bound. The date serializer handles quoting; no raw request text becomes SQL. This scope preserves the connection's and server's normal plan-cache settings. The prepared-plan benchmark compiles with `render_postcompile=True` to inspect the same statement shape sent by the driver.

## Native ARM64 results

Measured source [`c3211deec1ebaa204bc5395868905f0d4fa69b9a`](https://github.com/abhi1693/devfeed.tech/commit/c3211deec1ebaa204bc5395868905f0d4fa69b9a), [workflow run 40083944048](https://github.com/abhi1693/devfeed.tech/actions/runs/40083944048), completed 24 October 2026. Both jobs passed on PostgreSQL 17.11 `aarch64-unknown-linux-gnu`. The 100,000-article fixture has 311,928 reviews and 165,000 historical analysis jobs; the 300,000-article fixture has 935,808 reviews and 495,000 jobs. All 1-, 7- and 30-day comparisons preserved results, including forced custom, forced generic and warmed automatic prepared plans. All broad-period buffer, correlated-subplan and temporary-work guards passed.

Each cell shows baseline → candidate. Times are medians of seven measured executions. Buffer accesses include shared hits **and** reads. Backend CPU is serial PostgreSQL user/system CPU with a 10 ms clock-tick resolution; it is not whole-service CPU. Temporary writes are MiB reported by the instrumented plan.

| Articles | Days | Concurrent ingestion |       Median ms |   Buffer accesses | Backend CPU ms | Temp written MiB |
| -------: | ---: | :------------------- | --------------: | ----------------: | -------------: | ---------------: |
|  100,000 |    1 | No                   |     6.81 → 6.90 |     7,533 → 7,533 |        10 → 10 |        0.0 → 0.0 |
|  100,000 |    7 | No                   |   53.23 → 29.94 |   51,709 → 17,298 |        50 → 30 |        0.0 → 0.0 |
|  100,000 |   30 | No                   | 217.97 → 113.40 |  230,628 → 20,920 |      210 → 110 |        0.0 → 0.0 |
|  100,000 |    1 | Yes                  |     7.11 → 6.98 |     7,537 → 7,537 |        10 → 10 |        0.0 → 0.0 |
|  100,000 |    7 | Yes                  |   53.07 → 30.67 |   51,743 → 17,931 |        50 → 30 |        0.0 → 0.0 |
|  100,000 |   30 | Yes                  | 222.17 → 116.21 |  231,287 → 24,795 |      220 → 120 |        0.0 → 0.0 |
|  300,000 |    1 | No                   |   17.07 → 17.51 |   25,568 → 25,568 |        20 → 20 |        0.0 → 0.0 |
|  300,000 |    7 | No                   | 136.79 → 109.84 | 175,842 → 253,742 |      140 → 110 |        0.0 → 0.0 |
|  300,000 |   30 | No                   | 688.75 → 428.30 | 780,918 → 306,141 |      690 → 430 |       0.0 → 21.3 |
|  300,000 |    1 | Yes                  |   17.96 → 17.88 |   25,572 → 25,572 |        20 → 20 |        0.0 → 0.0 |
|  300,000 |    7 | Yes                  | 134.12 → 112.82 | 175,936 → 256,454 |      130 → 110 |        0.0 → 0.0 |
|  300,000 |   30 | Yes                  | 673.89 → 429.88 | 782,877 → 319,983 |      670 → 430 |       0.0 → 21.4 |

The 300,000-article seven-day candidate uses more logical buffer accesses despite lower ordinary-run latency and CPU. In warmed automatic prepared mode it measured 117.74 ms against 113.40 ms for the baseline, with 272,379 against 176,736 accesses. One-day report times are effectively unchanged at this sample size. This change does not promise an improvement for every period and planner mode.

The 300,000-article 30-day candidate adds about 21.3–21.4 MiB of temporary writes and the same amount of temporary reads; the baseline writes none. The combined 42.7–42.8 MiB is below the 73.2 MiB budget for its 150,000-publication cohort. Materialization performs less article work than the inlined alternative, which measured 528.73 ms steady and 535.86 ms with ingestion, compared with 428.30 and 429.88 ms for materialization. At 100,000 articles neither strategy spills.

The exact driver SQL retains the improvement after prepared-plan adoption. These are the 30-day results; the artifacts also retain all shorter-period plans and samples.

| Articles | Prepared plan mode |       Median ms |   Buffer accesses |
| -------: | :----------------- | --------------: | ----------------: |
|  100,000 | Forced custom      | 215.66 → 118.76 |  231,807 → 26,101 |
|  100,000 | Forced generic     | 168.74 → 116.20 |  222,202 → 26,101 |
|  100,000 | Warmed automatic   | 168.25 → 116.55 |  222,202 → 26,101 |
|  300,000 | Forced custom      | 663.70 → 431.11 | 784,734 → 324,778 |
|  300,000 | Forced generic     | 547.84 → 433.45 | 755,649 → 324,778 |
|  300,000 | Warmed automatic   | 567.32 → 436.38 | 755,649 → 324,778 |

Each warmed automatic statement recorded five custom executions followed by ten generic executions, including the instrumented plan. The forced modes recorded nine executions of their requested plan type. The candidate therefore preserves known window selectivity even when PostgreSQL chooses a generic plan.

At 100,000 articles, the concurrent 30-day writer measured 112.7 → 115.1 commits/s, 3.80 → 3.65 ms median commit time, and 24.3 → 22.0 KiB WAL per commit, with no errors.

At 300,000 articles, the concurrent 30-day writer measured 119.5 → 121.6 commits/s, 3.26 → 3.10 ms median commit time, and 39.9 → 34.7 KiB WAL per commit, with no errors.

The Python calling-thread CPU medians span 0.32–0.74 ms for the baseline and 0.24–0.60 ms for the candidate. All report plans record zero WAL bytes. Writer WAL differences include ordinary full-page/checkpoint effects and are descriptive. The short 300,000-article one-day candidate writer interval contains only 18 commits and one 35.4 ms maximum commit, producing 86.0 commits/s; this sample does not establish a sustained ingestion regression or a production percentile.

These fixtures match representative job cardinality with smaller retained job payloads: a local 165,000-job fixture occupied about 182 MB, while the production audit estimated 2.45 GB for 156,002 jobs. Those are total relation footprints, including indexes. Neither publication query reads job payloads. The benchmark establishes publication/review query behavior under concurrent ordinary inserts; it does not reproduce every production body, historical job blob, storage condition, parallel plan or complete application workload. It makes no production p95 claim. Production requires the matched-workload follow-up described below.

## Rollout and rollback

This change adds no indexes, triggers, stored facts or migration, so write paths and recovery semantics remain unchanged. Rolling back the application restores the original SQL without a database downgrade. After deployment, compare fresh statement-statistics deltas under matched report periods and ingestion workload; confirm report freshness, review precedence and duration metrics. Check PostgreSQL CPU, buffer work, temporary writes, ingestion latency and queue age independently. Local x86 runs validate the SQL and profiling mechanism; native ARM64 artifacts supply architecture-specific execution evidence. Future production diagnostics must remain bounded and read-only; this benchmark does not execute the changed query against production. Do not run these fixture or `ANALYZE` operations against production.
