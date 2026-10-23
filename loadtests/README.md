# Locust load tests

For administration boards, Overview, and partner analytics at production scale,
see the [durable read-model profiler](../apps/admin-api/READ_MODELS.md#reproducible-scale-checks).

These tests exercise real public API reader journeys over HTTP: Latest feed,
cursor pagination, article detail, feed options, and topic/source-filtered feeds.
They use published article IDs and cursors returned by the application, bounded
metric names, weighted tasks and 0.2–1 second think time. No external publisher
URLs or article images are fetched. Search is an explicit option for targets with
a populated Typesense index.

Locust runs in its own process so gevent cannot monkey-patch the application or
pytest. It is pinned in the optional `loadtest` dependency group, outside runtime
images. This suite complements the SQL budgets and destructive disposable pooler
fault tests; it does not replace them.

Random choices select public article, topic, source and search journeys only.
They do not generate credentials, session identifiers, or authorization values;
cryptographic randomness would add no protection to this workload simulation.

`loadtests/compare.py --output` intentionally selects a local artifact directory
for reading samples and writing comparison reports. It is an operator-only CLI,
not a sandboxed service, and accepts absolute paths. Its `GITHUB_STEP_SUMMARY`
destination is supplied by the invoking CI job. Do not pass model-generated or
untrusted remote-request paths to either interface. This differs from the disposable
runner's resource-creation and `reports/` boundary described below.

## Disposable local run

Requires Linux, Docker and uv. The runner creates its own PostgreSQL database,
Redis database 15, session-mode PgBouncer (five server slots), seeded API process
and isolated environment. It never truncates an existing database, reads the
checkout's `.env`, starts AI providers, or contacts production.

```sh
uv run --locked --group loadtest python scripts/load-test.py
```

Default: eight users, two users spawned per second, 30 seconds, 1,000 published
articles, 20 sources/topics, cache disabled and API admission 16. The dataset
is synthetic and the topology is one API and one pooler. It is not a production
capacity model.

Reports stay within the invocation directory's `reports/` folder. `--output` can
select a subdirectory there, using a relative or absolute path; paths or symlinks
that escape that folder are rejected before the runner creates resources. Run
from the repository root for the examples below. Repeating a run replaces its
reports, but an existing `database-statistics.json` symlink is rejected.

Compare uncached database pressure with a normal cache-enabled run **sequentially**
on the same otherwise idle machine, with separate report directories:

```sh
uv run --locked --group loadtest python scripts/load-test.py \
  --users 16 --spawn-rate 4 --seconds 60 --rows 1000 \
  --output reports/loadtest/uncached
uv run --locked --group loadtest python scripts/load-test.py \
  --users 16 --spawn-rate 4 --seconds 60 --rows 1000 --cache on \
  --output reports/loadtest/cached
```

The runner always removes only its own processes/containers/network, including
on test failure. It writes CSV endpoint statistics and history, an HTML report,
API/setup logs, run metadata and a post-run `SHOW POOLS` snapshot. The API remains
alive during the snapshot; retained application clients or waiting work fail the
run. PgBouncer's idle **server** connections are expected and reusable.

After committing the seed data, the runner explicitly analyzes its disposable
database so query plans do not depend on automatic analyze starting during the
measurement. `database-statistics.json` records each table's estimated row count,
page count, and analyze timestamps before the API starts. This preparation applies
equally to the base and candidate applications.
The seed subprocess emits statistics as JSON on stdout and its progress message
on stderr; the runner saves those streams to the statistics artifact and setup
log respectively. The seed command accepts only the row count, with no output path.

## Existing staging API / interactive UI

Supply the public API origin, not a website HTML route. The workload requires
published articles, approved sources and active topics with visible articles.
The existing-target workload makes GET requests only and never seeds data.
Non-loopback origins require explicit `--allow-remote-target`.

```sh
uv run --locked --group loadtest locust -f loadtests/locustfile.py \
  --host https://api.staging.example.com --allow-remote-target \
  --web-host 127.0.0.1
```

The UI is available at localhost:8089. For a reproducible headless baseline:

```sh
uv run --locked --group loadtest locust -f loadtests/locustfile.py \
  --host https://api.staging.example.com --allow-remote-target \
  --headless --users 50 --spawn-rate 2 --run-time 10m --stop-timeout 10 \
  --reset-stats --csv reports/staging-reader --csv-full-history \
  --html reports/staging-reader.html
```

Create `reports/` first. Add `--include-search` only with a configured, populated
search service. The search profile exercises exact, typo-tolerant, natural-language,
and zero-result queries. It requires every variant to produce successful samples
and applies a separate default search p95 limit of 800 ms; override it deliberately
with `--search-max-p95-ms`. The local runner deliberately does not provision Typesense.

Increase user counts in measured steps before a long soak (`--run-time 30m`).
Users are not requests/second: each journey makes multiple requests and includes
think time. Run distributed generation only after checking generator CPU and
network headroom, using Locust's normal master/worker options and the same pinned
version/workload on every node. Production runs need an explicitly agreed target,
load ceiling, duration and abort criteria; no production run accompanies this change.

## Quality gates and interpretation

The default gate requires at least 50 requests, zero failures, successful samples
for each core route, and p95 <= 2,000 ms globally and for each endpoint with at
least five samples. Override with `--minimum-requests`, `--max-failure-ratio` and
`--max-p95-ms` for a deliberate test profile; these are initial smoke limits, not
an established production SLO. Unhandled user exceptions still fail the run.
Empty datasets, HTML fallbacks, malformed JSON and unexpected schemas fail.
Redirects, 401/403/429/503 and admission rejection are failures, not silently
retried or excluded. Bootstrap failures are also reported in distributed statistics.
`--reset-stats` excludes ramp-up samples; the runtime still includes ramp-up time.

## Pull request regression reports

The `PR performance` check runs **only on pull requests**. Pushes to master,
release tags, scheduled builds, manual builds and merge-group builds do not run
Locust. Successful application tests unlock performance jobs and image builds in
parallel. Failed or cancelled application tests skip both. Security checks run
independently; `CI required` still requires tests, security, images and the PR
performance comparison to pass.

The PR report leads with the verdict and a compact p95 table, with both absolute
and percentage changes. Expand the secondary metrics for separate p50/p99 columns
and throughput, individual-run health, and interpretation details.

CI checks out the event's exact base and head commits into separate directories
and installs each revision's own locked application dependencies. The candidate's
Locust harness drives both versions, including bases that predate this harness.
Each gets a new database, seed dataset, Redis, PgBouncer and API process.
The runner explicitly enables `TCP_NODELAY` on the loopback listener inherited by
Uvicorn for both revisions. This prevents event-loop differences in inherited-socket
defaults from adding Nagle/delayed-ACK latency to reused HTTP connections.

Three matrix jobs run in parallel, **each on a fresh GitHub-hosted ARM64 runner**. Each job measures
base and head sequentially, alternating which revision runs first. This produces six
isolated samples, three per revision, while keeping each base/head pair on the same
host. The harness cleans up each sample's resources before the next sample starts.
All samples use the same profile:
16 users, 60 seconds per run, 1,000 articles, cache disabled, five server slots.

A bot updates one PR comment with commit SHAs, per-endpoint median p50/p95/p99,
request rates and the verdict. The same report is in the check's job summary;
HTML, CSV, final JSON, metadata and logs for all six runs are downloadable from
its artifact link. Fork and Dependabot PRs receive check summaries and artifacts
without a write-token comment. Benchmark execution has read-only permissions;
the separate comment job only reads artifacts and never executes candidate code.

Latency histograms use fixed one-millisecond bins for both revisions. Locust's
default ten-millisecond bins above 100 ms can turn an actual 89 → 106 ms change
into 89 → 110 ms, incorrectly crossing the 20 ms regression limit. Its default
100 ms bins above one second can also hide a 2,010 ms smoke-budget violation as
2,000 ms. The performance job checks both cases against real Locust statistics
before measuring. Each final JSON records `response_time_bucket_ms: 1`; the
comparison rejects missing or incompatible precision instead of mixing reports.

- **REGRESSED:** a route's median p95 rises by more than 20% **and** 20 ms,
  with that increase present in at least two same-runner base/head pairs; new errors, missing
  route coverage or failed connection-release checks also fail the candidate.
- **IMPROVED:** the inverse p95 threshold is met, with no regressed or noisy route.
- **NO MATERIAL CHANGE:** complete, successful traffic without a material change.
- **INCONCLUSIVE:** successful runs have a p95 range exceeding both 35% of the
  median and 20 ms, or a material median change lacks two confirming pairs.
  The check is green, with the uncertainty retained in the
  report; it does not claim an improvement.
- **ERROR:** baseline execution failed or reports are missing, invalid or
  mismatched. This is a failed test/report pipeline, not uncertain latency.

Only **REGRESSED** and **ERROR** comparisons fail the PR gate.
**INCONCLUSIVE**, **IMPROVED** and **NO MATERIAL CHANGE** pass. Every route needs
at least 20 samples in each run; normal absolute smoke gates still apply. These
are initial regression thresholds, not production SLOs or a statistical claim
of significance. Review workload/threshold changes as carefully as application
changes, since the harness comes from the candidate. An incompatible baseline
schema or failed baseline connection checks are reported as errors, not skipped.

A separate read-only comparison job collects all six artifacts and verifies their
commit SHAs and workload metadata before comparing. Missing, malformed or mismatched
artifacts are errors, never silently omitted. Matrix fail-fast is disabled
so one failed sample does not discard the other evidence. Runner identity,
architecture, logical CPU count, and timezone-aware start time must validate each
pair and its alternating execution order; the three pairs must use distinct runners.
Missing or mismatched provenance fails the comparison. The report retains all
paired p95 values and their absolute/relative deltas, with counts in both directions.
Pairing revisions
on the same host reduces differences in hardware and host load between base and
head. Alternating order, repeated pairs and the noise gate reduce, but cannot
eliminate, changes in host load over time or order effects.

Throughput is descriptive because users include think time. This profile does
not test cache behavior or maximum capacity. Compare cached workloads and run
longer soaks separately before drawing broader conclusions. Shared CI benchmark
numbers are not hardware-independent performance claims.

Correlate the test interval with HTTP admission rejections, connection acquisition
and hold time, PgBouncer waiting/server use, PostgreSQL lock waits/slow queries,
CPU/memory, and durable worker outcomes. Check that idle application clients go
away while PgBouncer server connections remain reusable. Authenticated user/admin
flows, real RQ background traffic, Next.js/browser rendering, extension parity and
Kubernetes pooler endpoint failover require separate tests on a representative
staging deployment. Locust's HTTP client does not execute JavaScript or render UI.

References: [Locust user scenarios](https://docs.locust.io/en/stable/writing-a-locustfile.html)
and [headless runs and exit gates](https://docs.locust.io/en/stable/running-without-web-ui.html).
