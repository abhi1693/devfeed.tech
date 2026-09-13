# Worker monitoring

The admin sidebar has **Workers** (`/workers`) and **Queues** (`/queues`). Both use
existing admin authentication and the saved refresh interval in Settings > Defaults.

See [dedicated worker queues](worker-queues.md) for routing, independent scaling and upgrade steps.

Workers shows background and AI workers, their state, current subject, and execution
counters. Long registration names are shortened in the list; full names, assigned
queues, heartbeat, and process information are in Technical details. Search by worker, host, or subject,
or filter by queue and state. Open a worker to see its current run, saved results,
subject, and expandable live job logs. Verification tasks link to their original topic
research run, where the evidence and approval decisions are recorded.

Queues combines PostgreSQL job records with Redis transport state:

- **Waiting** includes durable queued work and retries scheduled for later.
- **Dispatched** counts Redis queue entries. Redispatches can create duplicate
  attempts, so this is not a count of unique pending database jobs.
- **Running**, **Succeeded**, and **Failed** count retained database runs. Research
  and its separate verification task each count as a run.
- **Review required** counts completed verification runs requiring review. It does
  not mean that analysis failed, and historical runs can outlive a manual decision.
- Workers shared across queues appear in each assigned queue's capacity figures;
  a busy worker may currently be serving another assigned queue.

RQ execution counters cover the current worker registration, include skipped
executions, and do not measure approval or correctness. Registrations disappear
when their Redis keys expire; this page does not provide persistent worker history,
CPU/memory metrics, or an exact per-worker suspension reason. Provider cooldowns are
shown separately; the existing AI connection control provides account/server status.

An RQ failed-delivery registry can retain entries after a durable database task has
successfully retried. Compare the task's current status and lease with delivery
history before treating a retained Redis failure as unfinished work. Invalid article
inference includes safe validation codes and field/error identifiers in structured
logs; rejected model responses are not retained. See [automation](automation.md).

Telemetry reads never dequeue jobs, retry work, or clean RQ registries. If an update
fails, the page retains its previous snapshot and marks it stale. Unknown or expired
workers return 404. Redis outages return 503 rather than empty healthy results.
The user only decodes bounded, allowlisted JSON job references; it never loads
pickle or returns job payloads, credentials, or exception internals.

The overview keeps content activity and daily AI completion charts visible alongside
inventory and review totals. AI activity groups article and topic research by their
UTC completion date; queued work is shown separately. The 7/30-day control updates
both charts. Topic coverage, automation metrics, and individual blocker targets can
be expanded without crowding the initial view.
