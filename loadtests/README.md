# Locust load tests

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
search service. The local runner deliberately does not provision Typesense.

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
Locust. The PR's `CI required` gate waits for this comparison.

CI checks out the event's exact base and head commits into separate directories
and installs each revision's own locked application dependencies. The candidate's
Locust harness drives both versions, including bases that predate this harness.
Each gets a new database, seed dataset, Redis and PgBouncer. Six matrix jobs run in parallel, three samples per revision. **Each sample gets
a fresh GitHub-hosted ARM64 runner**, so leaked resources cannot carry over
between samples. All samples use the same profile:
16 users, 60 seconds per run, 1,000 articles, cache disabled, five server slots.

A bot updates one PR comment with commit SHAs, per-endpoint median p50/p95/p99,
request rates and the verdict. The same report is in the check's job summary;
HTML, CSV, final JSON, metadata and logs for all six runs are downloadable from
its artifact link. Fork and Dependabot PRs receive check summaries and artifacts
without a write-token comment. Benchmark execution has read-only permissions;
the separate comment job only reads artifacts and never executes candidate code.

- **REGRESSED:** a route's median p95 rises by more than 20% **and** 20 ms,
  with that increase present in at least two head samples versus the base median; new errors, missing
  route coverage or failed connection-release checks also fail the candidate.
- **IMPROVED:** the inverse p95 threshold is met, with no regressed or noisy route.
- **NO MATERIAL CHANGE:** complete, successful traffic without a material change.
- **INCONCLUSIVE:** the baseline cannot run successfully, reports are incomplete,
  or a revision's p95 range exceeds both 35% of its median and 20 ms. Rerun after checking the
  runner and artifacts; do not interpret this as an improvement.

Regressed **and inconclusive** comparisons fail the PR gate. Every route needs
at least 20 samples in each run; normal absolute smoke gates still apply. These
are initial regression thresholds, not production SLOs or a statistical claim
of significance. Review workload/threshold changes as carefully as application
changes, since the harness comes from the candidate. An incompatible baseline
schema or older connection behavior is reported as inconclusive, not skipped.

A separate read-only comparison job collects all six artifacts and verifies their
commit SHAs and workload metadata before comparing. Missing, malformed or mismatched
artifacts are inconclusive, never silently omitted. Matrix fail-fast is disabled
so one failed sample does not discard the other evidence. Runner identity,
architecture and logical CPU count are recorded in each sample. Separate VMs can
still differ in hardware or host load; repeated samples and the noise gate reduce,
but cannot eliminate, that uncertainty.

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
