# Notification transport experiment: disabled

Only `DEVFEED_NOTIFICATION_DELIVERY_BATCH_SIZE=1` is accepted. Environment or CLI
startup with another value fails validation with an explicit PostgreSQL-budget
message. The grouped-carrier prototype remains available to controlled tests and
to drain any previously queued experimental envelope; it cannot be enabled through
supported configuration. This draft is **not ready for activation**.

At fixed throughput of 100 HTTP attempts, repeated 20-second soft stops increase
database writes because untouched rows are cleared and dispatched again. Keeping
the default at one would not be a sufficient safeguard: the configuration now
enforces one. The PostgreSQL gate fails for the experimental ten-item path, even
though it reduces process/client lifecycles in a ready backlog. See
[`notification-postgres-work.md`](../benchmarks/notification-postgres-work.md)
for the paired server measurements, including negative results.

The next design must preserve individual durable RQ identities and avoid grouped
dispatch/release writes. A bounded notification-only child/runtime that consumes
several existing individual RQ jobs is a possible direction; it needs separate
deadline, shutdown, fairness, telemetry, idempotency, and PostgreSQL work proof.
No persistent-child implementation or production configuration change is included.

## Prototype behavior retained for review

Notification delivery can group up to ten ready database rows into one RQ transport
job. Each row keeps its own delivery ID, idempotency key, audience credential,
subscriber routing, claim transaction, attempt count, lease, retry and completion
transaction. Only the work-horse/import/telemetry lifecycle and HTTP client are
shared. Cookies returned by one delivery are cleared before another request, so
admin/user audiences cannot inherit ambient transport credentials.

Size one emits exactly the legacy handler, argument and stable per-attempt identity.
It does not create grouped carriers or execute batch release SQL. Existing singleton
jobs remain compatible, and no schema migration is introduced.

Batches use a stable ID derived from the selected rows' existing attempt identities.
Dispatch still publishes to Redis before stamping PostgreSQL rows; a rollback or
missing Redis queue membership remains safe to redispatch. Per-row locking and
Chimely's unchanged idempotency keys prevent duplicate delivery during replay.
On redispatch, an atomic Redis operation refreshes the envelope and restores missing
queue membership only while the carrier is queued and absent from RQ's intermediate
list. Existing waiters keep their position. If a worker already claimed the carrier,
its original envelope remains unchanged. PostgreSQL always stamps the publication
time from the actual returned carrier, so both restored and claimed batches keep
their release fence. A failed database commit can replay the same stable carrier ID.

A batch stops admitting rows after 20 seconds and has a 30-second RQ timeout.
Untouched queued rows are made immediately dispatchable only when their publication
timestamp still matches that batch. A newer dispatcher, a running claim or a
finished delivery cannot be reset by an old batch. Release uses a one-second SQL
statement budget; if PostgreSQL is unavailable, the ordinary durable redispatch
and expired-lease recovery paths remain the fallback. RQ deadlines are propagated,
not swallowed by per-item error handling or best-effort database cleanup. A slow
destination cannot monopolize the shared low queue indefinitely. Singletons retain their legacy timeout.

Partial HTTP failures update only their own delivery row. An unexpected per-item
runtime failure does not stop independent rows; the carrier reports failure after
processing them and releases still-unclaimed rows. Success and delayed retries are
not sent again by an immediate replay. Feed-topic eligibility is still rechecked
immediately before each row is claimed, and internal article/user routing IDs are
still removed from recipient payloads.

## Validation and rollout

The disposable-service suite compares 100 ready deliveries with batch size 1 and
10: 100 versus 10 carrier jobs and 100 versus 10 HTTP-client lifecycles, with 100
individual HTTP attempts, unchanged per-row idempotency, independent audience keys,
and per-row retry state. This demonstrates 90% fewer work-horse startups for a full
backlog, not 90% lower total CPU. Tests also cover deadline release fencing and
repairing a lost Redis list entry using the same grouped delivery identity.
Native regressions interleave LMOVE before the refresh action, change running/terminal
status or routing during refresh, and replay a publication whose database commit
failed. A restored batch's soft stop releases untouched work immediately while a
genuinely newer carrier keeps its own freshness. Unit cases inject RQ hard deadlines
at session-factory, transaction-entry, timeout-setting and release-update boundaries.

Keep this PR in draft until a replacement design passes the PostgreSQL gate before
considering native ARM64 CPU/latency rollout profiling. Parent-worker telemetry
remains unchanged; no profiler/exporter threads are started before fork. Supported
configuration stays at one. Compatible workers are still needed to drain any
previously queued experimental envelopes before an image rollback.
