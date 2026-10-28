-- Initial direct analytics schema. This migration is immutable once shipped.
-- Apply centrally before application writers start; stores do not create schema.
-- TODO markers describe future object retirement through a new numbered migration.
-- Preserve this historical file and the retained analytical data when removing importer code.

-- Raw hourly reader events expire after 30 days; aggregate targets below retain totals.
CREATE TABLE IF NOT EXISTS analytics_article_opens (
    opened_hour DateTime64(6, 'UTC'), article_id UUID, viewer_key FixedString(64),
    event_id UUID, historical UInt8, article_key String MATERIALIZED toString(article_id)
) ENGINE = ReplacingMergeTree(historical) PARTITION BY toYYYYMM(opened_hour)
ORDER BY (article_key, opened_hour, event_id)
TTL opened_hour + INTERVAL 30 DAY DELETE SETTINGS merge_with_ttl_timeout=3600;

-- statement

-- Lifetime open identities, including the historical overlap subtraction state.
CREATE TABLE IF NOT EXISTS analytics_open_lifetime (
    article_id UUID, open_state AggregateFunction(uniqExact, UUID),
    historical_state AggregateFunction(uniqExact, UUID)
) ENGINE=AggregatingMergeTree ORDER BY article_id;

-- statement

-- Hourly audience sketches retain no raw viewer keys.
CREATE TABLE IF NOT EXISTS analytics_open_audience_hourly (
    bucket_hour DateTime('UTC'), open_state AggregateFunction(uniqExact, UUID),
    reader_state AggregateFunction(uniqCombined64, String)
) ENGINE=AggregatingMergeTree ORDER BY bucket_hour;

-- statement

-- Ingest states must exist before application producers accept raw inserts.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics_open_lifetime_ingest
TO analytics_open_lifetime AS SELECT article_id,uniqExactState(event_id) AS open_state,
uniqExactStateIf(event_id,historical=1) AS historical_state
FROM analytics_article_opens GROUP BY article_id;

-- statement

-- Ingest audience states before raw-event TTL removes old rows.
CREATE MATERIALIZED VIEW IF NOT EXISTS analytics_open_audience_ingest
TO analytics_open_audience_hourly AS SELECT toDateTime(opened_hour,'UTC') AS bucket_hour,
uniqExactState(event_id) AS open_state,uniqCombined64State(toString(viewer_key)) AS reader_state
FROM analytics_article_opens GROUP BY bucket_hour;

-- statement

-- Imported lifetime totals are historical data; retain after importer retirement.
CREATE TABLE IF NOT EXISTS analytics_article_open_baseline (
    article_id UUID, opens UInt64
) ENGINE = ReplacingMergeTree ORDER BY article_id;

-- statement

