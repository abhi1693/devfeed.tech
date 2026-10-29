-- Permanent current-view coverage. Finite import checkpoints/history stay intact.
CREATE TABLE IF NOT EXISTS analytics_reconcile_progress (
    policy String, scope LowCardinality(String), version UInt64,
    source_at Nullable(DateTime64(6,'UTC')),
    completed_at Nullable(DateTime64(6,'UTC')), pending UInt8, cursor String
) ENGINE=ReplacingMergeTree(version) ORDER BY (policy,scope)
SETTINGS fsync_after_insert=1,fsync_part_directory=1,min_rows_to_fsync_after_merge=1;

-- statement

-- Compact source versions and owner identities; never article bodies or prompts.
CREATE TABLE IF NOT EXISTS analytics_reconcile_fingerprints (
    policy String, kind LowCardinality(String), entity_id UUID, version UInt64,
    fingerprint String, target_hash String, owners Array(String), deleted UInt8
) ENGINE=ReplacingMergeTree(version) ORDER BY (policy,kind,entity_id)
SETTINGS fsync_after_insert=1,fsync_part_directory=1,min_rows_to_fsync_after_merge=1;
