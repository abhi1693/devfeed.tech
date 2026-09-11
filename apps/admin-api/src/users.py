"""User inspection and recommendation refresh through the admin contract."""

import logging
import uuid
from datetime import datetime
from typing import Literal

from devfeed_core.models import (
    Article,
    ArticleLike,
    Source,
    Topic,
    UserAccount,
    UserInterest,
    UserRecommendation,
    UserRecommendationState,
    UserSource,
    UserTopic,
    utcnow,
)
from devfeed_core.recommendations import request_recommendation_refresh
from devfeed_core.services import OperationConflict
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import String, cast, func, select

from devfeed_admin_api.auth import require_admin
from devfeed_admin_api.dependencies import DB
from devfeed_admin_api.pagination import Listing, Page, paginate, record, require_record
from devfeed_admin_api.search import text_search

router = APIRouter(
    prefix="/v1/admin/users", tags=["admin-users"], dependencies=[Depends(require_admin)]
)
logger = logging.getLogger(__name__)


class AdminUserOut(BaseModel):
    id: uuid.UUID
    name: str
    email: str | None
    avatar_url: str | None
    created_at: datetime
    last_seen_at: datetime

    @classmethod
    def from_account(cls, account):
        return cls(
            id=account.id,
            name=account.profile.get("display_name") or account.name or "Unnamed user",
            email=account.email,
            avatar_url=account.profile.get("avatar_url"),
            created_at=account.created_at,
            last_seen_at=account.last_seen_at,
        )


class AdminUserDetail(AdminUserOut):
    sign_in_name: str | None
    followed_topics: int
    followed_sources: int = 0
    liked_articles: int
    interests: int
    recommendations: int
    feed_status: Literal["pending", "refreshing", "expired", "ready"]
    computed_at: datetime | None
    next_refresh_at: datetime | None
    expires_at: datetime | None
    refresh_attempts: int


