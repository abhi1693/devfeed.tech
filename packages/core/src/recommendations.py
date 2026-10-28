"""Bounded materialized recommendations. Serving never invokes this computation."""

import heapq
import logging
import math
import time
import uuid
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert

from devfeed_core.cache import CacheUnavailable
from devfeed_core.delivery import delivery_id
from devfeed_core.feed_generations import publish_generation
from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleTopic,
    RecommendationSourceEvent,
    RecommendationTopicEvent,
    Source,
    Topic,
    TopicRelation,
    UserAccount,
    UserInterest,
    UserRecommendation,
    UserRecommendationState,
    UserSource,
    UserTag,
    UserTopic,
    utcnow,
)
from devfeed_core.publication import visible_article
from devfeed_core.user_settings import normalize_feed_settings

logger = logging.getLogger(__name__)
MAX_INTERESTS = 200
MAX_RECOMMENDATIONS = 500
CANDIDATE_BUDGET = 10000
REFRESH_HOURS = 6
RANK_HOURS = 6
EXPIRY_HOURS = 24
REDISPATCH_SECONDS = 300


def has_recommendation_work(user_id):
    """Keep stale edges eligible for cleanup even after the last input is removed."""
    return or_(
        *[
            select(model.user_id).where(model.user_id == user_id).exists()
            for model in (
                UserTopic,
                UserSource,
                UserTag,
                ArticleLike,
                UserInterest,
                UserRecommendation,
            )
        ]
    )


def request_recommendation_refresh(session, user_id):
    """Durably request a rebuild using the same state lock as the worker."""
    if not session.scalar(select(has_recommendation_work(user_id))):
        return False
    session.execute(
        insert(UserRecommendationState).values(user_id=user_id).on_conflict_do_nothing()
    )
    state = session.scalar(
        select(UserRecommendationState)
        .where(UserRecommendationState.user_id == user_id)
        .with_for_update()
    )
    if not state.invalidated or state.attempts or state.next_refresh_at > utcnow():
        state.invalidated = True
        state.candidates_dirty = True
        state.next_refresh_at = utcnow()
        state.dispatched_at = None
        state.attempts = 0
    # A pending, due request is already queued. Preserve its dispatch marker.
    session.flush()
    return True


