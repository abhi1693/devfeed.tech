"""Read prepared user/article edges; never traverse the knowledge graph on a request."""

import base64
import binascii
import hashlib
import json
import logging
import uuid
from datetime import timedelta
from typing import Literal

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.cache import CacheUnavailable
from devfeed_core.feed_generations import generation_ids
from devfeed_core.models import (
    Article,
    ArticleLike,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
    UserAccount,
    UserRecommendation,
    UserRecommendationState,
    UserSource,
    UserTopic,
    utcnow,
)
from devfeed_core.publication import visible_article
from devfeed_core.recommendations import has_recommendation_work
from devfeed_core.schemas import ArticleOut, ContentType, FeedPage
from devfeed_core.user_settings import FeedSettings
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, text, update
from sqlalchemy.exc import SQLAlchemyError

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/user", tags=["personalization"])


class RecommendationReason(BaseModel):
    kind: Literal["followed_topic", "liked_topic", "related_topic", "followed_source"]
    topic_id: uuid.UUID | None = None
    seed_topic_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None


class RecommendationPage(FeedPage):
    status: Literal["ready", "refreshing"] = "ready"
    generation: uuid.UUID | None = None
    has_interests: bool = False
    reasons: dict[str, RecommendationReason] = Field(default_factory=dict)


def decode_position(cursor, user_id, scope):
    try:
        owner, generation, position, signature = json.loads(
            base64.b64decode(cursor, altchars=b"-_", validate=True)
        )
        if signature != scope:
            raise HTTPException(409, "Your feed preferences changed. Start from the first page.")
        if (
            not isinstance(owner, str)
            or not isinstance(generation, str)
            or uuid.UUID(owner) != user_id
            or type(position) is not int
            or not 0 < position <= 500
        ):
            raise ValueError("Invalid position")
        return uuid.UUID(generation), position
    except (ValueError, TypeError, binascii.Error, UnicodeError) as exc:
        raise HTTPException(422, "Invalid recommendation cursor") from exc


