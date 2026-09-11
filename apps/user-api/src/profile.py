"""Personal presentation settings; sign-in names and email remain provider-owned."""

import uuid

from devfeed_core.models import UserAccount
from devfeed_core.user_settings import FeedSettings, NotificationSettings, ProfileSettings
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, update

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB
from devfeed_user_api.preferences import lock_account

router = APIRouter(prefix="/v1/user/settings", tags=["user-settings"])


@router.get("/profile", response_model=ProfileSettings)
def profile(user: User, session: DB):
    value = session.scalar(
        select(UserAccount.profile).where(
            UserAccount.id == uuid.UUID(user.user_id),
            UserAccount.issuer == user.issuer,
            UserAccount.subject == user.subject,
            UserAccount.organization_id == user.organization_id,
        )
    )
    if value is None:
        raise HTTPException(401, "User account unavailable")
    return ProfileSettings.model_validate(value)


@router.put("/profile", response_model=ProfileSettings)
def save_profile(payload: ProfileSettings, user: User, session: DB):
    account = lock_account(session, user)
    session.execute(
        update(UserAccount).where(UserAccount.id == account).values(profile=payload.model_dump())
    )
    session.commit()
    return payload


@router.get("/notifications", response_model=NotificationSettings)
def notification_settings(user: User, session: DB):
    value = session.scalar(
        select(UserAccount.notification_settings).where(
            UserAccount.id == uuid.UUID(user.user_id),
            UserAccount.issuer == user.issuer,
            UserAccount.subject == user.subject,
            UserAccount.organization_id == user.organization_id,
        )
    )
    if value is None:
        raise HTTPException(401, "User account unavailable")
    return NotificationSettings.model_validate(value)


@router.put("/notifications", response_model=NotificationSettings)
def save_notification_settings(payload: NotificationSettings, user: User, session: DB):
    account = lock_account(session, user)
    session.execute(
        update(UserAccount)
        .where(UserAccount.id == account)
        .values(notification_settings=payload.model_dump())
    )
    session.commit()
    return payload


@router.get("/feed", response_model=FeedSettings)
def feed_settings(user: User, session: DB):
    value = session.scalar(
        select(UserAccount.feed_settings).where(
            UserAccount.id == uuid.UUID(user.user_id),
            UserAccount.issuer == user.issuer,
            UserAccount.subject == user.subject,
            UserAccount.organization_id == user.organization_id,
        )
    )
    if value is None:
        raise HTTPException(401, "User account unavailable")
    return FeedSettings.model_validate(value)


@router.put("/feed", response_model=FeedSettings)
def save_feed_settings(payload: FeedSettings, user: User, session: DB):
    account = lock_account(session, user)
    session.execute(
        update(UserAccount)
        .where(UserAccount.id == account)
        .values(feed_settings=payload.model_dump())
    )
    session.commit()
    return payload