def _expand_events(factory, model, event_key, membership, membership_key, batch):
    """One indexed topic/user page, checkpointed atomically with refresh requests."""
    with factory.begin() as session:
        event = session.scalar(
            select(model)
            .order_by(model.created_at, event_key)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if event is None:
            return 0
        users = select(membership.user_id).where(membership_key == getattr(event, event_key.key))
        if event.cursor:
            users = users.where(membership.user_id > event.cursor)
        ids = list(session.scalars(users.order_by(membership.user_id).limit(batch)))
        if ids:
            # Lock in deterministic order, matching the worker's state-first locking.
            states = session.scalars(
                select(UserRecommendationState)
                .where(UserRecommendationState.user_id.in_(ids))
                .order_by(UserRecommendationState.user_id)
                .with_for_update()
            )
            for state in states:
                state.candidates_dirty = True
            event.cursor = ids[-1]
            # Round-robin topic pages; large audiences do not starve others.
            event.created_at = utcnow()
        if len(ids) < batch:
            if event.version != event.pass_version:
                event.pass_version, event.cursor = event.version, None
                event.created_at = utcnow()  # Let other topics run between completed passes.
            else:
                session.delete(event)
        return len(ids)


def expand_recommendation_events(factory, batch=100):
    topics = _expand_events(
        factory,
        RecommendationTopicEvent,
        RecommendationTopicEvent.topic_id,
        UserInterest,
        UserInterest.topic_id,
        batch,
    )
    sources = _expand_events(
        factory,
        RecommendationSourceEvent,
        RecommendationSourceEvent.source_id,
        UserSource,
        UserSource.source_id,
        batch,
    )
    return topics + sources


def dispatch_recommendations(factory, queue, batch=25):
    """Publish one locked batch; durable identities repair interrupted publication."""
    if batch < 1:
        return 0
    now = utcnow()
    logs = []
    with factory.begin() as session:
        states = session.scalars(
            select(UserRecommendationState)
            .where(
                UserRecommendationState.next_refresh_at <= now,
                has_recommendation_work(UserRecommendationState.user_id),
                or_(
                    UserRecommendationState.dispatched_at.is_(None),
                    UserRecommendationState.dispatched_at
                    < now - timedelta(seconds=REDISPATCH_SECONDS),
                ),
            )
            .order_by(
                UserRecommendationState.computed_at.is_not(None),
                UserRecommendationState.next_refresh_at,
                UserRecommendationState.user_id,
            )
            .limit(batch)
            .with_for_update(skip_locked=True)
        ).all()
        for state in states:
            queue.enqueue(
                "devfeed_aggregator.recommendation_tasks.refresh",
                str(state.user_id),
                job_timeout=30,
                result_ttl=0,
                failure_ttl=86400,
                ttl=None,
                job_id=delivery_id("recommendations", state.user_id, state.next_refresh_at),
                unique=True,
                at_front=state.computed_at is None,
            )
            state.dispatched_at = now
            logs.append(
                {
                    "first_feed": state.computed_at is None,
                    "wait_ms": max(0, round((now - state.next_refresh_at).total_seconds() * 1000)),
                }
            )
    for fields in logs:
        logger.info("recommendation_refresh_dispatched", extra=fields)
    return len(logs)


def interests(session, user_id):
    """Explicit follows dominate likes; inferred interests expand one directed hop."""
    follows = list(
        session.scalars(
            select(Topic.id)
            .join(UserTopic)
            .where(UserTopic.user_id == user_id, Topic.status == "active")
            .order_by(Topic.id)
            .limit(100)
        )
    )
    result = {
        tid: dict(topic_id=tid, seed_topic_id=tid, weight=100, reason="followed_topic")
        for tid in follows
    }
    liked = (
        select(ArticleLike.article_id)
        .where(ArticleLike.user_id == user_id)
        .order_by(ArticleLike.created_at.desc(), ArticleLike.article_id)
        .limit(100)
        .subquery()
    )
    liked_topics = session.execute(
        select(Topic.id, func.count().label("likes"))
        .select_from(liked)
        .join(Article, Article.id == liked.c.article_id)
        .join(ArticleTopic, ArticleTopic.article_id == Article.id)
        .join(Topic)
        .where(
            visible_article(),
            Topic.status == "active",
            ArticleTopic.role.in_(["primary", "supporting"]),
        )
        .group_by(Topic.id)
        .order_by(func.count().desc(), Topic.id)
        .limit(100)
    )
    for tid, count in liked_topics:
        result.setdefault(
            tid,
            dict(
                topic_id=tid,
                seed_topic_id=tid,
                weight=min(80, 55 + count * 5),
                reason="liked_topic",
            ),
        )
    seeds = dict(result)
    if seeds:
        other = TopicRelation.related_topic_id
        relations = session.execute(
            select(TopicRelation.topic_id, other, TopicRelation.relation)
            .where(
                or_(
                    TopicRelation.topic_id.in_(seeds),
                    (TopicRelation.relation == "related_to") & other.in_(seeds),
                )
            )
            .order_by(TopicRelation.topic_id, other, TopicRelation.relation)
            .limit(4000)
        ).all()
        active = (
            set(
                session.scalars(
                    select(Topic.id).where(
                        Topic.id.in_({t for row in relations for t in row[:2]}),
                        Topic.status == "active",
                    )
                )
            )
            if relations
            else set()
        )
        for source, target, relation in relations:
            if source not in active or target not in active:
                continue
            if relation not in {
                "related_to",
                "uses_language",
                "depends_on",
                "implements",
                "part_of",
            }:
                continue
            directions = [(source, target)]
            if relation == "related_to":
                directions.append((target, source))
            for seed, topic in directions:
                if seed not in seeds:
                    continue
                weight = int(seeds[seed]["weight"] * 0.4)
                if topic not in result or result[topic]["weight"] < weight:
                    result[topic] = dict(
                        topic_id=topic,
                        seed_topic_id=seed,
                        weight=weight,
                        reason="related_topic",
                    )
    return sorted(result.values(), key=lambda row: (-row["weight"], row["topic_id"]))[
        :MAX_INTERESTS
    ]


# Both paths retain the same per-interest limits and eligibility rules.
_LATERAL_CANDIDATES_SQL = """
SELECT i.topic_id, i.seed_topic_id, NULL::uuid AS source_id, NULL::uuid AS tag_id,
       i.weight, i.reason, a.id, a.feed_at
FROM user_interests i
CROSS JOIN LATERAL (
  SELECT article.id, article.feed_at
  FROM article_topics link JOIN articles article ON article.id = link.article_id
  WHERE link.topic_id = i.topic_id AND link.role IN ('primary', 'supporting')
    AND article.publication_status = 'published' AND article.review_status = 'approved'
    AND article.content_type = ANY(:content_types)
    AND article.language = ANY(:languages)
    AND EXISTS (SELECT 1 FROM article_origins origin
                JOIN sources source ON source.id = origin.source_id
                WHERE origin.article_id = article.id
                  AND source.approval_status = 'approved')
  ORDER BY article.feed_at DESC, article.id DESC LIMIT :candidate_limit
) a
WHERE i.user_id = :user_id
UNION ALL
SELECT NULL::uuid, NULL::uuid, f.source_id, NULL::uuid,
       100, 'followed_source', a.id, a.feed_at
FROM user_sources f JOIN sources source ON source.id = f.source_id
CROSS JOIN LATERAL (
  SELECT DISTINCT article.id, article.feed_at
  FROM article_origins origin JOIN articles article ON article.id = origin.article_id
  WHERE origin.source_id = f.source_id
    AND article.publication_status = 'published' AND article.review_status = 'approved'
    AND article.content_type = ANY(:content_types)
    AND article.language = ANY(:languages)
  ORDER BY article.feed_at DESC, article.id DESC LIMIT :candidate_limit
) a
WHERE f.user_id = :user_id AND source.approval_status = 'approved'
UNION ALL
SELECT NULL::uuid, NULL::uuid, NULL::uuid, f.tag_id, 100, 'followed_tag', a.id, a.feed_at
FROM user_tags f
CROSS JOIN LATERAL (
  SELECT article.id, article.feed_at
  FROM article_tags link JOIN articles article ON article.id = link.article_id
  WHERE link.tag_id = f.tag_id
    AND article.publication_status = 'published' AND article.review_status = 'approved'
    AND article.content_type = ANY(:content_types)
    AND article.language = ANY(:languages)
    AND EXISTS (SELECT 1 FROM article_origins origin
                JOIN sources source ON source.id = origin.source_id
                WHERE origin.article_id = article.id
                  AND source.approval_status = 'approved')
  ORDER BY article.feed_at DESC, article.id DESC LIMIT :candidate_limit
) a
WHERE f.user_id = :user_id
"""

# Share eligibility across signals before partitioned limits. Separate covered
# projections use the existing facet/feed indexes without scanning article bodies.
_BROAD_CANDIDATES_SQL = """
WITH public_article_ids AS MATERIALIZED (
  SELECT article.id FROM articles article
  WHERE article.publication_status='published' AND article.review_status='approved'
    AND article.content_type=ANY(:content_types) AND article.language=ANY(:languages)
), article_order AS MATERIALIZED (
  SELECT article.id,article.feed_at FROM articles article
), eligible_articles AS MATERIALIZED (
  SELECT article.id,article.feed_at FROM article_order article
  JOIN public_article_ids visible ON visible.id=article.id
  WHERE EXISTS (SELECT 1 FROM article_origins origin
                JOIN sources source ON source.id=origin.source_id
                WHERE origin.article_id=article.id AND source.approval_status='approved')
), topic_candidates AS (
  SELECT i.topic_id, i.seed_topic_id, i.weight, i.reason, article.id, article.feed_at,
         row_number() OVER (PARTITION BY i.topic_id
             ORDER BY article.feed_at DESC, article.id DESC) AS candidate_rank
  FROM user_interests i
  JOIN article_topics link ON link.topic_id=i.topic_id
  JOIN eligible_articles article ON article.id=link.article_id
  WHERE i.user_id=:user_id AND link.role IN ('primary','supporting')
), source_matches AS (
  SELECT DISTINCT f.source_id, article.id, article.feed_at
  FROM user_sources f
  JOIN sources source ON source.id=f.source_id
  JOIN article_origins origin ON origin.source_id=f.source_id
  JOIN eligible_articles article ON article.id=origin.article_id
  WHERE f.user_id=:user_id AND source.approval_status='approved'
), source_candidates AS (
  SELECT source_id,id,feed_at,
         row_number() OVER (PARTITION BY source_id ORDER BY feed_at DESC,id DESC) AS candidate_rank
  FROM source_matches
), tag_candidates AS (
  SELECT f.tag_id, article.id, article.feed_at,
         row_number() OVER (PARTITION BY f.tag_id
             ORDER BY article.feed_at DESC, article.id DESC) AS candidate_rank
  FROM user_tags f JOIN article_tags link ON link.tag_id=f.tag_id
  JOIN eligible_articles article ON article.id=link.article_id
  WHERE f.user_id=:user_id
)
SELECT topic_id,seed_topic_id,NULL::uuid AS source_id,NULL::uuid AS tag_id,weight,reason,id,feed_at
FROM topic_candidates WHERE candidate_rank<=:candidate_limit
UNION ALL
SELECT NULL::uuid,NULL::uuid,source_id,NULL::uuid,100,'followed_source',id,feed_at
FROM source_candidates WHERE candidate_rank<=:candidate_limit
UNION ALL
SELECT NULL::uuid,NULL::uuid,NULL::uuid,tag_id,100,'followed_tag',id,feed_at
FROM tag_candidates WHERE candidate_rank<=:candidate_limit
"""


def _association_row_estimate(row):
    """Estimate selected association rows from ordinary PostgreSQL column statistics."""
    try:
        total = float(row["total_rows"])
        distinct = float(row["n_distinct"])
        null_fraction = float(row["null_frac"])
        frequencies = [float(value) for value in row["frequencies"] or []]
    except (TypeError, ValueError):
        return None
    if (
        not all(math.isfinite(value) for value in (total, distinct, null_fraction))
        or total <= 0
        or distinct == 0
        or not 0 <= null_fraction <= 1
    ):
        return None
    if distinct < 0:
        distinct *= -total
    identities = row["identities"] or []
    if (
        len(identities) != len(frequencies)
        or len(set(identities)) != len(identities)
        or distinct < len(identities)
        or any(not math.isfinite(value) or not 0 <= value <= 1 for value in frequencies)
    ):
        return None
    common = dict(zip(identities, frequencies, strict=True))
    remaining = 1 - null_fraction - sum(frequencies)
    if remaining < -0.000001:
        return None
    typical = max(0, remaining) / max(1, distinct - len(identities))
    return total * sum(common.get(identifier, typical) for identifier in set(row["selected_ids"]))


def _reuse_candidate_visibility(session, user_id):
    """Share eligibility when selected associations repeat at least one catalog pass.

    These are advisory estimates only. Missing statistics retain indexed probes;
    neither plan changes which articles are eligible or the per-signal budgets.
    """
    rows = session.execute(
        text("""
        WITH selected AS (
          SELECT 'article_topics' AS relation,topic_id::text AS identity
          FROM user_interests WHERE user_id=:user_id
          UNION ALL
          SELECT 'article_origins',f.source_id::text
          FROM user_sources f JOIN sources source ON source.id=f.source_id
          WHERE f.user_id=:user_id AND source.approval_status='approved'
          UNION ALL
          SELECT 'article_tags',tag_id::text FROM user_tags WHERE user_id=:user_id
        ), inputs AS (
          SELECT relation,array_agg(DISTINCT identity) AS selected_ids
          FROM selected GROUP BY relation
        )
        SELECT inputs.selected_ids,relation.reltuples AS total_rows,
               stats.n_distinct,stats.null_frac,
               stats.most_common_vals::text::text[] AS identities,
               stats.most_common_freqs AS frequencies,
               (SELECT reltuples FROM pg_class WHERE oid='articles'::regclass) AS article_rows
        FROM inputs
        LEFT JOIN pg_class relation ON relation.relname=inputs.relation
          AND relation.relnamespace=current_schema()::regnamespace
        LEFT JOIN pg_stats stats ON stats.schemaname=current_schema()
          AND stats.tablename=inputs.relation AND NOT stats.inherited
          AND stats.attname=CASE inputs.relation
            WHEN 'article_topics' THEN 'topic_id'
            WHEN 'article_origins' THEN 'source_id' ELSE 'tag_id' END
    """),
        {"user_id": user_id},
    ).mappings()
    repeated_rows = 0.0
    article_rows = 0.0
    for row in rows:
        estimate = _association_row_estimate(row)
        if estimate is None or row["article_rows"] is None:
            return False
        article_rows = float(row["article_rows"])
        if not math.isfinite(article_rows) or article_rows <= 0:
            return False
        repeated_rows += estimate
    return article_rows > 0 and repeated_rows >= article_rows


def _use_broad_candidate_query(session, interest_count, content_types, languages, user_id=None):
    # Dense/narrow profiles benefit from early-exit LATERAL probes. For broad,
    # selective profiles, rank qualifying matches in sets instead of repeating
    # sparse filtering per follow. Estimates select a strategy, never eligibility.
    if interest_count < 8:
        return False
    rows = session.execute(
        text("""
        SELECT attname, most_common_vals::text::text[] AS identities,
               most_common_freqs AS frequencies
        FROM pg_stats
        WHERE schemaname=current_schema() AND tablename='articles'
          AND attname IN ('content_type','language') AND NOT inherited
    """)
    ).mappings()
    selected = {"content_type": set(content_types), "language": set(languages)}
    fractions = {}
    for row in rows:
        identities, frequencies = row["identities"], row["frequencies"]
        if not identities or not frequencies or len(identities) != len(frequencies):
            return False
        fractions[row["attname"]] = sum(
            frequency
            for identity, frequency in zip(identities, frequencies, strict=True)
            if identity in selected[row["attname"]]
        )
    if len(fractions) != 2:
        return False
    if fractions["content_type"] * fractions["language"] <= 0.25:
        return True
    return user_id is not None and _reuse_candidate_visibility(session, user_id)


def ranked_candidates(session, user_id, now, interest_count, content_types, languages):
    candidate_sql = (
        _BROAD_CANDIDATES_SQL
        if _use_broad_candidate_query(session, interest_count, content_types, languages, user_id)
        else _LATERAL_CANDIDATES_SQL
    )
    # At most 10,000 candidates, up to 500 per interest. Only scoring data is loaded.
    rows = session.execute(
        text(candidate_sql),
        {
            "user_id": user_id,
            "content_types": content_types,
            "languages": languages,
            "candidate_limit": min(MAX_RECOMMENDATIONS, CANDIDATE_BUDGET // interest_count),
        },
    ).mappings()
    unique: dict[uuid.UUID, dict] = {}
    for row in rows:
        age_days = max(0, (now - row["feed_at"]).total_seconds() / 86400)
        score = row["weight"] + 40 / (1 + age_days / 7)
        candidate = dict(row, score=score)
        previous = unique.get(row["id"])
        if previous is None or (
            score,
            str(row["source_id"] or row.get("tag_id") or row["topic_id"]),
        ) > (
            previous["score"],
            str(previous["source_id"] or previous.get("tag_id") or previous["topic_id"]),
        ):
            unique[row["id"]] = candidate
    groups = defaultdict(list)
    for row in unique.values():
        groups[
            (
                2 if row["reason"] == "followed_tag" else int(row["reason"] == "followed_source"),
                row["source_id"] or row.get("tag_id") or row["topic_id"],
            )
        ].append(row)
    heap: list[tuple[float, float, str, int, tuple[int, uuid.UUID]]] = []
    for topic, group in groups.items():
        group.sort(key=lambda r: (r["score"], r["feed_at"], r["id"]), reverse=True)
        heapq.heappush(
            heap,
            (
                -group[0]["score"],
                -group[0]["feed_at"].timestamp(),
                str(topic),
                0,
                topic,
            ),
        )
    output: list[dict] = []
    while heap and len(output) < MAX_RECOMMENDATIONS:
        negative_score, _, _, offset, topic = heapq.heappop(heap)
        row = groups[topic][offset]
        output.append(
            dict(
                user_id=user_id,
                article_id=row["id"],
                position=len(output) + 1,
                score=-negative_score,
                topic_id=row["topic_id"],
                seed_topic_id=row["seed_topic_id"],
                source_id=row["source_id"],
                tag_id=row.get("tag_id"),
                reason=row["reason"],
            )
        )
        if offset + 1 < len(groups[topic]):
            following = groups[topic][offset + 1]
            # A modest diversity penalty prevents one interest dominating every position.
            score = following["score"] - min(25, (offset + 1) * 2)
            heapq.heappush(
                heap,
                (
                    -score,
                    -following["feed_at"].timestamp(),
                    str(topic),
                    offset + 1,
                    topic,
                ),
            )
    return output


def refresh_recommendations(factory, user_id):
    """Publish all edges and generation atomically; crashes leave the prior list intact."""
    user_id = uuid.UUID(str(user_id))
    started = time.monotonic()
    try:
        with factory.begin() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            state = session.scalar(
                select(UserRecommendationState)
                .where(
                    UserRecommendationState.user_id == user_id,
                    UserRecommendationState.next_refresh_at <= utcnow(),
                )
                .with_for_update()
            )
            if state is None:
                return 0
            if not session.scalar(select(has_recommendation_work(user_id))):
                return 0
            now = utcnow()
            logger.info(
                "recommendation_refresh_started",
                extra={
                    "queue_wait_ms": max(
                        0, round((now - state.dispatched_at).total_seconds() * 1000)
                    )
                    if state.dispatched_at
                    else None,
                },
            )
            rebuild = (
                state.invalidated
                or state.candidates_dirty
                or state.ranked_at is None
                or state.ranked_at <= now - timedelta(hours=RANK_HOURS)
            )
            if rebuild:
                selected = interests(session, user_id)
                session.execute(delete(UserInterest).where(UserInterest.user_id == user_id))
                if selected:
                    session.execute(
                        insert(UserInterest),
                        [dict(user_id=user_id, **row) for row in selected],
                    )
                source_count = session.scalar(
                    select(func.count())
                    .select_from(UserSource)
                    .join(Source)
                    .where(UserSource.user_id == user_id, Source.approval_status == "approved")
                )
                tag_count = session.scalar(
                    select(func.count()).select_from(UserTag).where(UserTag.user_id == user_id)
                )
                interest_count = len(selected) + source_count + tag_count
                settings = normalize_feed_settings(
                    session.scalar(
                        select(UserAccount.feed_settings).where(UserAccount.id == user_id)
                    )
                )
                ranked = (
                    ranked_candidates(
                        session,
                        user_id,
                        now,
                        interest_count,
                        settings.content_types,
                        settings.languages,
                    )
                    if interest_count
                    else []
                )
                session.execute(
                    delete(UserRecommendation).where(UserRecommendation.user_id == user_id)
                )
                if ranked:
                    session.execute(insert(UserRecommendation), ranked)
                # Redis orders depend on this candidate pool and its reasons.
                # Invalidate old cursors atomically with replacement, even when
                # preferences did not change. Early recovery shuffles keep the epoch.
                state.preference_revision += 1
                state.ranked_at = now
                state.candidates_dirty = False
                state.interest_count = interest_count
            try:
                count = publish_generation(session, state)
            except CacheUnavailable:
                # Keep freshly ranked DB candidates usable during a cache outage.
                state.generation = None
                count = session.scalar(
                    select(func.count())
                    .select_from(UserRecommendation)
                    .where(UserRecommendation.user_id == user_id)
                )
            state.invalidated = False
            state.computed_at = now
            state.expires_at = now + timedelta(hours=EXPIRY_HOURS)
            state.next_refresh_at = now + timedelta(hours=REFRESH_HOURS)
            state.dispatched_at = None
            state.attempts = 0
        logger.info(
            "recommendation_refresh_completed",
            extra={"count": count, "duration_ms": round((time.monotonic() - started) * 1000)},
        )
        return count
    except Exception:
        # Retry indefinitely with bounded backoff. Never expose raw database errors to users.
        with factory.begin() as session:
            state = session.scalar(
                select(UserRecommendationState)
                .where(UserRecommendationState.user_id == user_id)
                .with_for_update()
            )
            if state is not None and state.next_refresh_at <= utcnow():
                state.attempts += 1
                state.next_refresh_at = utcnow() + timedelta(
                    seconds=min(900, 30 * 2 ** min(state.attempts, 5))
                )
                state.dispatched_at = None
        logger.exception("recommendations_refresh_failed")
        raise
