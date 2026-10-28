# Locust load tests

For administration boards, Overview, and partner analytics at production scale,
see [native analytics profiling](../apps/admin-api/ANALYTICS.md#disposable-profiling).

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
not a sandboxed service, and accepts absolute paths. If `GITHUB_STEP_SUMMARY` is
set, it also appends the report to that caller-supplied path. Do not pass model-generated or
untrusted remote-request paths to either interface. This differs from the disposable
runner's resource-creation and `reports/` boundary described below.

## Disposable local run

Requires Linux, Docker and uv. The runner creates its own PostgreSQL database,
Redis database 15, session-mode PgBouncer (five server slots), seeded API process
and isolated environment. It never truncates an existing database, reads the
checkout's `.env`, starts AI providers, or contacts production.

Every run generates a disposable database password. PgBouncer authenticates with
SCRAM using the database's exact stored verifier; its mounted configuration files
contain no plaintext password. The PostgreSQL readiness check uses TCP so it cannot
accept the temporary initialization server before the final server starts.

```sh
uv run --locked --group loadtest python scripts/load-test.py
```

Default: eight users, two users spawned per second, a 30-second measurement, 1,000 published
articles, 20 sources/topics, cache disabled and API admission 16. The dataset
is synthetic and the topology is one API and one pooler. It is not a production
capacity model.

Reports stay within the invocation directory's `reports/` folder. `--output` can
select a subdirectory there, using a relative or absolute path; paths or symlinks
that escape that folder are rejected before the runner creates resources. Run
from the repository root for the examples below. Repeating a run replaces its
reports, but an existing `database-statistics.json` symlink is rejected.
Known report and log filenames must not be symlinks. The runner's JSON reports
are written through file descriptors that reject symlinks and enforce owner-only
read/write permissions, including when replacing an existing report.

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

Each disposable run waits for every user to finish bootstrap, warms the workload
for 15 seconds, then parks all users between journeys. At that barrier it resets
statistics, each user's random streams and bootstrap cursor/article state before
releasing the users for the full requested `--seconds` measurement. A fixed
`--journey-seed` (default 294, an integer from 0 through 4,294,967,295) controls
separate per-user journey, resource and think-time streams. Different response
times can change how many journeys finish,
but cannot change the next journey chosen by another user. `--warmup-seconds`
changes the warmup duration. Bootstrap, warmup and request-draining failures still
fail the run; a watchdog cannot produce a successful shortened measurement.

`measurement.json` records completed phases and timestamps.
`runtime-diagnostics.json` retains API and load-generator CPU/RSS, PostgreSQL CPU
and throttling counters, database activity/waits and pooler snapshots at phase
changes and every five seconds. It also records measured-window PostgreSQL
statement deltas, including execution time, calls and buffer/temp activity. A
separate direct database connection keeps diagnostics outside the five pooler
slots. Phase boundaries are observed by polling; recorded observation lag makes
the CPU/SQL window's limits explicit. Partial diagnostics survive a failed run.
Image IDs and the PostgreSQL version are retained in the metadata. These counters
help investigate a failed latency gate; they do not establish causation alone.

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
For ordinary standalone Locust runs, `--reset-stats` excludes ramp-up samples;
the runtime still includes ramp-up time. The disposable runner uses the controlled
warmup and full measurement window above instead. Standalone Locust opts into that
protocol only with `--measurement-seconds`; its `--run-time` then acts as a watchdog
and must allow bootstrap, warmup, measurement and request draining to finish. The
controlled protocol requires a local load generator. An optional `--phase-file`
must name a different, regular file beside the explicit `--final-json` report;
phase writes use secure temporary siblings and atomic replacement.

## Optional revision comparisons

Performance comparisons are optional local tools. Automated performance jobs,
browser-budget reports and their PR comments have been retired; they are not
part of `CI required`. CI retains correctness tests for the harness and reports,
including the Locust histogram contracts.

The comparison report leads with the verdict and a compact p95 table, with both absolute
and percentage changes. Expand the secondary metrics for separate p50/p99 columns
and throughput, individual-run health, and interpretation details.

Prepare separate checkouts at the exact base and candidate commits, and install
each revision's own locked application dependencies. The candidate also needs the
optional `loadtest` dependency group. The candidate's
Locust harness drives both versions, including bases that predate this harness.
Each gets a new database, seed dataset, Redis, PgBouncer and API process.
The runner explicitly enables `TCP_NODELAY` on the loopback listener inherited by
Uvicorn for both revisions. This prevents event-loop differences in inherited-socket
defaults from adding Nagle/delayed-ACK latency to reused HTTP connections.

Run one pair from the candidate checkout, supplying both checkout paths:

```sh
uv run --locked --group loadtest python scripts/ci/api-performance-pair.py \
  --candidate-root /path/to/candidate --base-root /path/to/baseline \
  --repeat 0 --output reports/performance
```

The pair measures base and candidate sequentially and preserves both outcomes.
The harness cleans up each sample's resources before the next sample starts.
All samples use the same profile:
16 users, completed bootstrap, 15 seconds of warmup, a full 60-second measurement,
journey seed 294, 1,000 articles, cache disabled, five server slots. The measurement
barrier excludes warmup requests already in flight and resets per-user journey
state so unequal warmup throughput cannot change the measured starting point.

The strict comparator needs three independent native ARM64 hosts. Run one pair
per host with `--repeat 0`, `1` and `2`, using a distinct, truthful `RUNNER_NAME`
for each host. The repeat index alternates execution order. Collect the resulting
six `base-0` through `head-2` directories together under `reports/performance`,
then generate the local comparison:

```sh
uv run --locked --group loadtest python loadtests/compare.py \
  --base-sha <base-sha> --head-sha <candidate-sha> --output reports/performance
```

Each sample retains HTML, CSV, final JSON, phase records, runtime diagnostics,
metadata and logs. The comparator writes `comparison.json` and `summary.md`;
publishing those reports for review is a manual step. A single pair remains useful
for diagnostics but cannot satisfy the three-host comparison protocol.

Latency histograms use fixed one-millisecond bins for both revisions. Locust's
default ten-millisecond bins above 100 ms can turn an actual 89 → 106 ms change
into 89 → 110 ms, incorrectly crossing the 20 ms regression limit. Its default
100 ms bins above one second can also hide a 2,010 ms smoke-budget violation as
2,000 ms. The retained histogram contract tests check both cases against real Locust
statistics. Each final JSON records `response_time_bucket_ms: 1`; the
comparison rejects missing or incompatible precision instead of mixing reports.

- **REGRESSED:** a route's median p95 rises by more than 20% **and** 20 ms,
  with that increase present in at least two same-runner base/head pairs; new errors, missing
  route coverage or failed connection-release checks also fail the candidate.
- **IMPROVED:** the inverse p95 threshold is met, with no regressed or noisy route.
- **NO MATERIAL CHANGE:** complete, successful traffic without a material change.
- **INCONCLUSIVE:** successful runs have a p95 range exceeding both 35% of the
  median and 20 ms, or a material median change lacks two confirming pairs.
  The comparator returns success, with the uncertainty retained in the
  report; it does not claim an improvement.
- **ERROR:** baseline execution failed or reports are missing, invalid or
  mismatched. This is a failed test/report pipeline, not uncertain latency.

Only **REGRESSED** and **ERROR** comparisons return a nonzero exit status.
**INCONCLUSIVE**, **IMPROVED** and **NO MATERIAL CHANGE** return success. Every route needs
at least 20 samples in each run; normal absolute smoke gates still apply. These
are initial regression thresholds, not production SLOs or a statistical claim
of significance. Review workload/threshold changes as carefully as application
changes, since the harness comes from the candidate. An incompatible baseline
schema or failed baseline connection checks are reported as errors, not skipped.

The comparator reads all six samples and verifies their commit SHAs and workload
metadata before comparing. Missing, malformed or mismatched samples are errors,
never silently omitted. Completed warmup and measurement
windows, protocol version, seed and readiness counts must also match; older or
partially completed measurement protocols are rejected. The pair runner attempts
both revisions even when one fails, preserving the evidence. Host identity,
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
longer soaks separately before drawing broader conclusions. Shared-host benchmark
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