@router.get("/feed", response_model=RecommendationPage)
def feed(
    user: User,
    session: DB,
    limit: int = Query(24, ge=1, le=100),
    cursor: str | None = Query(None, max_length=300),
    generation: uuid.UUID | None = None,
    sort: Literal["recommended", "newest", "most_liked"] = "recommended",
    content_type: ContentType | None = None,
    source_id: uuid.UUID | None = None,
):
    user_id = uuid.UUID(user.user_id)
    # One shared snapshot prevents a refresh replacing ranks between state and article reads.
    # This is a brief read transaction; it does not block refreshes or user mutations.
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    row = session.execute(
        select(UserRecommendationState, UserAccount.last_seen_at, UserAccount.feed_settings)
        .join(UserAccount, UserAccount.id == UserRecommendationState.user_id)
        .where(UserRecommendationState.user_id == user_id)
    ).first()
    state, last_seen, stored_settings = row if row else (None, None, {})
    if state is None:
        raise HTTPException(401, "User account unavailable")
    settings = FeedSettings.model_validate(stored_settings)
    scope = hashlib.sha256(
        json.dumps(
            [sort, content_type, str(source_id), settings.languages],
            sort_keys=True,
        ).encode()
    ).hexdigest()[:16]
    position = decode_position(cursor, user_id, scope) if cursor else None
    now = utcnow()
    refreshing = state.invalidated or state.expires_at is None or state.expires_at <= now
    if refreshing and not session.scalar(select(has_recommendation_work(user_id))):
        return RecommendationPage(items=[], next_cursor=None)
    if refreshing and not session.scalar(
        select(
            or_(
                *[
                    select(model.user_id).where(model.user_id == user_id).exists()
                    for model in (UserTopic, UserSource, ArticleLike)
                ]
            )
        )
    ):
        return RecommendationPage(items=[], next_cursor=None, status="refreshing")
    # Keep the previous generation readable until its atomic replacement commits.
    # The query below still enforces current publication and user visibility rules.
    selected_generation = position[0] if position else generation or state.generation
    if position and generation is not None and generation != position[0]:
        raise HTTPException(422, "Conflicting feed generations")
    database_generation = uuid.uuid5(
        user_id, f"ranked:{state.ranked_at}:{state.preference_revision}"
    )
    use_database = selected_generation == database_generation
    offset = position[1] if position else 0
    scan_start = offset
    rows: list[tuple[Article, UserRecommendation, int]] = []
    eligible = or_(
        (UserRecommendation.source_id.is_not(None))
        & Article.origins.any(
            (ArticleOrigin.source_id == UserRecommendation.source_id)
            & ArticleOrigin.source.has(Source.approval_status == "approved")
        )
        & select(UserSource.user_id)
        .where(
            UserSource.user_id == user_id,
            UserSource.source_id == UserRecommendation.source_id,
        )
        .exists(),
        (UserRecommendation.source_id.is_(None))
        & Article.topic_links.any(
            (ArticleTopic.topic_id == UserRecommendation.topic_id)
            & ArticleTopic.role.in_(["primary", "supporting"])
            & ArticleTopic.topic.has(Topic.status == "active")
        ),
    )
    identifiers: list[uuid.UUID] | None = None
    ordered_identifiers = None
    if sort != "recommended":
        try:
            all_ids = (
                generation_ids(user_id, state.preference_revision, selected_generation, 0, 500)
                if selected_generation and not use_database
                else None
            )
        except CacheUnavailable:
            all_ids = None
        if all_ids is None and not use_database:
            if position or generation:
                raise HTTPException(409, "Your feed has expired. Start from the first page.")
            use_database = True
            selected_generation = database_generation
        likes = (
            select(func.count())
            .where(ArticleLike.article_id == Article.id)
            .correlate(Article)
            .scalar_subquery()
        )
        ordering = (
            (likes.desc(), Article.feed_at.desc(), Article.id.desc())
            if sort == "most_liked"
            else (Article.feed_at.desc(), Article.id.desc())
        )
        ordered_identifiers = list(
            session.scalars(
                select(Article.id)
                .join(UserRecommendation, UserRecommendation.article_id == Article.id)
                .where(
                    UserRecommendation.user_id == user_id,
                    *([Article.id.in_(all_ids)] if all_ids is not None else []),
                    visible_article(),
                    eligible,
                    Article.language.in_(settings.languages),
                    Article.content_type.in_(
                        [content_type] if content_type else settings.content_types
                    ),
                    *(
                        [Article.origins.any(ArticleOrigin.source_id == source_id)]
                        if source_id
                        else []
                    ),
                )
                .order_by(*ordering)
                .limit(500)
            )
        )
    # A cursor always stays with its original ordering. A fresh request can fall
    # back to the already-ranked DB candidates without doing recommendation work.
    while len(rows) <= limit and offset < 500:
        count = min(
            500 - offset, limit + 1 if offset == scan_start else max(24, limit + 1 - len(rows))
        )
        if ordered_identifiers is not None:
            identifiers = ordered_identifiers[offset : offset + count]
        elif not use_database:
            try:
                identifiers = (
                    generation_ids(
                        user_id, state.preference_revision, selected_generation, offset, count
                    )
                    if selected_generation
                    else None
                )
            except CacheUnavailable:
                identifiers = None
            if identifiers is None:
                if position or generation or rows or offset != scan_start:
                    raise HTTPException(409, "Your feed has expired. Start from the first page.")
                use_database = True
                selected_generation = database_generation
        if use_database and ordered_identifiers is None:
            ranked = session.execute(
                select(UserRecommendation.article_id, UserRecommendation.position)
                .where(UserRecommendation.user_id == user_id, UserRecommendation.position > offset)
                .order_by(UserRecommendation.position)
                .limit(count)
            ).all()
            identifiers = [identifier for identifier, _ in ranked]
            database_positions = {identifier: rank for identifier, rank in ranked}
        if not identifiers:
            break
        articles = session.execute(
            select(Article, UserRecommendation)
            .join(UserRecommendation, UserRecommendation.article_id == Article.id)
            .where(
                UserRecommendation.user_id == user_id,
                Article.id.in_(identifiers),
                visible_article(),
                eligible,
                Article.language.in_(settings.languages),
                Article.content_type.in_(
                    [content_type] if content_type else settings.content_types
                ),
                *([Article.origins.any(ArticleOrigin.source_id == source_id)] if source_id else []),
            )
            .options(*PUBLIC_ARTICLE_OPTIONS)
        )
        by_id = {article.id: (article, entry) for article, entry in articles}
        rows.extend(
            (
                *by_id[identifier],
                database_positions[identifier]
                if use_database and ordered_identifiers is None
                else offset + index + 1,
            )
            for index, identifier in enumerate(identifiers)
            if identifier in by_id
        )
        offset = (
            database_positions[identifiers[-1]]
            if use_database and ordered_identifiers is None
            else offset + len(identifiers)
        )
        if len(identifiers) < count:
            break
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([str(user_id), str(selected_generation), page[-1][2], scope]).encode()
        ).decode()
    result = RecommendationPage(
        status="refreshing" if refreshing else "ready",
        generation=selected_generation,
        items=[ArticleOut.from_article(article) for article, _, _ in page],
        next_cursor=next_cursor,
        has_interests=state.interest_count > 0,
        reasons={
            str(entry.article_id): RecommendationReason(
                kind=entry.reason,
                topic_id=entry.topic_id,
                seed_topic_id=entry.seed_topic_id,
                source_id=entry.source_id,
            )
            for _, entry, _ in page
        },
    )
    record_activity(session, user_id, last_seen, now)
    return result


def record_activity(session, user_id, last_seen, now):
    """Throttle durable activity writes; finish the read snapshot before any write."""
    if last_seen is not None and last_seen >= now - timedelta(minutes=15):
        return
    session.commit()
    try:
        session.execute(text("SET LOCAL lock_timeout = '100ms'"))
        session.execute(text("SET LOCAL statement_timeout = '200ms'"))
        session.execute(
            update(UserAccount)
            .where(
                UserAccount.id == user_id, UserAccount.last_seen_at < now - timedelta(minutes=15)
            )
            .values(last_seen_at=now)
        )
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        logger.warning("feed_activity_update_deferred")
