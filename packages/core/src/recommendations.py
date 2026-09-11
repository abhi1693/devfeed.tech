"""Bounded materialized recommendations. Serving never invokes this computation."""

import heapq
import logging
import uuid
from collections import defaultdict
from datetime import timedelta

from sqlalchemy import delete, func, or_, select, text
from sqlalchemy.dialects.postgresql import insert

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
    UserTopic,
    utcnow,
)
from devfeed_core.publication import visible_article
from devfeed_core.user_settings import FeedSettings

logger = logging.getLogger(__name__)
MAX_INTERESTS = 200
MAX_RECOMMENDATIONS = 500
CANDIDATE_BUDGET = 10000
REFRESH_HOURS = 6
EXPIRY_HOURS = 24
REDISPATCH_SECONDS = 300


def has_recommendation_work(user_id):
    """Keep stale edges eligible for cleanup even after the last input is removed."""
    return or_(
        *[
            select(model.user_id).where(model.user_id == user_id).exists()
            for model in (UserTopic, UserSource, ArticleLike, UserInterest, UserRecommendation)
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
            now = utcnow()
            for state in states:
                if state.next_refresh_at > now:
                    state.next_refresh_at = now
                    state.dispatched_at = None
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
    """Durable redispatch after Redis loss or an interrupted worker, without leases."""
    count = 0
    now = utcnow()
    for _ in range(batch):
        with factory.begin() as session:
            state = session.scalar(
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
                .order_by(UserRecommendationState.next_refresh_at)
                .limit(1)
                .with_for_update(skip_locked=True)
            )
            if state is None:
                break
            queue.enqueue(
                "devfeed_aggregator.recommendation_tasks.refresh",
                str(state.user_id),
                job_timeout=30,
                result_ttl=0,
                failure_ttl=86400,
                ttl=REDISPATCH_SECONDS,
            )
            state.dispatched_at = now
            count += 1
    return count


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


def ranked_candidates(session, user_id, now, interest_count, content_types):
    # At most 10,000 candidates, up to 500 per interest. Only scoring data is loaded.
    rows = session.execute(
        text("""
        SELECT i.topic_id, i.seed_topic_id, NULL::uuid AS source_id,
               i.weight, i.reason, a.id, a.feed_at
        FROM user_interests i
        CROSS JOIN LATERAL (
          SELECT article.id, article.feed_at
          FROM article_topics link JOIN articles article ON article.id = link.article_id
          WHERE link.topic_id = i.topic_id AND link.role IN ('primary', 'supporting')
            AND article.publication_status = 'published' AND article.review_status = 'approved'
            AND article.content_type = ANY(:content_types)
            AND EXISTS (SELECT 1 FROM article_origins origin
                        JOIN sources source ON source.id = origin.source_id
                        WHERE origin.article_id = article.id
                          AND source.approval_status = 'approved')
          ORDER BY article.feed_at DESC, article.id DESC LIMIT :candidate_limit
        ) a
        WHERE i.user_id = :user_id
        UNION ALL
        SELECT NULL::uuid, NULL::uuid, f.source_id, 100, 'followed_source', a.id, a.feed_at
        FROM user_sources f JOIN sources source ON source.id = f.source_id
        CROSS JOIN LATERAL (
          SELECT DISTINCT article.id, article.feed_at
          FROM article_origins origin JOIN articles article ON article.id = origin.article_id
          WHERE origin.source_id = f.source_id
            AND article.publication_status = 'published' AND article.review_status = 'approved'
            AND article.content_type = ANY(:content_types)
          ORDER BY article.feed_at DESC, article.id DESC LIMIT :candidate_limit
        ) a
        WHERE f.user_id = :user_id AND source.approval_status = 'approved'
    """),
        {
            "user_id": user_id,
            "content_types": content_types,
            "candidate_limit": min(MAX_RECOMMENDATIONS, CANDIDATE_BUDGET // interest_count),
        },
    ).mappings()
    unique: dict[uuid.UUID, dict] = {}
    for row in rows:
        age_days = max(0, (now - row["feed_at"]).total_seconds() / 86400)
        score = row["weight"] + 40 / (1 + age_days / 7)
        candidate = dict(row, score=score)
        previous = unique.get(row["id"])
        if previous is None or (score, str(row["source_id"] or row["topic_id"])) > (
            previous["score"],
            str(previous["source_id"] or previous["topic_id"]),
        ):
            unique[row["id"]] = candidate
    groups = defaultdict(list)
    for row in unique.values():
        groups[(row["reason"] == "followed_source", row["source_id"] or row["topic_id"])].append(
            row
        )
    heap: list[tuple[float, float, str, int, tuple[bool, uuid.UUID]]] = []
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
    try:
        with factory.begin() as session:
            session.execute(text("SET LOCAL statement_timeout = '5s'"))
            state = session.scalar(
                select(UserRecommendationState)
                .where(
                    UserRecommendationState.user_id == user_id,
                    UserRecommendationState.next_refresh_at <= utcnow(),
                )
                .with_for_update(skip_locked=True)
            )
            if state is None:
                return 0
            if not session.scalar(select(has_recommendation_work(user_id))):
                return 0
            now = utcnow()
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
            interest_count = len(selected) + source_count
            settings = FeedSettings.model_validate(
                session.scalar(select(UserAccount.feed_settings).where(UserAccount.id == user_id))
            )
            ranked = (
                ranked_candidates(session, user_id, now, interest_count, settings.content_types)
                if interest_count
                else []
            )
            session.execute(delete(UserRecommendation).where(UserRecommendation.user_id == user_id))
            if ranked:
                session.execute(insert(UserRecommendation), ranked)
            state.generation = uuid.uuid4()
            state.invalidated = False
            state.computed_at = now
            state.expires_at = now + timedelta(hours=EXPIRY_HOURS)
            state.next_refresh_at = now + timedelta(hours=REFRESH_HOURS)
            state.dispatched_at = None
            state.attempts = 0
            state.interest_count = interest_count
            return len(ranked)
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
