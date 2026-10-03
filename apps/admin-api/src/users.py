"""User inspection and recommendation refresh through the admin contract."""

import logging
import uuid
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from devfeed_core.models import (
    Article,
    ArticleBookmark,
    ArticleLike,
    Source,
    Topic,
    UserAccount,
    UserInterest,
    UserLink,
    UserMustRead,
    UserReadingDay,
    UserReadingEvent,
    UserReadingStreak,
    UserRecommendation,
    UserRecommendationState,
    UserSource,
    UserStackAssociation,
    UserTopic,
    utcnow,
)
from devfeed_core.must_reads import read_snapshot
from devfeed_core.recommendations import request_recommendation_refresh
from devfeed_core.services import OperationConflict
from devfeed_core.user_settings import (
    DevCardSettings,
    FeedSettings,
    NotificationSettings,
    ProfileLink,
    ProfileVisibility,
    UserAppearanceSettings,
)
from devfeed_core.user_settings import (
    UserReadingStreak as ReadingStreakSettings,
)
from fastapi import APIRouter, Depends, HTTPException, Query
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
    username: str | None = None
    followed_topics: int = 0
    followed_sources: int = 0
    liked_articles: int = 0
    bookmarks: int = 0
    reads: int = 0
    last_read_at: datetime | None = None

    @classmethod
    def from_account(cls, account):
        return cls(
            id=account.id,
            name=account.profile.get("display_name") or account.name or "Unnamed user",
            email=account.email,
            avatar_url=account.profile.get("avatar_url"),
            created_at=account.created_at,
            last_seen_at=account.last_seen_at,
            username=account.username,
        )


class AdminUserDetail(AdminUserOut):
    sign_in_name: str | None
    interests: int
    recommendations: int
    reading_days: int
    profile_bio: str | None
    profile_location: str | None
    profile_about: str | None
    profile_public: bool
    profile_links: list[ProfileLink]
    stack: list["AdminUserStack"]
    dev_card: DevCardSettings
    dev_card_technologies: list["AdminUserTechnology"]
    reading_streak: ReadingStreakSettings
    feed_preferences: FeedSettings
    appearance_preferences: UserAppearanceSettings
    notification_preferences: NotificationSettings
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


class AdminUserStack(BaseModel):
    id: uuid.UUID
    name: str
    section: str
    since_year: int | None


class AdminUserTechnology(BaseModel):
    id: uuid.UUID
    name: str


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


class AdminUserBookmark(BaseModel):
    id: uuid.UUID
    title: str
    publication_status: str
    bookmarked_at: datetime


class AdminUserRead(BaseModel):
    id: str
    article_id: uuid.UUID
    title: str | None
    publication_status: str | None
    opened_at: datetime
    read_date: date


class AdminUserReadingDay(BaseModel):
    id: date
    read_date: date
    article_count: int
    last_read_at: datetime


class AdminUserInterest(BaseModel):
    id: uuid.UUID
    name: str | None
    status: str | None
    seed_topic_id: uuid.UUID
    seed_topic_name: str | None
    weight: int
    reason: str


class AdminUserMustRead(BaseModel):
    id: uuid.UUID
    title: str
    position: int
    reason: str
    read: bool


class AdminUserMustReads(BaseModel):
    selection_date: date
    timezone: str
    generated: bool
    presented_at: datetime | None
    items: list[AdminUserMustRead]


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
            text_search(
                query.q, name, UserAccount.username, UserAccount.email, cast(UserAccount.id, String)
            )
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
            "username": UserAccount.username,
            "created_at": UserAccount.created_at,
            "last_seen_at": UserAccount.last_seen_at,
        },
        default="-created_at",
    )
    accounts = page["items"]
    if accounts:
        ids = [account.id for account in accounts]
        counts = {
            row.id: row
            for row in session.execute(
                select(
                    UserAccount.id,
                    select(func.count())
                    .select_from(UserTopic)
                    .where(UserTopic.user_id == UserAccount.id)
                    .scalar_subquery()
                    .label("topics"),
                    select(func.count())
                    .select_from(UserSource)
                    .where(UserSource.user_id == UserAccount.id)
                    .scalar_subquery()
                    .label("sources"),
                    select(func.count())
                    .select_from(ArticleLike)
                    .where(ArticleLike.user_id == UserAccount.id)
                    .scalar_subquery()
                    .label("likes"),
                    select(func.count())
                    .select_from(ArticleBookmark)
                    .where(ArticleBookmark.user_id == UserAccount.id)
                    .scalar_subquery()
                    .label("bookmarks"),
                    select(func.max(UserReadingDay.last_read_at))
                    .where(UserReadingDay.user_id == UserAccount.id)
                    .scalar_subquery()
                    .label("last_read_at"),
                ).where(UserAccount.id.in_(ids))
            )
        }
        page["items"] = [
            AdminUserOut.from_account(account).model_copy(
                update={
                    "followed_topics": counts[account.id].topics,
                    "followed_sources": counts[account.id].sources,
                    "liked_articles": counts[account.id].likes,
                    "bookmarks": counts[account.id].bookmarks,
                    "last_read_at": counts[account.id].last_read_at,
                }
            )
            for account in accounts
        ]
    return page


