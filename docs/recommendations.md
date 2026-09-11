# Precomputed recommendations

My feed reads stored user/article relationships. Interest expansion and ranking run
in background workers, independently of AI and full-automation flags. Anonymous
browsing and the Latest feed keep their existing behavior.

## Data and ranking

- `user_recommendation_states`: current generation, invalidation, freshness,
  redispatch timestamp, retry count and next refresh time. New and existing accounts
  receive a row automatically through migration `0020_user_recommendations`.
- `user_interests`: up to 200 weighted user/topic relationships, recording the seed
  topic and whether the interest came from a follow, like, or related topic.
- `user_recommendations`: up to 500 ranked user/article relationships with a score,
  reason, matching topic and seed topic. The user/position index serves pagination.
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
interests, with at most 500 per topic. Freshness contributes up to 40 points, decaying
with age. Repeated results from the same winning topic receive a bounded diversity
penalty. Scores and final positions are computed together; this is deterministic
ranking, not a trained collaborative-filtering model. Opens are not used as a
positive recommendation signal, and no anonymous identities are linked to accounts.

## Automatic refresh and correctness

Database triggers record follows, likes, article classification/visibility changes,
source approval changes, and topic/relationship changes in the writing transaction.
This includes bulk SQL and cascades, not only ORM hooks. Follow and like mutations
immediately invalidate the user's generation; the API and UI hide it until rebuilt.

Each scheduler tick expands a bounded topic/user page, then dispatches due refreshes
to the existing ingestion/background RQ queue. Both use `DEVFEED_SCHEDULER_BATCH_SIZE`.
Topic pages rotate fairly; changes arriving during a large fanout cause another pass
after the current pass completes, rather than repeatedly restarting its first page.
Only users whose materialized interests include the affected topic are queued.

Workers lock one state row with `SKIP LOCKED`, rebuild its interests and ranked
articles, then publish a new generation atomically. Concurrent user mutations either
precede the refresh or invalidate it afterward. Exceptions roll back the entire
refresh and retry with bounded backoff (up to 15 minutes). A lost Redis job or killed
worker is redispatched after five minutes. The worker job has a 30-second timeout;
individual computation statements have a five-second timeout. Expensive profiles
therefore fail and retry instead of holding unbounded database work.

Ready lists refresh at least every six hours when scheduler/worker capacity allows;
they expire after 24 hours. Catalogue changes request earlier refreshes. During
catalogue refresh, a nonexpired generation can still be served, with current
publication/source/topic-assignment checks. Changes to inferred interest relevance
become visible after background refresh. On an explicit follow/like change, invalidation
is immediate. Rebuilding starts automatically, and the UI polls with backoff while
waiting; hidden tabs do not make feed requests.

`GET /v1/user/feed` returns `status`, `has_interests` and per-article `reasons` alongside
`items` and `next_cursor`. Cursors contain owner, generation and position. A different
owner is rejected; an older generation returns 409 with a restart action in the UI.
A repeatable-read snapshot prevents mixing state and rows from different generations.
The normal nonempty response performs seven database statements, including the snapshot
and batched article metadata reads. A bounded fallback fills holes from withdrawn or
reclassified candidates. No graph traversal or ranking runs on the request path.

## Inspection and operation

The admin knowledge graph has an optional Users layer. It shows explicit `follows`
and `likes`, derived `interested_in`, and current `recommended` edges. User labels
use display names; email addresses, provider subjects and authentication data are
excluded. User relationships are not exposed through public graph routes. Recommendation
and interest edges disappear from the projection while their generation is invalid
or expired. Recommendation scores are displayed as scores, not relevance percentages.

Apply migrations through `0021_recommendation_churn` before starting the updated API,
scheduler and workers. The follow-up migration adds the recent-likes index and skips
unchanged publication/source assignments when scheduling refreshes. No separate
service or graph database is required. Scheduler results include
`recommendation_users_queued` and `recommendations_dispatched`; durable state exposes
retry counts and overdue refresh times for diagnosis. Retention is bounded per user,
and account/article deletion cascades remove associated stored recommendations.

Validate with the recommendation, personalization, knowledge-graph and discovery
query-budget tests. The discovery profiler supports 10,000–100,000 synthetic articles
and records actual PostgreSQL EXPLAIN/BUFFERS output and request timings; results are
local measurements, not production latency guarantees.

The local 10,000-article profile on 2026-09-11 used three requests per case:

| Prepared feed | Database statements | Median request time |
| --- | ---: | ---: |
| 1 article | 7 | 21.2 ms |
| 100 articles | 7 | 49.9 ms |
| 100 articles, 100 followed topics | 7 | 51.7 ms |

EXPLAIN checks bound ordinary article reads to the requested page rather than all
500 prepared candidates. These small samples verify the query shape; they are not
a production capacity estimate. Browser verification used disposable PostgreSQL,
Redis, real RQ workers and Chimely, covering follows, likes, unlikes, new publication,
notification delivery, and four viewport widths in both themes.
