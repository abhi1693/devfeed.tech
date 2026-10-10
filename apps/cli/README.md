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
