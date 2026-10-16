# Source polling schedules

Admin source PATCH and CLI `devfeed sources update SOURCE_UUID --poll-interval SECONDS`
share the same locked scheduling service. A changed interval on a healthy, enabled,
approved source recalculates `next_fetch_at` from `last_success_at + new interval`;
overdue sources become due now for the next scheduler cycle. Unchanged intervals
and unrelated edits leave the pending schedule intact.

If no fetch has succeeded yet, an interval edit retains the existing initial due
time. Disabled/unapproved sources, queued or running ingestion jobs, and source
failure cooldowns retain their due times as well. Active retries keep their job
availability and upstream `Retry-After` delay. A successful worker completion uses
the latest interval for the next fetch. Re-enabling a healthy approved source
without changing its interval requests an immediate fetch; it does not bypass an
active job or failure cooldown. A combined interval/enable edit follows the cadence
rules above. Merely sending `enabled=true` for an already-enabled source does not
reschedule it. Adaptive cadence should reuse `reconcile_source_schedule`.

## YouTube resources

Submit a channel URL/handle, playlist URL, or video URL through the reader's source
suggestion, the admin source form/import, or the CLI. Resource identities are separate
from publisher/aggregator semantics; YouTube sources have no invented RSS URL.

```sh
devfeed sources add 'https://www.youtube.com/@GoogleDevelopers' --type publisher
devfeed sources add 'https://www.youtube.com/playlist?list=PLAYLIST_ID' --type aggregator
devfeed sources add 'https://youtu.be/VIDEO_ID' --type publisher
devfeed sources add 'https://www.youtube.com/watch?v=VIDEO_ID&list=PLAYLIST_ID' --type aggregator --youtube-target playlist
devfeed sources import youtube-urls.txt --type publisher
devfeed sources approve SOURCE_UUID --by operator --note 'Reviewed developer-focused metadata'
devfeed sources fetch SOURCE_UUID
```

Replace placeholder IDs with real public resources. A watch URL containing both IDs
previews both targets; the default is only that video. Select playlist explicitly to
submit the collection. Handles resolve to stable channel IDs and their uploads playlist.
Direct video submissions create one nonrecurring review origin, retaining the actual
channel attribution without approving or following that channel. Admin article creation
also recognizes video URLs and retrieves authoritative metadata for that same identity.
Its manually entered title/summary do not replace retrieved YouTube evidence.

All entry points enter normal pending source review. Approval enables indexing; each
video independently needs developer relevance, grounded evidence, known language,
editorial approval and a supported active primary topic before publication. Empty or
sparse descriptions remain pending for further evidence or explicit review. Indexing
does not require transcripts or claim that a video was watched. Reader watch actions
count as original-link opens in existing activity metrics, not playback or completion.

Enable **YouTube Data API v3** in your Google Cloud project and configure
`DEVFEED_YOUTUBE_API_KEY` only in the provider-facing backend services. Restrict the key
to that API and your server environment. Compose routes it to submission APIs and
provider-facing workers; it is absent from web/admin/partner frontend containers and
public API responses. Missing configuration and quota exhaustion remain explicit errors
in source/job state; quota and rate failures retain checkpoints and use normal backoff.
The key is sent in an HTTP header, never a URL.

| Setting | Default | Purpose |
| --- | --- | --- |
| `DEVFEED_YOUTUBE_INITIAL_IMPORT_LIMIT` | `100` | Maximum items visited in the first collection pass |
| `DEVFEED_YOUTUBE_REFRESH_ITEM_LIMIT` | `100` | Maximum items visited per later pass (minimum 2 so history can progress) |
| `DEVFEED_YOUTUBE_REQUEST_LIMIT` | `20` | Maximum provider calls per adapter invocation |
| `DEVFEED_YOUTUBE_REVALIDATE_SECONDS` | `86400` | Stale metadata/retry cooldown for global availability revalidation |
| `DEVFEED_YOUTUBE_REVALIDATION_BATCH_SIZE` | `5` | Maximum stale videos queued per scheduler tick |

Collection refreshes revisit the head and advance durable history checkpoints within
these bounds. Video metadata reads are batched. Known videos also rotate through
availability checks; stale videos from RSS-only or inactive collections use the existing
enrichment outbox. Source approval and active-job coalescing still apply. Limits bound
each pass rather than silently truncating the collection's lifetime history.

Playlist insertion time, video publication time and DevFeed discovery time are separate.
Reordering a playlist never renews a video's publication/feed dates. Video IDs deduplicate
globally across channels, playlists, direct imports and normalized RSS YouTube links.
Collection membership and import provenance remain auditable. Removal is confirmed only
after a complete scan and a bounded provider lookup; removing one origin cannot withdraw
a public video with another approved active origin. A provider-confirmed private/deleted
video is withheld from public results. Availability changes become visible after a
successful refresh and the reader's normal response-cache expiry.

Admin imports accept up to 25 YouTube resources per batch; ordinary publisher import
limits stay unchanged. Very large collections take multiple scheduled passes. No search
API calls, watch-page scraping, transcript fetching or embedded playback is used.

Provider references: [channels.list](https://developers.google.com/youtube/v3/docs/channels/list),
[playlistItems.list](https://developers.google.com/youtube/v3/docs/playlistItems/list),
[videos.list](https://developers.google.com/youtube/v3/docs/videos/list),
[quota model](https://developers.google.com/youtube/v3/getting-started), and
[API key headers](https://docs.cloud.google.com/docs/authentication/api-keys-use).
