# Source polling schedules

Admin source PATCH and CLI `devfeed sources update SOURCE_UUID --poll-interval SECONDS`
share the same locked scheduling service. A changed interval on a healthy, enabled,
approved source recalculates `next_fetch_at` from `last_success_at + new interval`;
overdue sources become due now for the next scheduler cycle. Unchanged intervals
and unrelated edits leave the pending schedule intact.

If no fetch has succeeded yet, an interval edit retains the existing initial due
time. Disabled/unapproved sources, queued or running ingestion jobs, and source
failure cooldowns retain their due times as well. Active retries keep their job
availability and upstream `Retry-After` delay. A successful worker completion uses
the latest interval for the next fetch. Re-enabling a healthy approved source
without changing its interval requests an immediate fetch; it does not bypass an
active job or failure cooldown. A combined interval/enable edit follows the cadence
rules above. Merely sending `enabled=true` for an already-enabled source does not
reschedule it. Adaptive cadence should reuse `reconcile_source_schedule`.

## Adaptive source polling

Fixed polling remains the default. Use `devfeed sources add URL --type publisher
--polling-mode adaptive` or `devfeed sources update ID --polling-mode adaptive` to
opt in a source. The global `DEVFEED_ADAPTIVE_SOURCE_POLLING_ENABLED` gate must also
be true. `--poll-interval` always sets the configured fixed/fallback interval;
learning never overwrites it. `sources show` exposes the gate, effective mode and
interval, bounded activity state, decision reason and next fetch time.

When changing the gate or bounds, restart **all ingestion workers, the scheduler,
and the admin API** with the same environment. Fresh CLI invocations read the new
configuration. Mixed configurations are unsupported. Defaults are 300–86,400
seconds; configure `DEVFEED_ADAPTIVE_SOURCE_POLLING_MIN_SECONDS` and
`DEVFEED_ADAPTIVE_SOURCE_POLLING_MAX_SECONDS` within 300–604,800, minimum ≤ maximum.
Apply migration 0023 before starting the new services.

The policy targets one newly observed source entry per fetch using an arrival-rate
EWMA with a six-hour half-life. First fetches and history older than seven days
establish an hourly probe baseline, ignoring the import burst. Each window uses
at most eight arrivals; acceleration is at most 2× with a 15% dead band. Successful
quiet windows grow intervals by 2× per elapsed day, up to the maximum. A stable
source-specific ±5% jitter is clipped to bounds. Tuning values are starting points,
not production measurements. HTTP 304, duplicates and empty feeds are quiet;
failed requests never train the policy. New origins count independently of article
creation, editorial approval and enrichment. Publication dates older than the last
observation are conservatively excluded from velocity; missing/future dates use
arrival evidence. The retained state is constant-size, with no scheduler article
scans or added per-entry history.

Manual fetches coalesce with active jobs. They accumulate capped pending arrival
evidence for the next automatic window without advancing its clock. The first
successful manual fetch can establish a baseline. Retries and upstream cooldowns
win over scheduling, including subsequent manual requests. Queuing alone does not
train the policy; elapsed observation windows include operational queue delays.

Interval/mode edits and re-enabling use the source lock and preserve active jobs,
disabled/unapproved sources and failure cooldowns. Adaptive sources retain learned
state when their fallback interval changes. Switching to fixed restores cadence
from the last success. Without a success, an interval edit retains the initial
pending time; bounded mode/gate reconciliation uses now as its safe fallback. Feed URLs are
immutable in the source edit API/CLI. Discovery replacing a feed clears learning
and HTTP validators; an in-flight result for the old URL is discarded.

After turning the gate off, the scheduler reconciles at most
`DEVFEED_SCHEDULER_BATCH_SIZE` eligible schedules per tick, independently of its
normal due-source batch. It restores fixed cadence from the last success (or now
without one), spreading overdue catch-up over 0–59 seconds. With N eligible sources
and batch B, reconciliation takes at most ceil(N/B) successful scheduler ticks plus
59 seconds. Active jobs finish using the current gate; cooldown sources wait for
their existing protection. Returning a source to fixed mode reconciles immediately
under the same protections. A slow adaptive schedule cannot remain indefinitely
when the gate is off.

Interval decisions emit structured logs. Aggregate telemetry reports effective
cadence, fetches with new origins, unchanged responses, queue delay and discovery
latency for plausible dates within seven days; metric labels contain only mode,
never source IDs. Reproduce the deterministic 90-day latency/request/queue/error
comparison with `uv run python scripts/testing/adaptive_polling.py`. It models 120
sources sharing four workers, minute-long jobs and ticks, batches of 20, and a
one-hour rate-limit cooldown every 97th request. This is a synthetic capacity
comparison, not a production capacity estimate.
