# Precomputed recommendations

My feed reads a prepared Redis sequence of article IDs and hydrates eligible articles
from PostgreSQL. Interest expansion, ranking and hourly shuffling run in background
workers, independently of AI and full-automation flags. Anonymous
browsing and the Latest feed keep their existing behavior.

## Data and ranking

- `user_recommendation_states`: current generation, invalidation, freshness,
  redispatch timestamp, retry count and next refresh time. Migration `0012` adds
  candidate invalidation, last ranking time and a preference revision to this existing
  table. New accounts receive a row automatically through the account trigger.
- `user_interests`: up to 200 weighted user/topic relationships, recording the seed
  topic and whether the interest came from a follow, like, or related topic.
- `user_recommendations`: up to 500 ranked user/article relationships with a score,
  reason, matching topic and seed topic, or an explicitly followed source. These are
  the candidate pool and the unshuffled fallback; no new preferences or generated-order
  table is needed. The user/position index serves fallback pagination.
- Redis lists: at most 500 binary UUIDs per user/preference revision/generation,
  retained for three hours. Empty generations use a single empty-byte marker.
- `user_sources`: up to 100 explicit source subscriptions per user, independently
  of topic selections. An index on source/user supports audience fanout.
- `recommendation_source_events`: durable source changes with the same bounded
  fanout and retry behavior as topic events.
- `recommendation_topic_events`: durable, coalesced catalogue changes with a cursor
  for bounded fanout to affected users via the topic/user interest index.

Follows have base weight 100. The latest 100 likes contribute base weights 60–80
through active primary/supporting article topics; ineligible articles do not seed
interests. A single approved relationship hop contributes 40% of the seed weight.
`related_to` is symmetric; `uses_language`, `depends_on`, `implements`, and `part_of`
are followed only in their saved direction. Pending proposals and incidental or
comparison-only article assignments are excluded. Inferred interests never create
explicit follows or notification subscriptions.

Candidate generation loads at most 10,000 lightweight records, divided across
topic interests and approved source subscriptions, with at most 500 per interest.
Source follows have weight 100 and include eligible articles regardless of topic
assignments. Articles matching both a topic and source are deduplicated. Freshness contributes up to 40 points, decaying
with age. Repeated results from the same winning topic or source receive a bounded diversity
penalty. Scores and final positions are computed together; this is deterministic
ranking, not a trained collaborative-filtering model. Opens are not used as a
positive recommendation signal, and no anonymous identities are linked to accounts.

## Automatic refresh and correctness

Database triggers record follows, likes, article classification/visibility changes,
source approval changes, and topic/relationship changes in the writing transaction.
This includes bulk SQL and cascades, not only ORM hooks. Follow and like mutations
immediately invalidate the user's generation for rebuilding. Existing DB candidates
remain available while the replacement is prepared. Removing all
follows and likes hides the previous generation immediately.

Each scheduler tick expands a bounded topic/user page and source/user page, then dispatches due refreshes
to the existing ingestion/background RQ queue. Both use `DEVFEED_SCHEDULER_BATCH_SIZE`.
Topic pages rotate fairly; changes arriving during a large fanout cause another pass
after the current pass completes, rather than repeatedly restarting its first page.
Only users whose materialized interests include the affected topic or who follow
the affected source are queued.

Workers lock one state row with `SKIP LOCKED`. They rebuild candidates after input
changes or when ranking is six hours old; otherwise they reuse the stored candidates.
Every hour, a weighted shuffle gives higher-ranked candidates more chance of appearing
near the top, with a soft limit of two articles per publisher in the first eight slots
when alternatives exist. Each generation uses a fresh seed and contains no duplicate IDs.

The worker atomically writes an immutable Redis list before committing its generation
pointer in PostgreSQL. Concurrent user mutations either precede the refresh or invalidate
it afterward. Database errors roll back and retry with bounded backoff (up to 15 minutes).
An unreferenced Redis list expires naturally. Redis publication failures instead commit
the ranked DB candidates and retry the shuffle in five minutes. A lost Redis job or
killed worker is redispatched after five minutes. Worker jobs have a 30-second timeout;
individual computation statements have a five-second timeout.

Hourly rotation targets users active in the last 48 hours, plus explicitly invalidated
users. Feed reads update the existing account activity timestamp at most every 15 minutes,
so returning users become eligible again. The schedule is best effort under queue load;
prepared candidates become stale after 24 hours. Catalogue changes request earlier ranking.
Current publication/source/topic-assignment checks always apply. Source reasons also
check that the user still follows the approved originating source. Preference changes
invalidate older cursors, and the UI polls for replacement recommendations with backoff.
Initial loads and pending refreshes work even when a new tab has not received focus.

`GET /v1/user/feed` returns `status`, `generation`, `has_interests` and per-article `reasons`
alongside `items` and `next_cursor`. New tabs select the latest generation. Open tabs pin
it through the optional `generation` query parameter. Cursors contain owner, generation
and position; another owner's cursor is rejected. Older hourly generations remain readable
until their Redis TTL expires, provided preferences and the candidate pool have not changed.
Candidate rebuilds advance the sequence revision in the same transaction as replacing
the ranked rows; older cursors return 409 with a restart action. Hourly shuffles that
reuse candidates retain earlier generations. Live eligibility
checks can omit withdrawn candidates without changing the order of surviving IDs.

