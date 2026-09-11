# Admin overview

The private overview combines windowed activity with clearly labelled current-state
panels. It uses the existing admin API, generated client, cards, charts,
theme, refresh preference, and error notifications.

The four summary metrics are read-only, with date/value tooltips on their daily
sparklines. Publishing and reader trends share one activity surface. All supporting
charts are visible on the page, with no tabs: traffic concentration, interest versus
publication coverage, personalized feed health, recommendation mix, source output,
job reliability, and current workload. Scatter plots expose gaps between interest,
discovery, and publication; they do not claim cohort conversion. Job reliability uses
rates with absolute counts on hover, while current workload remains separate.
Measurement definitions and caveats sit behind accessible info icons. Content types
retain the same shared-theme color across date ranges and modes.

## Measurements

- Summary: first publications, deduplicated article preview opens, new accounts,
  and median discovery-to-first-publication time, with the preceding equal number
  of calendar days for comparison. Today is partial. Publication p90 is calculated
  over individual articles, never by averaging daily percentiles.
- Publishing chart: daily discoveries versus first publications, or first
  publications stacked by content type. Re-publication does not count twice.
- Reader chart: preview opens, not outbound clicks or impressions. Counts are
  deduplicated per viewer, article, and hour. Missing history is null, not zero.
- Popular articles: top ten by retained opens, capped at 30 calendar days; likes
  are current relationship counts, not a historical like-event total.
- Topic coverage: top ten active topics by explicit followers then inferred users.
  Inferred users count only `related_topic` interests. Publication coverage uses
  primary/supporting assignments on currently published articles; it can overlap.
- Personalized feeds: current ready, refreshing, expired, never computed, and
  analysis-not-needed counts. Overdue means the next refresh is at least 15 minutes
  late. Empty ready feeds with inputs and the five highest-priority user issues link
  to user analysis. Stored recommendation reasons include results awaiting refresh.
- Source performance: twelve active approved sources ranked by discoveries, with
  distinct source/article counts, current followers, failed fetch jobs in the
  period, and last successful fetch. Multiple origins never multiply one source's
  article count. Counts across different sources must not be summed as unique
  articles. A separate list includes five sources with the most consecutive failures.
- Processing: current queued/running jobs, selected-period completed/failed jobs,
  oldest queued age, reported AI tokens, average recorded article/topic analysis
  duration, and publications without intervention. Completed notification jobs can
  include skipped recipients; these are not delivery or read receipts.

Last sign-in does not support DAU or retention. Deleted follow/like relationships
do not support complete historical churn charts. No billing or recommendation CTR
is inferred from unrelated counters.

## Storage and query behavior

Apply migration `0028_overview_daily` before starting updated services. It adds the
aggregate `overview_daily` table and timestamp indexes for article publication,
discovery, and account creation. No viewer identities are copied into rollups.

The existing scheduler refreshes daily aggregates at most every five minutes,
before pruning old opens. First startup backfills up to 180 days from retained
records, explicitly marking unavailable open history. Subsequent runs refresh the
current/previous day and any gap since the last run. Previously retained closed-day
history is used by reads even after source open events are removed. If aggregation
fails, that scheduler tick leaves open events intact and continues other work.

The endpoint reads rollups and computes the current day or missing initial history.
It does not write analytics on GET. Current-state counts and publication percentiles
are queried separately. Lists are limited to 5, 10, or 12 rows; no per-row API calls
are made. The cold snapshot has a tested ceiling of 31 SQL statements independent
of the number of returned records. Changes to historical metadata after a day is
finalized do not rewrite its aggregate snapshot.

The existing Redis response-cache helper stores each 1–90-day range for 60 seconds
in a separate admin-overview namespace. Authentication runs before cache lookup,
and HTTP responses remain `Cache-Control: no-store`. A warm hit performs no SQL.
Concurrent cold loaders receive a retryable response rather than duplicating the
aggregate work. Redis failure falls back to database reads. The UI retains its
last successful snapshot on failure and uses the admin's configured polling interval.
