# Analysis catalog preparation

Article analysis prepares the complete approved catalog for retrieval before
inference, then prepares it again for the final revision-checked application.
The opt-in `DEVFEED_ANALYSIS_CATALOG_REUSE_ENABLED=true` retains one catalog and
retrieval generation for that job, so unchanged inputs reuse the first preparation.
The default is `false` because this keeps the generation in memory during inference.
The longer scope also requires `DEVFEED_CACHE_ENABLED=true`. When caching is
disabled, the worker keeps its existing per-stage scopes instead of adding revision
queries to cold, failed, or continuously changing jobs. Cache transport failure
still permits local retention: the enabled-cache path already checks PostgreSQL
revisions and falls back to authoritative catalog reads.
The owner clears both containers on success, dependency failure, lost lease, and
uncaught exceptions; another delivery or durable retry starts with fresh containers.
No session or SQL connection remains open during external inference.

Every catalog load still flushes writes and checks PostgreSQL catalog revisions;
the final apply checks the revision again under the existing publication locks.
Raw SQL writes rotate the transactional revisions. An edit drops the previous
snapshot and prepared index before constructing the replacement. A write between
preparation and the locked check retries preparation; relevant identity changes
still supersede the job. Lease, evidence, source approval, provider admission, and
publication checks are unchanged. Specialist shortlist validation reuses an
already matching identity map or checks IDs directly, so a shortlist cannot evict
the retained complete retrieval index.

A separate construction-only optimization memoizes repeated short ASCII vocabulary.
A bounded sample selects that path; a 512-entry memo only accepts values up to 128
characters. Unicode and longer values use the original normalization path. Mostly
unique vocabularies skip the memo. No normalized vocabulary survives construction.

## Local comparison

Python 3.12, x86-64, 60,000 identities, three cold repetitions; median process CPU.
Shared and unique fixtures have names, aliases, five keywords and short descriptions.
The tag-heavy fixture has 4,450 rich topics and 55,550 simple tags. The single-build
baseline is `origin/master` at `6f12ed1d`. Job preparation compares this implementation
with its flag off/on: two catalog JSON reads, retrieval and nine specialist identity
checks. It excludes SQL, inference, job persistence and publication. These synthetic
measurements establish preparation cost, not whole-worker or production savings.

| Fixture | Single build before | Single build after | Job flag off | Job flag on |
| --- | ---: | ---: | ---: | ---: |
| Shared | 2.456 s | 2.176 s | 5.611 s | 2.789 s |
| Unique | 3.301 s | 3.335 s | 8.569 s | 4.040 s |
| Tag-heavy | 0.729 s | 0.673 s | 2.076 s | 0.991 s |

Job preparation used 50–53% less CPU across these fixtures. The single-build
optimization is modest: 11% for shared vocabulary, 8% for the tag-heavy fixture,
and about 1% slower for the unique control. Timings are observations, not CI
thresholds. The retained job scope provides the main reduction.

A separate one-repetition `tracemalloc` run of the tag-heavy fixture measured
89.55 MiB peak Python allocation with the flag off and 89.34 MiB with it on.
During the simulated inference gap, live traced allocation rose from 0.45 MiB
to **88.40 MiB**. This excludes interpreter/native allocation, the prebuilt fixture,
RPC buffers and other worker state. The important tradeoff is the longer memory
residency, even when the preparation peak is similar. Worker concurrency multiplies
that residency. Tracing adds overhead; traced CPU timings are not in the table.

Reproduce with an explicit local baseline checkout:

```sh
uv run python tests/analysis/catalog_preparation_benchmark.py \
  --baseline-root /path/to/checkout-at-6f12ed1d --rows 60000 --repeats 3
uv run python tests/analysis/catalog_preparation_benchmark.py \
  --baseline-root /path/to/checkout-at-6f12ed1d --rows 60000 --repeats 1 \
  --mode tag-heavy --memory
```

Behavior coverage runs the actual worker through specialist calls and application,
with flag parity, a 60,000-tag database, zero connections during inference, deadlock
and durable retries, lost leases and raw SQL edits during inference or immediately
before locked application. Pure catalog tests compare retained and uncached results
at 60,000 identities and check bounded memo/ranking state and independent scopes.
Integration tests require disposable `_test` PostgreSQL and Redis database 15:

```sh
uv run pytest tests/analysis tests/infrastructure/test_catalog_storage_policy_unit.py \
  -m 'not integration'
uv run pytest tests/infrastructure/test_catalog_snapshots.py \
  tests/workers/test_full_app_automation.py tests/analysis/test_analysis_catalog.py \
  -m integration
```

Before enabling in production, repeat representative profiling on native ARM64 and
compare CPU, peak memory, inference admission and throughput under the same workload.
Start with one worker; disable the flag and restart workers to return to the shorter
preparation lifetime. No migration, provider quota, polling interval or replica
change is included.

Paired native PostgreSQL work across disabled, cold, warm, unavailable and changing
catalogs is documented in the [PostgreSQL work report](analysis-postgres-work.md). The
additional job scope is bypassed with caching disabled to preserve the existing
revision-query budget. Raw buffers/WAL and negative/noisy observations are retained.