-- Direct observations with stable replay identities.
CREATE TABLE IF NOT EXISTS analytics_partner_events (
    kind LowCardinality(String), owner String, identity String, day Int32,
    revision UInt64, impressions UInt64, clicks UInt64, searches UInt64,
    first_seen DateTime64(6, 'UTC'), last_seen DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(revision) ORDER BY (kind,owner,identity);

-- statement

-- Direct observations with stable replay identities.
CREATE TABLE IF NOT EXISTS analytics_search_events (
    kind LowCardinality(String), owner String, identity String, day Int32,
    revision UInt64, impressions UInt64, clicks UInt64, searches UInt64,
    first_seen DateTime64(6, 'UTC'), last_seen DateTime64(6, 'UTC')
) ENGINE=ReplacingMergeTree(revision) ORDER BY (kind,owner,identity);

-- statement

-- Content-free inference invocation observations.
CREATE TABLE IF NOT EXISTS analytics_inference_events (
    id UUID, started_at DateTime64(6, 'UTC'), revision UInt8,
    task LowCardinality(String), model LowCardinality(String), effort LowCardinality(String),
    model_is_null UInt8, model_is_empty UInt8, effort_is_null UInt8, effort_is_empty UInt8,
    reason Nullable(String),
    status LowCardinality(String), input_tokens UInt64, cached_input_tokens UInt64,
    reported_cached_input_tokens UInt64, cache_write_input_tokens UInt64, has_input_tokens UInt8,
    output_tokens UInt64, reasoning_tokens UInt64, web_searches UInt64,
    duration_ms UInt64, has_duration UInt8
) ENGINE = ReplacingMergeTree(revision)
PARTITION BY toYYYYMM(started_at) ORDER BY (started_at, id);

-- statement

-- Current typed overview observations; latest revision wins.
CREATE TABLE IF NOT EXISTS analytics_pipeline_events (
    domain LowCardinality(String), id UUID, revision UInt64, deleted UInt8,
    occurred_at Nullable(DateTime64(6, 'UTC')), created_at Nullable(DateTime64(6, 'UTC')),
    task LowCardinality(String), status LowCardinality(String), outcome LowCardinality(String),
    attempt UInt32, duration_ms Nullable(UInt64), automated UInt8,
    source_ids Array(UUID), topic_ids Array(UUID), interest_count UInt32,
    has_generation UInt8, invalidated UInt8, has_recommendations UInt8,
    expires_at Nullable(DateTime64(6, 'UTC')), next_refresh_at Nullable(DateTime64(6, 'UTC')),
    name String, target_id UUID, consecutive_failures UInt32
) ENGINE = ReplacingMergeTree(revision) ORDER BY (domain, id);

-- statement

-- Current typed overview observations; latest revision wins.
CREATE TABLE IF NOT EXISTS analytics_user_events (
    domain LowCardinality(String), id UUID, revision UInt64, deleted UInt8,
    occurred_at Nullable(DateTime64(6, 'UTC')), created_at Nullable(DateTime64(6, 'UTC')),
    task LowCardinality(String), status LowCardinality(String), outcome LowCardinality(String),
    attempt UInt32, duration_ms Nullable(UInt64), automated UInt8,
    source_ids Array(UUID), topic_ids Array(UUID), interest_count UInt32,
    has_generation UInt8, invalidated UInt8, has_recommendations UInt8,
    expires_at Nullable(DateTime64(6, 'UTC')), next_refresh_at Nullable(DateTime64(6, 'UTC')),
    name String, target_id UUID, consecutive_failures UInt32
) ENGINE = ReplacingMergeTree(revision) ORDER BY (domain, id);

-- statement

-- Current operator cards and tombstones are isolated by application policy.
CREATE TABLE IF NOT EXISTS analytics_card_canonical (
    policy String,
    kind LowCardinality(String),
    shard UInt16,
    entity_id UUID,
    version UInt128,
    deleted UInt8,
    stage LowCardinality(String),
    state LowCardinality(String),
    source_id Nullable(UUID),
    source_name Nullable(String),
    source_sort String,
    source_ids Array(UUID),
    search_text String,
    literal_search_text String,
    source_type LowCardinality(String),
    created_at DateTime64(6,'UTC'),
    activity_at DateTime64(6,'UTC'),
    terminal_at Nullable(DateTime64(6,'UTC')),
    has_active_work UInt8,
    next_transition_at Nullable(DateTime64(6,'UTC')),
    evaluated_at DateTime64(6,'UTC'),
    catalog_sensitive UInt8,
    review_pending UInt8,
    enabled UInt8,
    approval_status LowCardinality(String),
    consecutive_failures UInt32,
    blocker_codes Array(String),
    title String,
    editorial_revision UInt64,
    semantic_hash String,
    payload String
) ENGINE = ReplacingMergeTree(version) ORDER BY (policy, kind, shard, entity_id);

-- statement

-- Readiness and current-card policy for the direct analytical stores.
CREATE TABLE IF NOT EXISTS analytics_direct_status (
    import_id String,version UInt64,ready UInt8,updated_at DateTime64(6,'UTC'),
    capture_started_at DateTime64(6,'UTC'),reader_coverage_start Date,lag_seconds Float64,
    policy String
) ENGINE=ReplacingMergeTree(version) ORDER BY import_id;

-- statement

-- Retained PostgreSQL historical rows contain actual data; keep after importer retirement.
CREATE TABLE IF NOT EXISTS analytics_history_archive (
    source_table LowCardinality(String),identity String,record String
) ENGINE=ReplacingMergeTree ORDER BY (source_table,identity);

-- statement

-- Closed audience history preserves aggregate-only data from the deployed PostgreSQL schema.
CREATE TABLE IF NOT EXISTS analytics_audience_archive_daily (
    day Date,
    opens Nullable(UInt64),
    readers Nullable(Float64),
    accounts_created Nullable(UInt64),
    published Nullable(UInt64),
    version UInt64
) ENGINE = ReplacingMergeTree(version) ORDER BY day;

-- statement

-- TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Drop this historical import gate in a later numbered
-- migration after all supported deployments complete import.
CREATE TABLE IF NOT EXISTS analytics_article_open_cutover (
    cutover_hour DateTime64(6, 'UTC'), ready UInt8
) ENGINE = MergeTree ORDER BY (cutover_hour, ready);

-- statement

-- TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Retire source-import checkpoints through a later numbered
-- migration after the upgrade floor advances; preserve required current-card policy coverage.
CREATE TABLE IF NOT EXISTS analytics_backfill_progress (
    import_id String, source LowCardinality(String), page UInt64,
    cursor String, complete UInt8, boundary String
) ENGINE=ReplacingMergeTree(page) ORDER BY (import_id,source);
