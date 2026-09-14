# Admin overview

The overview is ordered by the questions an administrator needs to answer:

1. **Needs attention:** review queues, active workload, and publishing blockers.
2. **Publishing:** publication totals, time to publication, automatic publication, and sources.
3. **Audience:** opens, accounts, and readership, and audience details.
4. **Personalization:** feed readiness and recommendation reasons, and coverage.
5. **Processing:** current capacity beside 24-hour output, topic backlog beside decision efficiency,
   then finished-job counts beside topic review outcomes.
6. **AI usage:** completed-job and recorded-call tokens and inference diagnostics.

The section links provide direct navigation. All 33 panels are visible immediately under their
section headings, with no expandable groups. All panels participate in refreshes, with a maximum of four concurrent overview requests.

## Freshness and chart periods

One overview status reports loading, failures, or the oldest successful panel generation time.
Results older than five minutes are marked stale. Failed refreshes retain the previous result;
only failed or stale panels show their own timestamp. Status links jump directly to the affected panels. “Refresh all” and the date selector apply to every panel.

Sparse daily charts initially focus on recent activity with at least seven available buckets.
The calendar button in each chart header (“Show full period”) restores the entire selected period.
The control stays inside the header so it does not shift adjacent cards. This only changes the displayed window:
totals remain based on the selected period, and missing telemetry remains distinct from zero.

## Metric definitions

- **Published automatically** is the percentage of published articles that required no manual
  intervention. It does not measure job success or queue health.
- **Completed-job tokens** sum the cumulative usage recorded on completed article analysis,
  topic analysis, and research verification jobs, including retries, by completion date in UTC.
- **Recorded call tokens** aggregate individual inference calls by their start date in UTC.
  This telemetry began later than job accounting and includes other operations. The UI shows
  its recording start and missing-usage count. Cached input and reasoning are subsets of input
  and output, not additional tokens. These two accounting views are not directly comparable;
  neither is a bill or a provider quota estimate.
- **Eligible workers** are live workers subscribed to a pool, including busy shared workers.
  Busy and idle describe their observed state; shared workers can appear in both pools.
  Unavailable observations show a dash. Scheduler admission retains its existing reservable
  capacity calculation, which excludes busy shared workers.
- **Estimated actionable topic drain** covers only actionable topics at the observed decision
  rate. Deferred topics, manual review, and article analysis queues are explicitly excluded.

## Browser verification

Run `npm run admin:build`, then `node apps/admin/tests/browser/overview.mjs`.
The Playwright harness starts the production Next.js build and a disposable localhost API fixture.
It checks desktop and mobile layouts, all panels visible without expansion, sparse/full history, refresh loading,
partial failures with retained data, stale results, empty data, date ranges, and the four-request
concurrency limit. It also checks the sticky sidebar fills the viewport after scrolling and
keeps its final link reachable at 1080px and 600px viewport heights. It writes screenshots and results to ignored `reports/admin-overview/`.
The fixture does not verify production database totals or authenticate against production.

## Loading and query isolation

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
in separate summary tiles and a compact running-job donut with a count table for all eight job types, including idle types. Physical worker lanes are detailed
on Workers. Publication blockers distinguish queued extraction from finished
extraction that could not obtain sufficient text.

Regression tests cover independent slow-panel recovery, authenticated cache reads,
dataset coalescing, range changes, cancellation, and bounded browser concurrency.

## Additional measurement definitions

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

Processing separates current worker and backlog snapshots from the selected-period job outcomes.
Finished-job rows show absolute completed and failed counts, with failures first; queued and
running jobs are excluded. Backlog states explain the next step rather than implying progress.

## Live workload display

Workload follows the configured refresh interval and existing page-visibility polling rules.
The status distinguishes Live, Reconnecting, Stale data, and Manual refresh. “Checked”
measures time since the last successful response, not the age of the underlying cached dataset.
Data older than five minutes cannot show a healthy live indicator. Automatic polling follows the configured interval; Refresh all remains available. A zero refresh interval stays manual.

Counter deltas compare consecutive successful results in the same date range. The first response
is an initial observation. Queued and running deltas remain beside the totals. Queue changes
are not reported as completion events, and no activity or throughput is simulated.

The workload donut shows running jobs by type; the centre is the running total. Queued jobs
have a separate total and table column so large backlogs do not obscure running work. Empty
running data shows a neutral ring. The chart updates on successful polls with no decorative
flow animation. All eight types remain visible in a compact count table.
