# ClickHouse backfill retirement

`REMOVE_AFTER_CLICKHOUSE_BACKFILL` marks temporary import code to remove in a
later release after the ClickHouse cutover has completed everywhere that release
supports. This is a removal plan, not a second analytics serving implementation.
Current requests use the native ClickHouse layout throughout the transition.

## Temporary code

| Location                                                                                                  | Purpose                                                                                                                                               | Remove together                                                                                                                   |
| --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------- |
| `packages/core/src/analytics_upgrade.py`                                                                  | Preserve the first ClickHouse layout, import retained facts and daily aggregates, and reconcile queued format-1 observations that lack before-images. | `NativeUpgrade`, `V1_KEYS`, old-format normalization, worker imports/calls, and the `analytics_upgrade_checkpoint` control table. |
| `AnalyticsExporter._backfill_one` in `analytics_worker.py` and `BACKFILL_TABLES` in `analytics_schema.py` | Import the existing PostgreSQL population and closed `overview_daily` history into native ClickHouse once.                                            | Historical bootstrap cursors, one-time readiness barriers, and obsolete bootstrap-only configuration.                             |
| `AnalyticsExporter._export_events`                                                                        | Drain format-1 and format-2 records retained by the queue rename in migration `0042`. Current capture emits format 3.                                 | Pre-V3 format acceptance and `V1_KEYS` source validation; a new migration must then constrain the queue to format 3.              |
| Seed branch of `CardPublisher._timer_bootstrap_one` and `_timer_bootstrap_due`                            | Seed the indexed timer queue for existing `0042` card populations while covering unseeded due cards.                                                  | Seed cursor and transitional ClickHouse due scan after every supported installation has completed timer adoption.                 |
| `RollupPublisher._cleanup_one` in `analytics_rollups.py`                                                  | Remove orphaned `analytics_compact_*` staging tables created by the former partition-replacement compactor.                                           | Staging-table scan, cleanup constants, and associated tests after the staging retirement gate below. Keep generation-bounded pruning. |
| Historical cursor restoration in `scripts/job_retention_benchmark.py`                                     | Recreate `BACKFILL_TABLES` cursor rows after the disposable benchmark resets its schema.                                                              | Remove this restoration when the supported upgrade floor and historical importer no longer require those cursors.                 |

Historical import and timer seed pages are bounded, restartable, and lease-fenced.
Their checkpoints advance only after durable writes. The temporary timer due scan
can read the remaining unseeded card population until adoption completes. Retain
the import paths during this release so an upgrade cannot silently discard
historical facts, queued changes, or due timers. Missing or unknown format
versions fail before writes or acknowledgement; they do not default to an older
capture format.

Remove the importer's complexity-check exclusion from
`.github/scripts/python-checks.sh` when deleting `analytics_upgrade.py`. Permanent
analytics code remains subject to the normal complexity limit.

## Release gates

Remove these paths in the first later release that satisfies all of the following:

1. Every supported upgraded deployment has completed the `v1` checkpoint,
   `native_import_complete`, and `native_bootstrap_complete`. All historical source
   cursors are complete, and initial rollup/gauge publication barriers have drained.
2. No format-1 or format-2 records remain in `analytics_ingest_events`. No older
   binary capable of producing those formats or writing the retired ClickHouse
   layout can run. Advance the supported upgrade floor so skipped cutover releases
   are rejected explicitly rather than treated as fresh installations.
3. Validate native historical totals, dates, distinct-reader coverage, and attempt
   semantics against the retained migration evidence. Keep the original capture
   boundary and imported history; completion alone is not data-parity proof.
4. Fresh installations enable capture before accepting business writes, or retain
   an explicitly bounded native initialization mechanism. Removing the initial
   population scan must not create a gap between the first source write and
   capture activation. Test this ordering with concurrent writers.
5. Validate restart, lease handoff, queued changes, and ClickHouse restore without
   calling the old importer. Restoring an existing installation must preserve its
   ClickHouse data or follow a separately documented rebuild procedure.
6. Every upgraded `0042` card population has `complete=true` in
   `analytics_card_timer_bootstrap`, its queued deadlines have been reconciled,
   and reset/restart testing proves the indexed queue covers all current cards.
   Reject upgrades skipping this adoption once its temporary bridge is removed.
7. Every supported native installation has stopped old exporters that can create
   `analytics_compact_*` tables, and the transitional cleanup has removed all
   matching staging tables. Verify the inventory against ClickHouse `system.tables`
   on each installation. Do not remove generation-bounded rollup pruning, which
   is the permanent cleanup mechanism.

Read-only progress checks against PostgreSQL:

```sql
SELECT complete
FROM analytics_upgrade_checkpoint
WHERE name = 'v1';

SELECT native_import_complete, native_bootstrap_complete,
       card_bootstrap_complete
FROM analytics_pipeline_state
WHERE singleton;

SELECT source_table
FROM analytics_bootstrap_cursors
WHERE NOT complete;

SELECT format_version, count(*)
FROM analytics_ingest_events
WHERE format_version < 3
GROUP BY format_version;

SELECT domain, bucket
FROM analytics_fact_refresh
WHERE initial_pending;

SELECT domain, shard
FROM analytics_gauge_refreshes
WHERE bootstrap_required;

SELECT policy, kind_index, shard, cursor, complete, cleanup_pending
FROM analytics_card_timer_bootstrap
WHERE singleton;
```

An absent completion row is incomplete. Native format-3 changes can continue to
arrive while the old-format query remains empty; an entirely idle source database
is not required for removal.

## Native behavior and historical data to keep

- Exact-ID captured-event delivery, native fact/rollup/gauge publication, and
  generation fencing remain the steady-state implementation.
- Indexed card timers and their lease-fenced publication, due dequeue, and reset
  epochs remain permanent native behavior. Preserve the timer-control state and
  bounded cleanup; only the adoption cursor and due-scan bridge are temporary.
- `CARD_BACKFILL_TABLES`, `CardPublisher._backfill_one`, current-card coverage
  cursors, policy rebuilds, and reset recovery initialize the current native card
  layout. They are still needed when a derivation policy changes or current-card
  coverage is reset; they are not old analytical serving code.
- `analytics_audience_archive_daily` and other native archives contain real
  historical data queried by native reports. Retiring the importer must not drop
  them or truncate history.
- `analytics_v1_archive_*`, `analytics_v1_pending_archive`, and
  `analytics_v1_attempt_evidence` retain migration evidence. Deleting the importer
  does not authorize deleting these tables; apply a separate verified retention
  decision after parity and recovery requirements are met.
- Existing Alembic migrations and migration regression fixtures describe supported
  database upgrades. Keep migration `0042` immutable; constrain or remove obsolete
  control state in a subsequent migration rather than rewriting upgrade history.