@router.get("/{user_id}", response_model=AdminUserDetail, operation_id="admin_user_get")
def user(user_id: uuid.UUID, session: DB):
    account = record(session, UserAccount, user_id)
    models = (
        UserTopic,
        ArticleLike,
        UserInterest,
        UserRecommendation,
        UserSource,
        ArticleBookmark,
        UserReadingDay,
        UserReadingEvent,
    )
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
    streak = session.get(UserReadingStreak, user_id)
    links = session.scalars(
        select(UserLink).where(UserLink.user_id == user_id).order_by(UserLink.position)
    ).all()
    stack = session.execute(
        select(UserStackAssociation, Topic)
        .join(Topic, Topic.id == UserStackAssociation.topic_id)
        .where(UserStackAssociation.user_id == user_id)
        .order_by(UserStackAssociation.position)
    ).all()
    card = DevCardSettings.model_validate(account.profile.get("dev_card", {}))
    selected_technologies = set(card.technologies) if card.technologies is not None else None
    technologies = [
        AdminUserTechnology(id=item.topic_id, name=topic.name)
        for item, topic in stack
        if item.section != "past"
        and (selected_technologies is None or item.topic_id in selected_technologies)
    ]
    last_read_at = session.scalar(
        select(func.max(UserReadingDay.last_read_at)).where(UserReadingDay.user_id == user_id)
    )
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
        **AdminUserOut.from_account(account).model_dump(
            exclude={
                "followed_topics",
                "followed_sources",
                "liked_articles",
                "bookmarks",
                "reads",
                "last_read_at",
            }
        ),
        sign_in_name=account.name,
        followed_topics=counts[0],
        followed_sources=counts[4],
        bookmarks=counts[5],
        reads=counts[7],
        last_read_at=last_read_at,
        reading_days=counts[6],
        liked_articles=counts[1],
        interests=counts[2],
        recommendations=counts[3],
        feed_status=status,
        computed_at=state.computed_at if state else None,
        next_refresh_at=state.next_refresh_at if state else None,
        expires_at=state.expires_at if state else None,
        refresh_attempts=state.attempts if state else 0,
        profile_bio=account.profile.get("bio"),
        profile_location=account.profile.get("location"),
        profile_about=account.about,
        profile_public=ProfileVisibility.model_validate(
            account.profile.get("visibility", {})
        ).public,
        profile_links=[{"label": link.label, "url": link.url} for link in links],
        stack=[
            {
                "id": str(item.topic_id),
                "name": topic.name,
                "section": item.section,
                "since_year": item.since_year,
            }
            for item, topic in stack
        ],
        dev_card=card,
        dev_card_technologies=technologies,
        reading_streak={
            "current_days": streak.current_days if streak else 0,
            "longest_days": streak.longest_days if streak else 0,
            "total_days": streak.total_days if streak else 0,
            "last_read_date": streak.last_read_date if streak else None,
        },
        feed_preferences=FeedSettings.model_validate(account.feed_settings),
        appearance_preferences=UserAppearanceSettings.model_validate(account.appearance_settings),
        notification_preferences=NotificationSettings.model_validate(account.notification_settings),
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
    logger.info("user_analysis_requested", extra={"user_id": user_id.hex})
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
    "/{user_id}/bookmarks",
    response_model=Page[AdminUserBookmark],
    operation_id="admin_user_bookmarks",
)
def bookmarks(user_id: uuid.UUID, session: DB, query: Listing):
    require_record(session, UserAccount, user_id)
    statement = select(ArticleBookmark).join(Article).where(ArticleBookmark.user_id == user_id)
    if query.q:
        statement = statement.where(text_search(query.q, Article.title))
    page = paginate(
        session,
        statement,
        query,
        {"title": Article.title, "bookmarked_at": ArticleBookmark.created_at},
        "-bookmarked_at",
    )
    labels = article_labels(session, [row.article_id for row in page["items"]])
    page["items"] = [
        dict(
            id=row.article_id,
            title=labels[row.article_id].title,
            publication_status=labels[row.article_id].publication_status,
            bookmarked_at=row.created_at,
        )
        for row in page["items"]
    ]
    return page


