# Worker runtime and production audit

This branch builds on the direct ClickHouse work in PR #311. The changes reduce
worker coordination and process overhead while retaining the durable PostgreSQL
job rows, unique Redis delivery identities, fenced claims, isolated RQ workhorses
and bounded recovery. No production workloads were changed during this audit.

## Production evidence

The read-only audit found DevFeed `0.1.4`, PostgreSQL revision `0037`, and the old
pipeline/report projection workers still running. The production observations
therefore describe the deployed release, not the candidate ClickHouse pipeline.

During the 2026-10-27 20:18:02–20:20:35 UTC SQL sample, completed queries consumed
67.38 SQL seconds. A conservative selection of reporting families accounted for
39.27 seconds. Five-minute CPU samples showed approximately 0.91 cores for the
pipeline projector, 0.19 for report projection, and 0.64 for the scheduler. The
reporting work is already removed by PR #311; it is not a new worker improvement
from this branch. Scheduler logs included a 52.4-second cycle despite the nominal
15-second polling interval.

Fifteen RQ worker pods used about 1.34 CPU cores in the sampled five-minute window.
Several AI workers had no job execution in their recent logs. On one idle worker,
container CPU grew by 10.10 seconds during 124 seconds while the parent process
used about 0.56 seconds. A single invocation of its existing Python marker probe
took 1.84 seconds wall time. Kubernetes events independently recorded worker probe
commands exceeding their five-second timeout. This supports interpreter/process
startup as a material idle cost; it does not attribute every container CPU sample
to probes or establish the candidate's production savings. A later read-only
Redis snapshot found 351 deliveries in each article-analysis queue with
`codex_quota_paced` and no provider cooldown. That admission policy intentionally
limits consumption; dispatch improvements do not bypass it or prove faster
provider execution while work is paced.

A previous source-enrichment container exited with Redis `ResponseError: Wrong
number of response items from pipeline execution` during RQ shutdown registration.
An owned Redis fixture reproduced first-signal interruption of `MULTI/EXEC` parsing
20 out of 20 times. Recent worker restarts were principally clean completions;
the inspected terminated-container records did not identify an OOM cause.

OTLP exporter timeouts were also present. Loki returned recent errors; a bounded
Tempo search timed out, so trace-level attribution remains unavailable. A later
idle-in-transaction sample was only 16 milliseconds old, not evidence of a leaked
long-lived transaction. Historical scheduling events reported insufficient CPU,
but current resource requests must be assessed separately from measured usage.
Later, one worker node reported `NodeStatusUnknown` and scheduler exec access
failed with a kubelet proxy 502. That infrastructure failure is separate from the
application changes. A subsequent check found the node Ready again; no node
remediation or cluster mutation was performed.

## Execution design

