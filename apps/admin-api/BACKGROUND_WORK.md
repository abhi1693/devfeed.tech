# Background workload capacity

DevFeed protects ingestion and interactive reads by bounding both individual work
and its sustained rate. A finite queue, a statement timeout, or a separate pool
does not by itself bound the work repeated over an hour.

## Report demand and facts

Saved reports remain the serving interface. Reads renew a durable ten-minute demand
lease at most once a minute per normalized group/range. Attention and throughput
are the explicit warm set. Active operational reports refresh after 60 seconds;
historical reports after 300 seconds. A previously viewed 90-day report does not
become permanent maintenance. Expired demand does not cancel an owned calculation;
at most its already queued refresh can finish. Leases, cooldowns and the last good
document survive restart. A cold range still requests work immediately.

Migration 0039 adds compact report facts and UTC daily buckets. Cheap capture
triggers record changed identities; the publisher performs bounded reconciliation.
Each entity's previous contribution is replaced and its old/new delta applied in
one transaction. Replay and late corrections cannot add the same contribution
twice. Acknowledgement is fenced against a concurrent writer's generation. Initial
coverage uses resumable primary-key cursors; no migration scans the population to
build report data. A report requires complete coverage and no change older than
60 seconds waiting to be applied. A continuously nonempty recent queue is allowed.

Token facts retain cumulative article/topic/research job accounting separately
from the existing inference invocation ledger. Publication facts distinguish first
publication activity from current published inventory. Source/topic membership
corrections update the same reusable buckets. Exact publication percentiles read
narrow per-article duration facts; daily medians are never combined. Edge-day facts
preserve inclusive timestamp bounds, UTC rollover and future-date exclusion.

The normal publisher opts into facts; raw calculations remain an independent parity
oracle for offline tests. An incomplete fact projection never causes a serving
calculation to fall back to wide raw histories. Saved documents record the fact
observation used and preserve truthful lag and pipeline generation.

## Automation wakeups

Migration 0040 records semantic article input revisions and coalesced source/global
invalidations. Job completion, changed evidence, editorial changes and source policy
changes wake the existing automation evaluator. Dispatch and lease bookkeeping do
not continually wake unchanged inputs. Stable blocked work sleeps until a dependency
changes, a real retry/deadline arrives, or the bounded daily reconciliation sweep.
Publication still locks and evaluates authoritative records; the demand revision
does not grant publication authority.

Global catalog ranking can change when vocabulary or catalog size changes. Such
changes retain a conservative complete pass over eligible articles, advanced by
bounded keyset pages. Cheap invalidation pages default to 2000 rows, independently
of the 50 expensive article evaluations per cycle. Foreground changes and background
maintenance each reserve half the evaluator batch, with unused capacity available
to the other lane. Existing maintenance cannot prevent a newly completed job from
being evaluated. Successive changes do not rewind an active cursor. A policy
fingerprint also covers deployment/settings changes. This reduces repeated polling;
it does not claim that arbitrary catalog changes have constant cost.

`DEVFEED_AUTOMATION_DEPENDENCY_WAKES_ENABLED=false` restores legacy polling. The
transition itself schedules a bounded wake pass so articles already sleeping are
not forgotten. `DEVFEED_AUTOMATION_RECONCILE_SECONDS` defaults to 86400. Real retry
and worker-recovery deadlines remain independent of that fallback interval.

## Work budgets and attribution

Continuous pipeline/report duty fractions default to 0.5/0.25. Each calculation's
wall time determines an interruptible minimum idle period, including failures.
The allowance includes report fact maintenance. Heartbeats continue during the
wait; backlog metrics, rather than readiness alone, expose insufficient capacity.
The limits apply per worker process. Keep a single publisher per workload under
the reserved database connection budget, and account for rolling surge/failover.

PostgreSQL connections identify the runtime service through `application_name`;
publishers explicitly distinguish pipeline and reports. An explicit
`DEVFEED_DATABASE_APPLICATION_NAME` overrides that default. This helps activity
inspection; `pg_stat_statements` is still grouped by database/user/query identity,
so application names alone cannot partition its historical totals.

Select Kubernetes requests from measured sustained usage, then spread expensive
publishers and preserve database headroom. A CPU limit may slow catch-up while
leaving total query work unchanged. Infrastructure sizing must be measured again
after these changes before editing the owning GitOps deployment.

