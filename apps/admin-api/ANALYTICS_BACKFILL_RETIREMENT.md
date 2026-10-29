# ClickHouse historical import retirement

`REMOVE_AFTER_CLICKHOUSE_BACKFILL` marks the finite import from the last
PostgreSQL analytics schema. Application requests read the direct ClickHouse
stores throughout this release; the importer provides no PostgreSQL fallback.

Remove the following together in a later release after every supported deployment
has completed its import and the supported upgrade floor has advanced:

- `analytics_backfill.py`, `analytics_import.py`, `analytics_import_runner.py` and
  `analytics_cutover.py`: bounded scanners, frozen historical row copying,
  temporary write guards and legacy PostgreSQL cleanup.
- The historical stage of `AnalyticsImportWorker` and its import batch/poll
  configuration. Keep the analytics process for bounded current-state
  reconciliation, along with its independent coverage and fingerprint tables.
  Retain a bounded mechanism to seed current cards whenever their policy changes.
- The historical PostgreSQL field/key allowlist in `analytics_schema.py`. Keep
  the live producer registry.
- `partner_claim_backfill.py` and old article-open baseline import entry points.
  Preserve the imported receipt timestamps and fixed open cutoff.

Before removal, verify on every supported deployment:

1. `analytics_direct_status FINAL` has `ready=1` for the current application card
   policy. All `direct-v1` and policy-specific `cards-*` progress rows are complete.
2. PostgreSQL has no legacy analytics tables, calculation/capture triggers, guard
   functions, analytics sequences or search count columns. Business tables remain
   present and writable.
3. Historical inference calls, completed attempts, reader totals, search totals
   and partner totals match retained pre-import evidence. Test replay after a
   failed insert/checkpoint, restart, busy DDL locks and a changed card policy.
4. Old application processes have drained. Advancing the supported upgrade floor
   must reject installations that skipped the required import release.
5. Backups include ClickHouse data, its import checkpoints and readiness record.
   Starting an empty ClickHouse instance after source cleanup cannot reconstruct
   deleted history. Direct delivery has no durable replay queue.

Read-only ClickHouse progress checks:

```sql
SELECT ready, policy, capture_started_at
FROM analytics_direct_status FINAL;

SELECT import_id, source, page, complete
FROM analytics_backfill_progress FINAL
ORDER BY import_id, source;
```

An absent checkpoint is incomplete. Retiring import code does not authorize
removing historical data from ClickHouse. Keep `analytics_audience_archive_daily`,
article-open baselines, inference events, partner/search baselines and retained
migration evidence until a separate retention decision authorizes their removal.
