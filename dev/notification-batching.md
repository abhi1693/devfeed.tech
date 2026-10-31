# Bounded notification transport batches

Notification delivery can group up to ten ready database rows into one RQ transport
job. Each row keeps its own delivery ID, idempotency key, audience credential,
subscriber routing, claim transaction, attempt count, lease, retry and completion
transaction. Only the work-horse/import/telemetry lifecycle and HTTP client are
shared. Cookies returned by one delivery are cleared before another request, so
admin/user audiences cannot inherit ambient transport credentials.

`DEVFEED_NOTIFICATION_DELIVERY_BATCH_SIZE` defaults to **1**. This emits exactly the
legacy handler, argument and stable per-attempt identity. Upgrade every low/common
worker and the dispatcher before increasing it to 2–10; older workers do not
understand the batch envelope. No migration is needed. Existing singleton jobs
remain executable after the upgrade. Do not enable the gate during a mixed-version
worker rollout.

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

Keep this PR in draft for native ARM64 CPU/latency profiling with real tracing and
profiling enabled. Compare total worker CPU, notification age, throughput, low-queue
fairness and partial-failure recovery. Parent-worker telemetry remains unchanged;
no profiler/exporter threads are started before fork. Returning the batch size to
1 stops creating new batches; keep compatible workers until existing batches drain
before rolling back their image. No production configuration is changed by this PR.