## Sustained validation

`scripts/profile_background_work.py` runs the actual scheduler and both publisher
loops on explicitly local disposable PostgreSQL/Redis. It includes proactive report
scheduling, all report groups at 7/30/90-day ranges, synthetic committed feed input,
catalog edits, an unattended period and hard worker termination/restart. Inference
providers, source-fetch workers, external identity exchange and browser/proxy
latency are outside this harness; those retain their existing contract suites.

Fixture preparation/backfill is recorded separately from steady-state measurement.
The normal measured phases last 30 minutes active, 21 minutes unattended and three
minutes recovery, followed by a measured drain. Foreground work must converge
within 15 minutes and catalog maintenance within four hours; both debts, their
oldest ages and cursor progress are reported. Future dependency/retry deadlines
are counted separately. These are explicit test budgets to validate, rather than
a claim that a full taxonomy rebuild finishes in seconds. The
unattended phase separately measures ten minutes after the demand lease expires.
Statement costs
are interval deltas, restricted to top-level statements to avoid nested-function
double counting. Buffer reads are not necessarily physical storage reads.

```sh
DEVFEED_TEST_DATABASE_URL=postgresql+psycopg://profile@127.0.0.1:5432/workload_test \
DEVFEED_TEST_REDIS_URL=redis://127.0.0.1:6379/15 \
uv run python scripts/profile_background_work.py --seed --scale 1 \
  --require-native-arm64 --sql-seconds-per-minute 60 \
  --isolated-database-cluster \
  --buffers-per-minute 10000000 --wal-bytes-per-minute 67108864 \
  --output reports/background/candidate.json
```

The workflow repeats this at 150k and 300k articles with proportionate job/payload
histories on native ARM64, comparing base and candidate code on independent runners with
identical resource limits and the same harness. Weekly runs enforce candidate
absolute budgets without fabricating a before/after comparison. It fails on missing
results, incomplete phases, latency/errors/backlog violations, absolute database
work limits, or material SQL/WAL/throughput regressions. All resource changes,
including unfavorable buffer changes, remain in the comparison. The initial
budgets are explicit workload limits to validate, not observed production SLAs.
Short/local runs are labelled smoke and cannot qualify as native-scale evidence.

## Candidate ranking CPU

Candidate ranking reuses a catalog's static zero-score order and sorts only positive
matches. More than 25% positive matches retains the original full sort. Candidate
order, per-field limits, fallback selection and the encoded prompt budget remain
identical, including ties, duplicate IDs, Unicode and oversized entries.

A local x86_64 comparison used 5000 topics, 60000 tags and 16 distinct article
inputs per run. The table shows mean process CPU from two samples per method in
alternating old/new/new/old order, excluding catalog preparation. Every output
payload matched the frozen full-sort oracle. The first lazy order build is included.

| Positive entries per input | Full sort, ms/input | Current, ms/input | CPU change |
| -------------------------- | ------------------: | ----------------: | ---------: |
| 0                          |              101.13 |              9.60 |    -90.50% |
| 105                        |              112.03 |             10.61 |    -90.53% |
| 65000                      |              190.61 |            197.16 |     +3.44% |

Separate `tracemalloc` runs measured peak Python allocations of 135.16→129.09 MiB
for zero matches, 135.11→129.10 MiB for sparse matches and 145.37→145.37 MiB for
dense matches. The lazy cached order adds 2.2–2.4 MiB of retained allocations per
prepared sparse catalog; dense inputs do not create it. These are allocation
measurements, not process RSS. Including cold preparation and all 16 rankings,
CPU fell about 51–53% for zero/sparse inputs and rose about 5% for dense inputs.

This microbenchmark demonstrates a specific CPU reduction. It does not certify
native ARM64 capacity, the evaluator's configured rate, full catalog convergence,
or combined PostgreSQL/worker costs. Those require the sustained comparison above.

Run the parity contracts with
`uv run --no-sync --no-build pytest tests/analysis/test_analysis_catalog.py -m 'not integration'`.
The following recipe reproduces the ranking comparison without database traffic.
Run it again with `CANDIDATE_MEMORY=1` for a separate allocation pass; tracing
changes CPU costs, so do not compare its CPU numbers with the untraced run.

