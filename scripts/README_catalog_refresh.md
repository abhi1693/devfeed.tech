# Catalog refresh publication benchmark

`profile_catalog_refresh.py` exercises real pipeline SQL, transactional change capture,
report leases, report calculations, and report publication on an explicitly disposable
local PostgreSQL database. It reuses `profile_read_models.py`'s deterministic fixture
and target validation. It refuses remote database hosts, databases without the `_test`
suffix, libpq routing overrides, and Redis databases other than local database 15.

## Dataset and workload

The default fixture contains 150,000 articles, 200,000 analysis jobs, 150,000 enrichment
jobs, 150,000 image jobs, 300,000 reviews, 150,000 ingestion jobs, 100,000 content bodies,
500,000 article-topic links, 1,000,000 article-tag links, 5,000 topics, and 60,000 tags.
Analysis input and catalog history use the default 8 KiB evidence bodies. Ordinary
constraints, indexes, and capture triggers remain enabled. The recorded incident
population was approximately 92,000 articles and 19,679 retained cards; the JSON output
records the actual retained-card count rather than assuming all articles are retained.
Preparation moves future queued enrichment retry timers to one recorded deadline three
hours ahead before bootstrap and cloning. Already-due work stays due. This preserves
retry-wait examples while preventing the original 30-minute fixture timer from firing
on just one side of a slow sequential comparison.

The harness first publishes blockers and the 30-day overview without catalog work.
It then starts a retained-card refresh and explicitly sets its start age to 61 seconds.
This injected timestamp reproduces the incident's freshness condition without requiring
a slow machine; measured SQL, application CPU, and wall durations use the real clock.
The default 20 worker batches examine at most 200 card identities apiece, so the scan
remains active throughout this phase on the full fixture.

A separate database connection commits one deterministic topic revision and one new
article, origin, analysis job, and ingestion-history job concurrently with each batch.
The worker and writer synchronize after each batch, before report publication. This
keeps the input cardinality identical across the comparison despite runtime differences.
Every revision arrives before the retained-card scan finishes. After edits stop, the
ordinary bounded drain must clear all events and finish catalog coverage; sampled
cards are compared with the authoritative lifecycle implementation.

`--expect baseline` requires stable publication followed by the original
`ProjectionNotReady` starvation. `--expect fixed` requires both reports to publish on
every requested batch with explicit `catching_up` metadata, operational lag no greater
than 60 seconds, separate catalog age at least 60 seconds, and nondecreasing article
generations. Both modes require cursor progression, all expected writer commits,
eventual convergence, and lifecycle parity. `complete` records that the workload
finished; `gates.passed` records whether the expected behavior was observed. Population
and native ARM64 PostgreSQL 17 qualification are reported separately.
`--expect either` accepts either complete outcome for a future comparison whose
baseline already contains the fix; partial publication still fails.

## Matched baseline and candidate runs

Use separate checkouts and virtual environments. Copy the candidate harness and
`profile_read_models.py` fixture helper into the baseline checkout, so both sides run
the same workload against their own application
implementation. Start PostgreSQL 17 and Redis locally, with PostgreSQL storage on a
filesystem large enough for the fixture and its clone. Do not reuse application or
integration-test databases. Example environment:

```bash
export DEVFEED_TEST_DATABASE_URL=postgresql+psycopg://postgres:profile@127.0.0.1:55493/catalog_baseline_test
export DEVFEED_TEST_REDIS_URL=redis://127.0.0.1:56493/15
uv run python scripts/profile_catalog_refresh.py --seed --prepare-only \
  --scale 1 --history-bytes 8192 --batch 200 --output /path/to/evidence/prepared.json
```

After preparation exits and its connections close, clone the prepared database with
PostgreSQL's `CREATE DATABASE catalog_candidate_test TEMPLATE catalog_baseline_test
STRATEGY FILE_COPY`. The file-copy strategy avoids writing the full fixture to WAL.
The clone preserves identical articles, histories, capture state, and projection rows.
Run the baseline with the original URL and the candidate with the clone URL:

```bash
uv run python scripts/profile_catalog_refresh.py --expect baseline \
  --batch 200 --batches 20 --output /path/to/evidence/baseline.json

# In the candidate checkout, after changing DEVFEED_TEST_DATABASE_URL to the clone:
uv run python scripts/profile_catalog_refresh.py --expect fixed \
  --batch 200 --batches 20 --output /path/to/evidence/candidate.json
```

For a small functional check, use `--seed --scale .001 --history-bytes 256 --batch 5
--batches 3`. Small results do not qualify as volume evidence. Keep generated JSON and
logs outside tracked source files.

Each JSON artifact includes actual dataset counts, stable report timings, individual
batch timings, application process CPU, report generation and cursor state, concurrent
writer commits, convergence time, lifecycle parity, database size, historical payload
sizes, per-database buffer/temp/tuple counters, and server WAL bytes. Captured SELECTs
receive non-production `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` after the measured
phase. These plans expose exact execution shape, buffers, and temporary blocks without
charging EXPLAIN time to the workload. PostgreSQL cumulative counters can flush with a
delay; server WAL also includes any other database activity on that disposable server.

Use a native ARM64 runner for the issue's platform requirement and record PostgreSQL's
reported build architecture. Emulation and an amd64 developer host do not qualify.
Pass `--require-native-arm64` to reject an incompatible Python or PostgreSQL build
before migration or fixture writes. The focused `Catalog refresh scale` workflow
runs cloned baseline and candidate fixtures on `ubuntu-24.04-arm` and uploads both
JSON results, including the complete query plans.
Use equal container CPU/memory limits and PostgreSQL settings for both sides. Record
other workloads with `--environment-note`; shared-host wall times are diagnostic
samples, not production latency or percentile claims. Application CPU, SQL plans,
publication outcomes, cardinality, and convergence remain separate observations.