@router.get("/{user_id}/reads", response_model=Page[AdminUserRead], operation_id="admin_user_reads")
def reads(user_id: uuid.UUID, session: DB, query: Listing):
    require_record(session, UserAccount, user_id)
    statement = (
        select(UserReadingEvent)
        .outerjoin(Article, Article.id == UserReadingEvent.article_id)
        .where(UserReadingEvent.user_id == user_id)
    )
    if query.q:
        statement = statement.where(text_search(query.q, Article.title))
    page = paginate(
        session,
        statement,
        query,
        {"title": Article.title, "opened_at": UserReadingEvent.occurred_at},
        "-opened_at",
    )
    labels = article_labels(session, [row.article_id for row in page["items"]])
    page["items"] = [
        dict(
            id=f"{row.read_date}:{row.article_id}",
            article_id=row.article_id,
            title=labels[row.article_id].title if row.article_id in labels else None,
            publication_status=labels[row.article_id].publication_status
            if row.article_id in labels
            else None,
            opened_at=row.occurred_at,
            read_date=row.read_date,
        )
        for row in page["items"]
    ]
    return page


@router.get(
    "/{user_id}/reading-days",
    response_model=Page[AdminUserReadingDay],
    operation_id="admin_user_reading_days",
)
def reading_days(user_id: uuid.UUID, session: DB, query: Listing):
    require_record(session, UserAccount, user_id)
    statement = select(UserReadingDay).where(UserReadingDay.user_id == user_id)
    if query.q:
        statement = statement.where(text_search(query.q, cast(UserReadingDay.read_date, String)))
    page = paginate(
        session,
        statement,
        query,
        {"read_date": UserReadingDay.read_date, "article_count": UserReadingDay.article_count},
        "-read_date",
    )
    page["items"] = [
        dict(
            id=row.read_date,
            read_date=row.read_date,
            article_count=row.article_count,
            last_read_at=row.last_read_at,
        )
        for row in page["items"]
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
    "/{user_id}/must-reads",
    response_model=AdminUserMustReads,
    operation_id="admin_user_must_reads",
)
def must_reads(user_id: uuid.UUID, session: DB, timezone: str | None = Query(None, max_length=100)):
    account = record(session, UserAccount, user_id)
    if timezone is None:
        previous = session.scalar(
            select(UserMustRead)
            .where(UserMustRead.user_id == user_id)
            .order_by(UserMustRead.selection_date.desc())
            .limit(1)
        )
        # A browser-local preference has no stored IANA zone. Use the actual
        # selection's zone when available, rather than the inspecting admin's zone.
        configured = UserAppearanceSettings.model_validate(account.appearance_settings).timezone
        timezone = previous.timezone if previous else configured if configured != "local" else "UTC"
    try:
        day = utcnow().astimezone(ZoneInfo(timezone)).date()
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "Invalid timezone") from exc
    snapshot = session.get(UserMustRead, (user_id, day))
    articles, reasons, read_ids = read_snapshot(
        session, snapshot, FeedSettings.model_validate(account.feed_settings), include_details=False
    )
    positions = (
        {pick["id"]: index + 1 for index, pick in enumerate(snapshot.picks)} if snapshot else {}
    )
    return AdminUserMustReads(
        selection_date=day,
        timezone=snapshot.timezone if snapshot else timezone,
        generated=snapshot is not None,
        presented_at=snapshot.presented_at if snapshot else None,
        items=[
            AdminUserMustRead(
                id=article.id,
                title=article.title,
                position=positions[str(article.id)],
                reason=reasons[str(article.id)],
                read=article.id in read_ids,
            )
            for article in articles
        ],
    )


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
