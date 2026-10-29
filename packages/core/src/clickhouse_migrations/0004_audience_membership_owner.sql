-- Keep the membership's real account identity for current-generation cohorts.
-- Older rows remain unknown until the finite, acknowledged metadata refresh.
ALTER TABLE analytics_user_events ADD COLUMN IF NOT EXISTS user_id Nullable(UUID);