The worker keeps RQ's process isolation and hard job timeouts. Merely returning a
coroutine from an RQ handler still runs one job to completion; it does not create
cross-job concurrency. Local post-fork import measurements were tens to roughly
one hundred milliseconds, insufficient evidence for replacing the queue framework
or preloading every optional library. RQ documents both its execution lifecycle
and the import/preloading tradeoff in its [worker guide](https://python-rq.org/docs/workers/).

The existing lean `devfeed-worker` entrypoint now accepts the same service queue,
name and job-limit arguments without loading the full operator CLI. Fresh local
interpreter measurements put the broad CLI at about 121 MiB peak RSS and the
worker entrypoint at about 99 MiB. These are import measurements, not guaranteed
steady-state pod reductions.

AI admission reads queue depths, quota, cooldown and suspension in one bounded,
nontransactional Redis pipeline. Empty AI queues make no provider readiness RPC.
Pending AI work still needs fresh admission before dequeue. Heartbeats and probe
writes occur on a bounded cadence; idle polling no longer repeats state writes.
Non-AI consumers retain RQ's blocking transport. Redis documents why batching
commands reduces [round trips](https://redis.io/docs/latest/develop/clients/redis-py/transpipe/).

A clock-accelerated, 100-logical-second Redis fixture with three samples per case
measured the following idle-loop costs. Provider checks were stubbed; their network
latency and CPU are outside these values.

| Assigned queues | Redis commands before → after | Median Python CPU before → after | Idle provider checks |
| --------------- | ----------------------------- | -------------------------------- | -------------------- |
| 2               | 1,111 → 514                   | 97.5 ms → 18.1 ms                | 11 → 0               |
| 6               | 1,515 → 918                   | 133.2 ms → 17.4 ms               | 11 → 0               |

First SIGTERM requests a stop without throwing from the middle of a Redis
exchange. A healthy, blocked consumer observes it within ten seconds. During a
persistent connection outage, stopping waits for the current RQ retry/backoff
(up to sixty seconds) plus the configured network deadline. Busy jobs can drain;
a second signal keeps RQ's force-stop behavior. If transport fails after assignment,
normal intermediate-queue cleanup and durable republication recover the job; this
is not a claim of immediate execution during a Redis outage.

## Dispatch and idle coordination

The shared `JobDispatcher` selects and locks a bounded batch, publishes unique
deliveries before committing their dispatch markers, and batches ORM writes.
Analytics preimages are read in groups of at most 200 exact primary keys. General
SQL filters preserve their original predicates, including aliases and compound
keys. Tests cover attribution moves, partial publication, concurrent claims,
noncanonical UUID input and rollback. See [the dispatch benchmark](worker-dispatch.md)
for measured SQL and timing changes and the portable manual reproduction tool.

Discovery dispatch avoids repeatedly selecting an already-published prefix,
includes interrupted work in bounded recovery, and rechecks the selected stage under lock.
A stage change is admitted through its new queue on a later pass. It never applies
a stale queue route or approves sources automatically.

An empty partner lane uses one existence probe and opens no Redis queue connection
or publication lock. Actual work still acquires the shared queue lock and rechecks
fresh and recovery claims. Recommendation dispatch holds one bounded transaction
for the batch; the independent refill loop owns one reusable Redis client and
closes it at shutdown. Both keep their existing refill cadence. Partial Redis
publication leaves database work eligible for idempotent repair.

Targeted recommendation and partner-evaluation consumers wait for the dispatcher's
row lock, then recheck eligibility. Treating a locked target as completed would
consume a newly published delivery before its dispatch transaction commits.
Native tests exercise that handoff and concurrent recommendation consumers.
Batch scans retain `SKIP LOCKED` for independent work selection, consistent with
PostgreSQL's [locking guidance](https://www.postgresql.org/docs/current/sql-select.htm).

## Health, packaging and rollout

`devfeed-worker-health` is a packaged native shell executable. It reads a strict
three-integer parent heartbeat, compares the suspend-aware boot clock and tests
that the positive PID is alive. It starts neither Python nor a Redis client. Only
acknowledged RQ heartbeats refresh the file; a persistent connection failure lets
it expire. There is no older JSON/Python probe path. The wheel and BusyBox tests
verify the exact executable shipped to the nonroot runtime image.

Kubernetes documents the process overhead of
[exec probes](https://kubernetes.io/docs/concepts/workloads/pods/probes/).
A native shell check retains that mechanism but avoids interpreter and package
startup. The local probe timing and RSS comparison is recorded in the
[benchmark report](worker-dispatch.md); no production reduction is claimed before
the new image and probe commands are deployed together.

Compose and the recorded Fleet contract use the lean worker command and native
probe. The actual owning Fleet manifests must adopt these commands before the new
image is rolled out: the deleted Python module cannot serve an old probe command.
Use a termination grace of at least the longest admitted job plus bounded cleanup;
the supplied worker configuration uses 26 minutes for a 1,500-second analysis job.
The application source and this recorded contract do not constitute a GitOps deploy.

No production CPU or memory limit was lowered. Compose exposes explicit optional
worker limits; measured peak jobs, simultaneous work, database/Redis capacity and
recovery must inform cluster budgets. After rollout, compare completed jobs and
queue age against CPU seconds, working set, probe failures, database round trips
and restarts. Faster dispatch alone does not establish faster provider inference,
source fetches or image processing.