class AdminUserTopic(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    followed_at: datetime


class AdminUserSource(BaseModel):
    id: uuid.UUID
    name: str
    status: str
    followed_at: datetime


class AdminUserLike(BaseModel):
    id: uuid.UUID
    title: str
    publication_status: str
    liked_at: datetime


class AdminUserInterest(BaseModel):
    id: uuid.UUID
    name: str | None
    status: str | None
    seed_topic_id: uuid.UUID
    seed_topic_name: str | None
    weight: int
    reason: str


class AdminUserRecommendation(BaseModel):
    id: uuid.UUID
    title: str
    publication_status: str
    position: int
    score: float
    reason: str
    topic_id: uuid.UUID | None
    topic_name: str | None
    seed_topic_id: uuid.UUID | None
    seed_topic_name: str | None
    source_id: uuid.UUID | None = None
    source_name: str | None = None


@router.get("", response_model=Page[AdminUserOut], operation_id="admin_users_list")
def users(
    session: DB,
    query: Listing,
    interests: Literal["following", "liked", "none"] | None = None,
):
    name = func.coalesce(
        UserAccount.profile["display_name"].astext, UserAccount.name, "Unnamed user"
    )
    statement = select(UserAccount)
    if query.q:
        statement = statement.where(
            text_search(query.q, name, UserAccount.email, cast(UserAccount.id, String))
        )
    follows = (
        select(UserTopic.user_id).where(UserTopic.user_id == UserAccount.id).exists()
        | select(UserSource.user_id).where(UserSource.user_id == UserAccount.id).exists()
    )
    likes = select(ArticleLike.user_id).where(ArticleLike.user_id == UserAccount.id).exists()
    if interests == "following":
        statement = statement.where(follows)
    elif interests == "liked":
        statement = statement.where(likes)
    elif interests == "none":
        statement = statement.where(~follows, ~likes)
    page = paginate(
        session,
        statement,
        query,
        {
            "name": name,
            "email": UserAccount.email,
            "created_at": UserAccount.created_at,
            "last_seen_at": UserAccount.last_seen_at,
        },
        default="-created_at",
    )
    page["items"] = [AdminUserOut.from_account(account) for account in page["items"]]
    return page


@router.get("/{user_id}", response_model=AdminUserDetail, operation_id="admin_user_get")
def user(user_id: uuid.UUID, session: DB):
    account = record(session, UserAccount, user_id)
    models = (UserTopic, ArticleLike, UserInterest, UserRecommendation, UserSource)
    counts = session.execute(
        select(
            *[
                select(func.count())
                .select_from(model)
                .where(model.user_id == user_id)
                .scalar_subquery()
                for model in models
            ]
        )
    ).one()
    state = session.get(UserRecommendationState, user_id)
    status: Literal["pending", "refreshing", "expired", "ready"] = "pending"
    if state is not None and state.generation is not None:
        status = (
            "refreshing"
            if state.invalidated
            else (
                "expired" if state.expires_at is None or state.expires_at <= utcnow() else "ready"
            )
        )
    return AdminUserDetail(
        **AdminUserOut.from_account(account).model_dump(),
        sign_in_name=account.name,
        followed_topics=counts[0],
        followed_sources=counts[4],
        liked_articles=counts[1],
        interests=counts[2],
        recommendations=counts[3],
        feed_status=status,
        computed_at=state.computed_at if state else None,
        next_refresh_at=state.next_refresh_at if state else None,
        expires_at=state.expires_at if state else None,
        refresh_attempts=state.attempts if state else 0,
    )


@router.post(
    "/{user_id}/analysis",
    response_model=AdminUserDetail,
    status_code=202,
    operation_id="admin_user_analysis",
)
def analyze_user(user_id: uuid.UUID, session: DB):
    require_record(session, UserAccount, user_id)
    if not request_recommendation_refresh(session, user_id):
        raise OperationConflict("No follows, likes, interests, or recommendations to analyze")
    session.commit()
    logger.info("user_analysis_requested", extra={"user_id": str(user_id)})
    return user(user_id, session)


@router.get(
    "/{user_id}/topics", response_model=Page[AdminUserTopic], operation_id="admin_user_topics"
)
def topics(user_id: uuid.UUID, session: DB, query: Listing):
    require_record(session, UserAccount, user_id)
    statement = select(UserTopic).join(Topic).where(UserTopic.user_id == user_id)
    if query.q:
        statement = statement.where(text_search(query.q, Topic.name))
    page = paginate(
        session,
        statement,
        query,
        {"name": Topic.name, "followed_at": UserTopic.created_at},
        "-followed_at",
    )
    labels = topic_labels(session, [row.topic_id for row in page["items"]])
    page["items"] = [
        dict(
            id=row.topic_id,
            name=labels[row.topic_id].name,
            status=labels[row.topic_id].status,
            followed_at=row.created_at,
        )
        for row in page["items"]
        if row.topic_id in labels
    ]
    return page


@router.get(
    "/{user_id}/sources", response_model=Page[AdminUserSource], operation_id="admin_user_sources"
)
def sources(user_id: uuid.UUID, session: DB, query: Listing):
    require_record(session, UserAccount, user_id)
    statement = select(UserSource).join(Source).where(UserSource.user_id == user_id)
    if query.q:
        statement = statement.where(text_search(query.q, Source.name))
    page = paginate(
        session,
        statement,
        query,
        {"name": Source.name, "followed_at": UserSource.created_at},
        "-followed_at",
    )
    labels = {
        row.id: row
        for row in session.execute(
            select(Source.id, Source.name, Source.approval_status).where(
                Source.id.in_([row.source_id for row in page["items"]])
            )
        )
    }
    page["items"] = [
        dict(
            id=row.source_id,
            name=labels[row.source_id].name,
            status=labels[row.source_id].approval_status,
            followed_at=row.created_at,
        )
        for row in page["items"]
    ]
    return page


@router.get("/{user_id}/likes", response_model=Page[AdminUserLike], operation_id="admin_user_likes")
def likes(user_id: uuid.UUID, session: DB, query: Listing):
    require_record(session, UserAccount, user_id)
    statement = select(ArticleLike).join(Article).where(ArticleLike.user_id == user_id)
    if query.q:
        statement = statement.where(text_search(query.q, Article.title))
    page = paginate(
        session,
        statement,
        query,
        {"title": Article.title, "liked_at": ArticleLike.created_at},
        "-liked_at",
    )
    labels = article_labels(session, [row.article_id for row in page["items"]])
    page["items"] = [
        dict(
            id=row.article_id,
            title=labels[row.article_id].title,
            publication_status=labels[row.article_id].publication_status,
            liked_at=row.created_at,
        )
        for row in page["items"]
        if row.article_id in labels
    ]
    return page


@router.get(
    "/{user_id}/interests",
    response_model=Page[AdminUserInterest],
    operation_id="admin_user_interests",
)
def inferred_interests(user_id: uuid.UUID, session: DB, query: Listing):
    require_record(session, UserAccount, user_id)
    statement = (
        select(UserInterest)
        .outerjoin(Topic, Topic.id == UserInterest.topic_id)
        .where(UserInterest.user_id == user_id)
    )
    if query.q:
        statement = statement.where(text_search(query.q, Topic.name))
    page = paginate(
        session,
        statement,
        query,
        {"name": Topic.name, "weight": UserInterest.weight, "reason": UserInterest.reason},
        "-weight",
    )
    labels = topic_labels(
        session,
        {
            tid
            for row in page["items"]
            for tid in (row.topic_id, row.seed_topic_id)
            if tid is not None
        },
    )
    page["items"] = [
        dict(
            id=row.topic_id,
            name=labels[row.topic_id].name if row.topic_id in labels else None,
            status=labels[row.topic_id].status if row.topic_id in labels else None,
            seed_topic_id=row.seed_topic_id,
            seed_topic_name=labels[row.seed_topic_id].name if row.seed_topic_id in labels else None,
            weight=row.weight,
            reason=row.reason,
        )
        for row in page["items"]
    ]
    return page


@router.get(
    "/{user_id}/recommendations",
    response_model=Page[AdminUserRecommendation],
    operation_id="admin_user_recommendations",
)
def recommendations(user_id: uuid.UUID, session: DB, query: Listing):
    require_record(session, UserAccount, user_id)
    statement = (
        select(UserRecommendation).join(Article).where(UserRecommendation.user_id == user_id)
    )
    if query.q:
        statement = statement.where(text_search(query.q, Article.title))
    page = paginate(
        session,
        statement,
        query,
        {
            "title": Article.title,
            "position": UserRecommendation.position,
            "score": UserRecommendation.score,
        },
        "position",
    )
    articles = article_labels(session, [row.article_id for row in page["items"]])
    source_ids = [row.source_id for row in page["items"] if row.source_id]
    sources = (
        {
            row.id: row.name
            for row in session.execute(
                select(Source.id, Source.name).where(Source.id.in_(source_ids))
            )
        }
        if source_ids
        else {}
    )
    labels = topic_labels(
        session,
        {
            tid
            for row in page["items"]
            for tid in (row.topic_id, row.seed_topic_id)
            if tid is not None
        },
    )
    page["items"] = [
        dict(
            id=row.article_id,
            title=articles[row.article_id].title,
            publication_status=articles[row.article_id].publication_status,
            source_id=row.source_id,
            source_name=sources.get(row.source_id),
            position=row.position,
            score=row.score,
            reason=row.reason,
            topic_id=row.topic_id,
            topic_name=labels[row.topic_id].name if row.topic_id in labels else None,
            seed_topic_id=row.seed_topic_id,
            seed_topic_name=labels[row.seed_topic_id].name if row.seed_topic_id in labels else None,
        )
        for row in page["items"]
        if row.article_id in articles
    ]
    return page


def topic_labels(session, ids):
    return (
        {
            row.id: row
            for row in session.execute(
                select(Topic.id, Topic.name, Topic.status).where(Topic.id.in_(ids))
            )
        }
        if ids
        else {}
    )


def article_labels(session, ids):
    return (
        {
            row.id: row
            for row in session.execute(
                select(Article.id, Article.title, Article.publication_status).where(
                    Article.id.in_(ids)
                )
            )
        }
        if ids
        else {}
    )
