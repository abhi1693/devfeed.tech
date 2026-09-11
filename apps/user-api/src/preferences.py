"""User-owned topic preferences and uncached personalized discovery."""

import uuid
from typing import Annotated

from devfeed_core.models import Article, ArticleTopic, Topic, UserAccount, UserTopic, utcnow
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut, FeedPage
from devfeed_http.cursors import decode_cursor, encode_cursor
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, insert, literal, select, tuple_

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB

router = APIRouter(prefix="/v1/user", tags=["personalization"])


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_ids: Annotated[list[uuid.UUID], Field(max_length=100)]


@router.get("/preferences", response_model=Preferences)
def preferences(user: User, session: DB):
    ids = session.scalars(
        select(UserTopic.topic_id)
        .join(Topic)
        .where(UserTopic.user_id == uuid.UUID(user.user_id), Topic.status == "active")
        .order_by(UserTopic.topic_id)
    ).all()
    return Preferences(topic_ids=list(ids))


@router.put("/preferences", response_model=Preferences)
def save_preferences(payload: Preferences, user: User, session: DB):
    ids = sorted(set(payload.topic_ids))
    # Serialize updates from multiple tabs, and bind ownership only to the session.
    account = session.scalar(
        select(UserAccount.id)
        .where(
            UserAccount.id == uuid.UUID(user.user_id),
            UserAccount.issuer == user.issuer,
            UserAccount.subject == user.subject,
            UserAccount.organization_id == user.organization_id,
        )
        .with_for_update()
    )
    if account is None:
        raise HTTPException(401, "User account unavailable")
    active = set(
        session.scalars(select(Topic.id).where(Topic.id.in_(ids), Topic.status == "active"))
    )
    if active != set(ids):
        raise HTTPException(422, "Choose active topics")
    session.execute(delete(UserTopic).where(UserTopic.user_id == account))
    if ids:
        session.execute(
            insert(UserTopic),
            [
                {"user_id": account, "topic_id": topic_id, "created_at": utcnow()}
                for topic_id in ids
            ],
        )
    session.commit()
    return Preferences(topic_ids=ids)


@router.get("/feed", response_model=FeedPage)
def feed(
    user: User,
    session: DB,
    limit: int = Query(24, ge=1, le=100),
    cursor: str | None = Query(None, max_length=300),
):
    followed = select(UserTopic.topic_id).where(UserTopic.user_id == uuid.UUID(user.user_id))
    statement = select(Article).where(
        visible_article(),
        Article.topic_links.any(
            ArticleTopic.topic_id.in_(followed)
            & ArticleTopic.role.in_(["primary", "supporting"])
            & ArticleTopic.topic.has(Topic.status == "active")
        ),
    )
    if cursor:
        date, identifier = decode_cursor(cursor)
        statement = statement.where(
            tuple_(Article.feed_at, Article.id) < tuple_(literal(date), literal(identifier))
        )
    articles = session.scalars(
        statement.order_by(Article.feed_at.desc(), Article.id.desc()).limit(limit + 1)
    ).all()
    items = articles[:limit]
    return FeedPage(
        items=[ArticleOut.from_article(item) for item in items],
        next_cursor=encode_cursor(items[-1]) if len(articles) > limit else None,
    )
