# Admin Overview loading

The Overview shell renders after authentication/settings. Each of its 33 charts
or panels requests `GET /v1/admin/overview/panels/{panel}?days=7|30|90` independently,
with its own shimmer, retry, cancellation, and last-successful response. The browser
limits active Overview requests to four. A cold panel's 503 retries automatically;
an exhausted panel offers its own retry without replacing the page.

The server authenticates before returning cached data. Small shared datasets such
as daily counts coalesce requests across charts, but no panel loads the complete
legacy Overview report. Redis caches each dataset/range for 60 seconds. Local
snapshots can serve stale data for up to five minutes while refreshing. Each API
process limits concurrent reporting queries to two, with read-only sessions and
10-second statement limits. Unrelated API routes retain normal serving capacity.
The legacy aggregate endpoint remains available for compatibility.

Current workload shows all eight logical job types, with queued/running counts
for each; empty types remain in the legend. Physical worker lanes are detailed
on Workers. Publication blockers distinguish queued extraction from finished
extraction that could not obtain sufficient text.

Regression tests cover independent slow-panel recovery, authenticated cache reads,
dataset coalescing, range changes, cancellation, and bounded browser concurrency.

## Measurements

- Summary: first publications, deduplicated original-article clicks, new accounts,
  and median discovery-to-first-publication time, with the preceding equal number
  of calendar days for comparison. Today is partial. Publication p90 is calculated
  over individual articles, never by averaging daily percentiles.
- Publishing chart: daily discoveries versus first publications, or first
  publications stacked by content type. Re-publication does not count twice.
- Reader chart: clicks through to the original article. Preview opens and impressions
  are not counted. Counts are
  deduplicated per viewer, article, and hour. Missing history is null, not zero.
- Reader engagement: daily distinct viewers opening original articles, with a second
  series for viewers opening at least two distinct articles that day. Multiple hours
  of clicks on one article do not qualify as multiple articles. Signed-in accounts
  are deduplicated across devices; anonymous viewers use a browser cookie. Clearing
  cookies or signing in can count a person again. These are estimates, not page DAU,
  retention, completed reads, or satisfaction scores.
- Reading depth: daily clicks divided by daily readers. The period average divides
  total clicks by total reader-days, not an unweighted mean of daily averages. Days
  without readers or with missing history have no ratio; missing history is excluded.
- New accounts: daily sign-ups. Closed-day snapshots can include since-deleted
  accounts. No visitor conversion rate is inferred.
- Likes and follows: current percentage of existing accounts with at least one like,
  topic follow, or source follow. Accounts count once per feature, groups overlap,
  and removing relationships changes the snapshot. This chart is independent of the
  date range and is not a historical activity funnel.
- Popular articles: top ten by retained opens, capped at 30 calendar days; likes
  are current relationship counts, not a historical like-event total.
- Topic coverage: top ten active topics by explicit followers then inferred users.
  Inferred users count only `related_topic` interests. Publication coverage uses
  primary/supporting assignments on currently published articles; it can overlap.
- Personalized feeds: current ready, refreshing, expired, never computed, and
  analysis-not-needed counts. Overdue means the next refresh is at least 15 minutes
  late. Empty ready feeds with inputs and the five highest-priority user issues link
  to user analysis. Stored recommendation reasons include results awaiting refresh.
- Source output: compact donut with the five largest active, approved sources plus
  Other. Percentages use all eligible sources, including sources beyond the twelve
  returned detail rows. Multiple origins never multiply one source's article count;
  shared articles count once per source. The center is source publications, not a
  unique-article total. Names, compact counts and shares remain visible in a six-row
  legend. Fetch failures remain separate.
- Processing: current queued/running jobs, selected-period completed/failed jobs,
  oldest queued age, reported AI tokens, average recorded article/topic analysis
  duration, and publications without intervention. Completed notification jobs can
  include skipped recipients; these are not delivery or read receipts.

Last sign-in does not support DAU or retention. Deleted follow/like relationships
do not support complete historical churn charts. No billing or recommendation CTR
is inferred from unrelated counters.

## Storage and query behavior

Apply migration `0001` before starting updated services. It adds the
aggregate `overview_daily` table and timestamp indexes for article publication,
discovery, and account creation. No viewer identities are copied into rollups.

The existing scheduler refreshes daily aggregates at most every five minutes,
before pruning old opens. First startup backfills up to 180 days from retained
records, explicitly marking unavailable open history. Subsequent runs refresh the
current/previous day and any gap since the last run. When upgrading old rollups,
the retained 30-day window is backfilled with daily reader and multi-article reader
counts; older missing reader counts remain unknown. Existing open totals survive
that upgrade. The JSON rollup fields require no new migration. Previously retained closed-day
history is used by reads even after source open events are removed. If aggregation
fails, that scheduler tick leaves open events intact and continues other work.
