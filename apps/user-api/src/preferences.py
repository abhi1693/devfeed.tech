"""User-owned topic preferences and uncached personalized discovery."""

import uuid
from typing import Annotated

from devfeed_core.models import Source, Topic, UserAccount, UserSource, UserTopic, utcnow
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB

router = APIRouter(prefix="/v1/user", tags=["personalization"])


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic_ids: Annotated[list[uuid.UUID], Field(max_length=100)]


class TopicFollow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    followed: bool


def lock_account(session, user: User) -> uuid.UUID:
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
    return account


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
    account = lock_account(session, user)
    active = set(
        session.scalars(select(Topic.id).where(Topic.id.in_(ids), Topic.status == "active"))
    )
    if active != set(ids):
        raise HTTPException(422, "Choose active topics")
    session.execute(
        delete(UserTopic).where(UserTopic.user_id == account, UserTopic.topic_id.not_in(ids))
    )
    if ids:
        session.execute(
            pg_insert(UserTopic).on_conflict_do_nothing(),
            [
                {"user_id": account, "topic_id": topic_id, "created_at": utcnow()}
                for topic_id in ids
            ],
        )
    session.commit()
    return Preferences(topic_ids=ids)


@router.put("/preferences/topics/{topic_id}", response_model=TopicFollow)
def follow_topic(topic_id: uuid.UUID, payload: TopicFollow, user: User, session: DB):
    # A single-topic mutation must not replace preferences loaded by another tab.
    account = lock_account(session, user)
    if payload.followed:
        if (
            session.scalar(select(Topic.id).where(Topic.id == topic_id, Topic.status == "active"))
            is None
        ):
            raise HTTPException(422, "Choose an active topic")
        count = session.execute(
            select(func.count())
            .select_from(UserTopic)
            .where(UserTopic.user_id == account, UserTopic.topic_id != topic_id)
        ).scalar_one()
        if count is not None and count >= 100:
            raise HTTPException(422, "You can follow up to 100 topics")
        session.execute(
            pg_insert(UserTopic).values(user_id=account, topic_id=topic_id).on_conflict_do_nothing()
        )
    else:
        session.execute(
            delete(UserTopic).where(UserTopic.user_id == account, UserTopic.topic_id == topic_id)
        )
    session.commit()
    return payload


class SourcePreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_ids: Annotated[list[uuid.UUID], Field(max_length=100)]


@router.get("/preferences/sources", response_model=SourcePreferences)
def source_preferences(user: User, session: DB):
    account = session.scalar(
        select(UserAccount.id).where(
            UserAccount.id == uuid.UUID(user.user_id),
            UserAccount.issuer == user.issuer,
            UserAccount.subject == user.subject,
            UserAccount.organization_id == user.organization_id,
        )
    )
    if account is None:
        raise HTTPException(401, "User account unavailable")
    ids = session.scalars(
        select(UserSource.source_id)
        .join(Source)
        .where(UserSource.user_id == account, Source.approval_status == "approved")
        .order_by(UserSource.source_id)
    ).all()
    return SourcePreferences(source_ids=list(ids))


@router.put("/preferences/sources", response_model=SourcePreferences)
def save_source_preferences(payload: SourcePreferences, user: User, session: DB):
    ids = sorted(set(payload.source_ids))
    account = lock_account(session, user)
    approved = set(
        session.scalars(
            select(Source.id).where(Source.id.in_(ids), Source.approval_status == "approved")
        )
    )
    if approved != set(ids):
        raise HTTPException(422, "Choose approved sources")
    session.execute(
        delete(UserSource).where(UserSource.user_id == account, UserSource.source_id.not_in(ids))
    )
    if ids:
        session.execute(
            pg_insert(UserSource).on_conflict_do_nothing(),
            [
                {"user_id": account, "source_id": source_id, "created_at": utcnow()}
                for source_id in ids
            ],
        )
    session.commit()
    return SourcePreferences(source_ids=ids)


@router.put("/preferences/sources/{source_id}", response_model=TopicFollow)
def follow_source(source_id: uuid.UUID, payload: TopicFollow, user: User, session: DB):
    account = lock_account(session, user)
    if payload.followed:
        if (
            session.scalar(
                select(Source.id).where(
                    Source.id == source_id, Source.approval_status == "approved"
                )
            )
            is None
        ):
            raise HTTPException(422, "Choose an approved source")
        count = session.scalar(
            select(func.count())
            .select_from(UserSource)
            .where(UserSource.user_id == account, UserSource.source_id != source_id)
        )
        if count is not None and count >= 100:
            raise HTTPException(422, "You can follow up to 100 sources")
        session.execute(
            pg_insert(UserSource)
            .values(user_id=account, source_id=source_id)
            .on_conflict_do_nothing()
        )
    else:
        session.execute(
            delete(UserSource).where(
                UserSource.user_id == account, UserSource.source_id == source_id
            )
        )
    session.commit()
    return payload
