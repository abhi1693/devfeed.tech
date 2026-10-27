-- The migration journal is created before checking applied schema versions.
CREATE TABLE IF NOT EXISTS analytics_schema_migrations (
    version UInt32,
    name String,
    checksum FixedString(64),
    applied_at DateTime64(6, 'UTC') DEFAULT now64(6)
) ENGINE = MergeTree ORDER BY version;
