"""Read prepared user/article edges; never traverse the knowledge graph on a request."""

import base64
import binascii
import json
import uuid
from typing import Literal

from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.models import (
    Article,
    ArticleOrigin,
    ArticleTopic,
    Source,
    Topic,
    UserRecommendation,
    UserRecommendationState,
    UserSource,
    utcnow,
)
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut, FeedPage
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select, text

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB

router = APIRouter(prefix="/v1/user", tags=["personalization"])


class RecommendationReason(BaseModel):
    kind: Literal["followed_topic", "liked_topic", "related_topic", "followed_source"]
    topic_id: uuid.UUID | None = None
    seed_topic_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None


class RecommendationPage(FeedPage):
    status: Literal["ready", "refreshing"] = "ready"
    has_interests: bool = False
    reasons: dict[str, RecommendationReason] = Field(default_factory=dict)


def decode_position(cursor, user_id):
    try:
        owner, generation, position = json.loads(
            base64.b64decode(cursor, altchars=b"-_", validate=True)
        )
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
):
    user_id = uuid.UUID(user.user_id)
    position = decode_position(cursor, user_id) if cursor else None
    # One shared snapshot prevents a refresh replacing ranks between state and article reads.
    # This is a brief read transaction; it does not block refreshes or user mutations.
    session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
    state = session.get(UserRecommendationState, user_id)
    if state is None:
        raise HTTPException(401, "User account unavailable")
    now = utcnow()
    if state.invalidated or state.expires_at is None or state.expires_at <= now:
        return RecommendationPage(
            items=[],
            next_cursor=None,
            status="refreshing",
            has_interests=bool(state.interest_count),
        )
    if position and position[0] != state.generation:
        raise HTTPException(409, "Your recommendations have changed. Start from the first page.")
    candidates_query = select(UserRecommendation).where(UserRecommendation.user_id == user_id)
    if position:
        candidates_query = candidates_query.where(UserRecommendation.position > position[1])
    candidates = list(
        session.scalars(candidates_query.order_by(UserRecommendation.position).limit(limit + 1))
    )
    statement = (
        select(Article, UserRecommendation)
        .join(UserRecommendation, UserRecommendation.article_id == Article.id)
        .where(
            UserRecommendation.user_id == user_id,
            visible_article(),
            (
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
                .exists()
            )
            | Article.topic_links.any(
                (ArticleTopic.topic_id == UserRecommendation.topic_id)
                & ArticleTopic.role.in_(["primary", "supporting"])
                & ArticleTopic.topic.has(Topic.status == "active")
            ),
        )
        .options(*PUBLIC_ARTICLE_OPTIONS)
        .order_by(UserRecommendation.position)
        .limit(limit + 1)
    )
    rows = (
        list(
            session.execute(statement.where(Article.id.in_([rec.article_id for rec in candidates])))
        )
        if candidates
        else []
    )
    if len(candidates) == limit + 1 and len(rows) < len(candidates):
        # One bounded fallback fills holes caused by withdrawals since computation.
        # Ordinary reads hydrate only the requested ranked IDs, not all 500 candidates.
        rows.extend(
            session.execute(
                statement.where(UserRecommendation.position > candidates[-1].position).limit(
                    limit + 1 - len(rows)
                )
            )
        )
    page = rows[:limit]
    next_cursor = None
    if len(rows) > limit:
        next_cursor = base64.urlsafe_b64encode(
            json.dumps([str(user_id), str(state.generation), page[-1][1].position]).encode()
        ).decode()
    return RecommendationPage(
        items=[ArticleOut.from_article(article) for article, _ in page],
        next_cursor=next_cursor,
        has_interests=state.interest_count > 0,
        reasons={
            str(rec.article_id): RecommendationReason(
                kind=rec.reason,
                topic_id=rec.topic_id,
                seed_topic_id=rec.seed_topic_id,
                source_id=rec.source_id,
            )
            for _, rec in page
        },
    )