If a fresh request cannot read its Redis sequence, it serves the existing database ranking
without shuffling. Fallback cursors stay in DB order when Redis recovers, until candidates
are rebuilt. A lost shuffled sequence or obsolete cursor returns 409 with a restart action,
so pagination never silently switches order. Missing current lists schedule a bounded
background retry; repeated fallback requests do not keep writing refresh requests.
Authentication sessions still require Redis independently of the recommendation cache.

A repeatable-read snapshot keeps state and candidate reads consistent. Normal ready
nonempty responses use six SQL statements with Redis, or seven for DB fallback, including
batched article metadata reads. Infrequent activity/recovery writes add a short transaction.
Refreshing responses also check remaining work and interests. A bounded scan of at most
500 IDs fills withdrawn/reclassified holes. No graph traversal, ranking or shuffling runs
on the request path. Private lists have an environment-scoped namespace and do not depend
on the optional public response-cache flag.

## Content preferences

Feed settings store the selected content types alongside the display layout; existing
accounts default to all six types. At least one type is required. Workers apply the
selection before candidate limits and ranking, and a settings change immediately
invalidates the prepared generation. Layout-only changes keep the current generation.
Article type changes queue affected topic and source followers; serving also checks
the current type so an excluded article cannot appear while refresh is pending.

Latest, topic, source and search feeds resolve account preferences on the web server
and apply a validated multi-type filter in the public query before pagination. Private
cookies go only to the user API; shared public cache entries vary by filter values.
Explicit content-type tabs override defaults for that view. Notification subscriptions
continue to follow the selected topics and sources.

## Source publication notifications

Publication events snapshot approved source IDs alongside active topic IDs. Recipients
are the union of users following either at publication time; overlapping subscriptions
produce one notification. Delivery rechecks current follows and article eligibility.
The existing Chimely feed preference controls both topics and sources. Following a
source does not subscribe the user to its topics or send historical notifications.

## Inspection and operation

The admin user detail header includes **Rerun analysis**. Its authenticated,
CSRF-protected `POST /v1/admin/users/{user_id}/analysis` records a durable refresh
request and returns 202. The existing scheduler and worker rebuild interests and
recommendations; no ranking runs inside the admin request. Repeated pending requests
preserve dispatch state, and retrying a failed run clears its backoff. The detail
view reloads after queuing and follows the admin's configured refresh interval.

Users with no followed topics, followed sources, likes, stored interests, or prepared
recommendations are skipped by the scheduler and worker. The admin action is disabled
and its API returns 409 for these accounts. Their personal feed returns an empty ready
result rather than waiting for analysis. Adding a follow or like makes the account
eligible again through the existing invalidation triggers. Stored interests or
recommendations still qualify for a refresh so removing the last input can clean up
the previous results.

The admin knowledge graph has an optional Users layer. It shows explicit `follows`
and `likes`, derived `interested_in`, and current `recommended` edges. User labels
use display names; email addresses, provider subjects and authentication data are
excluded. User relationships are not exposed through public graph routes. Recommendation
and interest edges disappear from the projection while their generation is invalid
or expired. Recommendation scores are displayed as scores, not relevance percentages.

Apply migrations through `0012` before starting the updated API,
scheduler and workers. The baseline includes source subscriptions and transactional
source-change events. No separate service or graph database is required. Scheduler results include
`recommendation_users_queued` and `recommendations_dispatched`; durable state exposes
retry counts and overdue refresh times for diagnosis. Retention is bounded per user,
and account/article deletion cascades remove associated DB recommendations. Redis lists
expire automatically; live reads never expose deleted accounts or articles.

Validate with the recommendation, personalization, knowledge-graph and discovery
query-budget tests. The discovery profiler supports 10,000–100,000 synthetic articles
and records actual PostgreSQL EXPLAIN/BUFFERS output and request timings; results are
local measurements, not production latency guarantees.

The local 10,000-article profile for this change used five requests per case:

| Prepared feed | Database statements | Median request time |
| --- | ---: | ---: |
| 1 article | 6 | 20.5 ms |
| 100 articles | 6 | 46.1 ms |
| 100 articles, 100 followed topics | 6 | 56.9 ms |

EXPLAIN checks bound ordinary article reads to the requested page rather than all
500 prepared candidates. These small samples use TestClient with authentication overridden
and are not production latency guarantees.

A separate local Docker Redis benchmark shuffled and stored 500 pre-ranked IDs for each
of 10,000 users in 21.8 seconds. One generation added about 104 MB of Redis memory;
reading 25 IDs took 0.35 ms median, 0.50 ms p95. This excludes database candidate ranking,
RQ scheduling/forking, authentication and article hydration. Three hourly generations
need roughly three times the list storage; preference changes can temporarily add more.

Regression coverage includes old-generation pagination, Redis loss/corruption and DB
fallback, worker publication failure, inactive-user reactivation and bounded query counts.
Website and built Chrome/Edge browser checks verify open-tab stability, new-tab rotation,
refresh recovery and existing reader journeys.