```sh
DEVFEED_DATABASE_URL=postgresql+psycopg://unit@database.invalid/unit_test \
DEVFEED_REDIS_URL=redis://redis.invalid/15 \
uv run --no-sync --no-build python - <<'PY'
import json, os, random, time, tracemalloc, uuid
from devfeed_core import analysis
from devfeed_core.config import Settings

settings = Settings(_env_file=None, analysis_max_candidates=80, analysis_fallback_candidates=8)
analysis.get_settings = lambda: settings
optimized = analysis._candidate_order
tracing = os.environ.get("CANDIDATE_MEMORY") == "1"

def full_sort(prepared, scores):
    return sorted(range(len(scores)), key=lambda i: (-scores[i], *prepared.sort_keys[i]))

for case in ("zero", "sparse", "dense"):
    rng = random.Random(12931)
    taxonomy = {"topics": [], "tags": []}
    for i in range(65000):
        item = {"id": str(uuid.UUID(int=i + 1)), "name": f"Subject {rng.getrandbits(96):024x}",
                "slug": f"subject-{i}", "aliases": [], "keywords": []}
        if case == "dense":
            item["aliases"] = ["routing coroutines"]
        elif case == "sparse":
            if i % 5000 == 0:
                item["aliases"] = ["routing"]
            if i % 701 == 0:
                item["description"] = "Runtime coroutine scheduling evidence"
        taxonomy["topics" if i < 5000 else "tags"].append(item)
    title = "routing coroutines" if case == "dense" else "routing runtime coroutine"
    inputs = [{"title": title + f" article {i}", "text": f"Specific evidence for article {i}"}
              for i in range(16)]
    if case == "zero":
        inputs = [{"title": f"Unrelated evidence {i}"} for i in range(16)]
    reference = None
    methods = (full_sort, optimized) if tracing else (full_sort, optimized, optimized, full_sort)
    for order in methods:
        analysis._candidate_order = order
        analysis.candidate_terms.cache_clear()
        if tracing:
            tracemalloc.start()
        with analysis.reuse_candidates():
            analysis._prepared_catalog(taxonomy)
            if tracing:
                tracemalloc.reset_peak()
            started = time.process_time()
            rows = [analysis.analysis_candidates(taxonomy, snapshot) for snapshot in inputs]
            cpu = time.process_time() - started
            allocations = tracemalloc.get_traced_memory() if tracing else None
        if tracing:
            tracemalloc.stop()
        assert reference is None or rows == reference
        reference = rows
        print(json.dumps({"case": case, "method": order.__name__,
                          "cpu_ms_per_input": cpu * 1000 / len(inputs),
                          "retained_and_peak_bytes": allocations}))
analysis._candidate_order = optimized
PY
```

## Rollout and rollback

1. Stop the old scheduler and publishers before the schema transition. Old release
   readiness checks reject revision 0041; coordinate the immutable application
   rollout with the migration instead of stamping or bypassing readiness.
2. Apply migrations 0038–0041. Capture is installed transactionally; concurrent
   index creation is separate and retry-safe. Capture DDL still needs short table
   locks. New capture adds write/WAL work, so inspect writer latency during staging.
3. Update the owning Fleet backfill command to match the schema-0041 prepared
   `backfill` contract in `scripts/ci/fleet_contracts/commands.json`; the old one-pass
   refresh cannot initialize the new facts. Start the new bounded publishers and
   complete fact/pipeline backfills. Explicitly
   request required reports and drain the report queue before promoting dependent
   readers. Initial backfill can take substantially longer under the duty budget;
   inspect oldest pending work and progress, not just pod health.
4. Compare exact report/lifecycle results and interval SQL, buffers, temp spill,
   WAL, ingestion latency, reader latency and convergence. Promote one workload
   family at a time through Fleet and observe a comparable 24-hour production window.
5. To restore polling, turn off dependency wakes and allow its wake pass to finish.
   Keep additive facts and capture while diagnosing reporting. Full binary rollback
   requires stopping new writers and a coordinated schema downgrade because old
   binaries do not accept the new schema. Saved reports and source records remain
   authoritative inputs for rebuilding derived facts.

No readiness result or queued job submission proves backfill, report completion,
or production capacity. Native comparison and the final production observation are
separate gates.
