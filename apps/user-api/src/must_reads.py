"""Daily ranked picks; requests only read prepared recommendations."""

import uuid
from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from devfeed_core.models import (
    Article,
    Source,
    Topic,
    UserAccount,
    UserMustRead,
    UserRecommendation,
    UserRecommendationState,
    utcnow,
)
from devfeed_core.must_reads import read_snapshot
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut
from devfeed_core.user_settings import FeedSettings
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB
from devfeed_user_api.recommendations import recommendation_eligibility

router = APIRouter(prefix="/v1/user/must-reads", tags=["personalization"])


class DailySelection(BaseModel):
    date: date
    timezone: str
    items: list[ArticleOut]
    reasons: dict[str, str]
    read_ids: list[uuid.UUID]
    presented: bool
    preparing: bool = False


def selection_day(timezone):
    try:
        zone = ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(422, "Invalid timezone") from exc
    return utcnow().astimezone(zone).date()


def load_selection(session, user_id, timezone):
    day = selection_day(timezone)
    # Serialize both creation and presentation for this account across all devices.
    account = session.scalar(select(UserAccount).where(UserAccount.id == user_id).with_for_update())
    if account is None:
        raise HTTPException(401, "User account unavailable")
    settings = FeedSettings.model_validate(account.feed_settings)
    snapshot = session.get(UserMustRead, (user_id, day))
    state = session.get(UserRecommendationState, user_id)
    preparing = not state or state.invalidated or not state.ranked_at
    if snapshot is None and not preparing:
        rows = session.execute(
            select(Article, UserRecommendation)
            .join(UserRecommendation, UserRecommendation.article_id == Article.id)
            .where(
                UserRecommendation.user_id == user_id,
                visible_article(),
                recommendation_eligibility(user_id),
                Article.language.in_(settings.languages),
                Article.content_type.in_(settings.content_types),
            )
            .order_by(UserRecommendation.position)
            .limit(5)
        ).all()
        picks = []
        for article, entry in rows:
            topic = session.get(Topic, entry.topic_id) if entry.topic_id else None
            source = session.get(Source, entry.source_id) if entry.source_id else None
            seed = session.get(Topic, entry.seed_topic_id) if entry.seed_topic_id else None
            if source:
                reason = f"Because you follow {source.name}"
            elif topic and entry.reason == "followed_topic":
                reason = f"Because you follow {topic.name}"
            elif topic and entry.reason == "liked_topic":
                reason = f"Based on articles you liked about {topic.name}"
            elif seed:
                reason = f"Related to your interest in {seed.name}"
            else:
                reason = "Selected from your recommendations"
            picks.append({"id": str(article.id), "reason": reason})
        if picks:
            snapshot = UserMustRead(
                user_id=user_id, selection_date=day, timezone=timezone, picks=picks
            )
            session.add(snapshot)
            session.flush()
    articles, reasons, read_ids = read_snapshot(session, snapshot, settings)
    result = DailySelection(
        date=day,
        timezone=snapshot.timezone if snapshot else timezone,
        items=[ArticleOut.from_article(article) for article in articles],
        reasons=reasons,
        read_ids=read_ids,
        presented=bool(snapshot and snapshot.presented_at),
        preparing=preparing and snapshot is None,
    )
    return snapshot, result


@router.get("", response_model=DailySelection)
def daily(user: User, session: DB, timezone: str = Query("UTC", max_length=100)):
    _, result = load_selection(session, uuid.UUID(user.user_id), timezone)
    session.commit()
    return result


class Presentation(BaseModel):
    date: date
    timezone: str = Field(max_length=100)
    automatic: bool = True


class PresentationResult(BaseModel):
    claimed: bool


@router.post("/presentation", response_model=PresentationResult)
def present(payload: Presentation, user: User, session: DB):
    if selection_day(payload.timezone) != payload.date:
        raise HTTPException(409, "A new daily selection is available")
    snapshot, result = load_selection(session, uuid.UUID(user.user_id), payload.timezone)
    if result.date != payload.date:
        raise HTTPException(409, "A new daily selection is available")
    claimed = False
    if snapshot and result.items:
        claimed = (
            session.execute(
                update(UserMustRead)
                .where(
                    UserMustRead.user_id == uuid.UUID(user.user_id),
                    UserMustRead.selection_date == payload.date,
                    *([UserMustRead.presented_at.is_(None)] if payload.automatic else []),
                )
                .values(presented_at=func.coalesce(UserMustRead.presented_at, utcnow()))
                .returning(UserMustRead.user_id)
            ).scalar_one_or_none()
            is not None
        )
    session.commit()
    return {"claimed": claimed}
