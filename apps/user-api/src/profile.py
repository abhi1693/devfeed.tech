"""Private profile settings and explicitly filtered public presentation."""

import uuid
from contextlib import nullcontext, suppress
from datetime import date, timedelta

from devfeed_core.cache import (
    CacheUnavailable,
    get_cache,
    invalidate_public_cache,
    record_cache_read,
)
from devfeed_core.config import get_settings
from devfeed_core.models import (
    Topic,
    UserAccount,
    UserLink,
    UserReadingDay,
    UserReadingStreak,
    UserStackAssociation,
    utcnow,
)
from devfeed_core.reading_streaks import reading_streak_value
from devfeed_core.recommendations import request_recommendation_refresh
from devfeed_core.topic_logos import logo_url, logo_variants
from devfeed_core.user_settings import (
    FeedSettings,
    NotificationSettings,
    ProfileVisibility,
    PublicUserProfile,
    UserAppearanceSettings,
    UserProfileSettings,
    UserProfileUpdate,
    UserReadingHeatmap,
)
from devfeed_core.usernames import normalize_username
from fastapi import APIRouter, HTTPException, Response
from sqlalchemy import delete, select, update

from devfeed_user_api.auth import User
from devfeed_user_api.avatar_storage import AvatarObjects
from devfeed_user_api.dependencies import DB
from devfeed_user_api.preferences import lock_account

router = APIRouter(prefix="/v1/user/settings", tags=["user-settings"])
public_router = APIRouter(prefix="/v1/user/profiles", tags=["user-profiles"])


def owned_account(session, user):
    account = session.scalar(
        select(UserAccount).where(
            UserAccount.id == uuid.UUID(user.user_id),
            UserAccount.issuer == user.issuer,
            UserAccount.subject == user.subject,
            UserAccount.organization_id == user.organization_id,
        )
    )
    if account is None:
        raise HTTPException(401, "User account unavailable")
    return account


def profile_value(session, account):
    stack = session.execute(
        select(UserStackAssociation, Topic)
        .join(Topic, Topic.id == UserStackAssociation.topic_id)
        .where(UserStackAssociation.user_id == account.id)
        .order_by(UserStackAssociation.position)
    ).all()
    return UserProfileSettings.model_validate(
        {
            **account.profile,
            "username": account.username,
            "about": account.about,
            "links": [
                {"url": link.url, "label": link.label}
                for link in session.scalars(
                    select(UserLink)
                    .where(UserLink.user_id == account.id)
                    .order_by(UserLink.position)
                )
            ],
            "stack": [
                {
                    "topic_id": item.topic_id,
                    "section": item.section,
                    "since_year": item.since_year,
                    "name": topic.name,
                    "slug": topic.slug,
                    "kind": topic.kind,
                    "logo_url": logo_url(topic),
                    "logo_variants": logo_variants(topic),
                    "status": topic.status,
                }
                for item, topic in stack
            ],
            "reading_streak": reading_streak_value(session.get(UserReadingStreak, account.id)),
        }
    )


@router.get("/profile", response_model=UserProfileSettings)
def profile(user: User, session: DB):
    return profile_value(session, owned_account(session, user))


def heatmap_value(session, user_id, year):
    today = utcnow().date()
    selected_year = today.year if year is None else year
    if not 2000 <= selected_year <= today.year:
        raise HTTPException(422, "Heatmap year must be between 2000 and the current UTC year")
    start, end = date(selected_year, 1, 1), date(selected_year + 1, 1, 1)
    counts = dict(
        session.execute(
            select(UserReadingDay.read_date, UserReadingDay.article_count)
            .where(
                UserReadingDay.user_id == user_id,
                UserReadingDay.read_date >= start,
                UserReadingDay.read_date < end,
            )
            .order_by(UserReadingDay.read_date)
        ).all()
    )
    return UserReadingHeatmap.model_validate(
        {
            "year": selected_year,
            "days": [
                {
                    "date": start + timedelta(days=i),
                    "article_count": counts.get(start + timedelta(days=i), 0),
                }
                for i in range((end - start).days)
            ],
        }
    )


@router.get("/reading-heatmap", response_model=UserReadingHeatmap)
def reading_heatmap(user: User, session: DB, year: int | None = None):
    return heatmap_value(session, owned_account(session, user).id, year)


@router.put("/profile", response_model=UserProfileSettings)
def save_profile(payload: UserProfileUpdate, user: User, session: DB):
    account_id = lock_account(session, user)
    account = session.get(UserAccount, account_id)
    assert account is not None
    if "username" in payload.model_fields_set:
        if account.username is not None and payload.username != account.username:
            raise HTTPException(409, "Usernames cannot be changed once claimed")
        account.username = payload.username
    if "about" in payload.model_fields_set:
        account.about = payload.about
    values = dict(account.profile)
    replace_managed_avatar = (
        bool(values.get("avatar_variants"))
        and "avatar_url" in payload.model_fields_set
        and payload.avatar_url != values.get("avatar_url")
    )
    if replace_managed_avatar:
        values["avatar_variants"] = []
    for field in ("display_name", "avatar_url", "bio", "location"):
        if field in payload.model_fields_set:
            values[field] = getattr(payload, field)
    if "dev_card" in payload.model_fields_set:
        values["dev_card"] = {
            **values.get("dev_card", {}),
            **payload.dev_card.model_dump(mode="json", exclude_unset=True),
        }
    if "visibility" in payload.model_fields_set:
        visibility = ProfileVisibility.model_validate(values.get("visibility", {}))
        values["visibility"] = {
            **visibility.model_dump(),
            **payload.visibility.model_dump(exclude_unset=True),
        }
    account.profile = values
    if "stack" in payload.model_fields_set:
        ids = [item.topic_id for item in payload.stack]
        # Lock topics against concurrent deletion. Previously selected retired topics
        # may be retained but cannot be newly added.
        topics = session.execute(
            select(Topic.id, Topic.status).where(Topic.id.in_(ids)).with_for_update(read=True)
        ).all()
        existing = set(
            session.scalars(
                select(UserStackAssociation.topic_id).where(
                    UserStackAssociation.user_id == account_id
                )
            )
        )
        allowed = {
            identifier
            for identifier, status in topics
            if status == "active" or identifier in existing
        }
        if allowed != set(ids):
            raise HTTPException(422, "New stack entries must reference active topics")
        session.execute(
            delete(UserStackAssociation).where(UserStackAssociation.user_id == account_id)
        )
        session.add_all(
            UserStackAssociation(user_id=account_id, position=i, **item.model_dump())
            for i, item in enumerate(payload.stack)
        )
    if "links" in payload.model_fields_set:
        session.execute(delete(UserLink).where(UserLink.user_id == account_id))
        session.add_all(
            UserLink(user_id=account_id, position=i, **link.model_dump())
            for i, link in enumerate(payload.links)
        )
    session.flush()
    result = profile_value(session, account)
    with AvatarObjects(account_id) if replace_managed_avatar else nullcontext() as objects:
        if objects is not None:
            objects.remove()
        session.commit()
    invalidate_public_cache(reasons=("profile",))
    return result


def public_account(session, username):
    try:
        canonical = normalize_username(username)
    except ValueError:
        raise HTTPException(404, "Profile not found") from None
    if canonical is None:
        raise HTTPException(404, "Profile not found")
    account = session.scalar(select(UserAccount).where(UserAccount.username == canonical))
    if account is None:
        raise HTTPException(404, "Profile not found")
    visibility = ProfileVisibility.model_validate(account.profile.get("visibility", {}))
    if not visibility.public:
        raise HTTPException(404, "Profile not found")
    return account


@public_router.get(
    "/{username}", response_model=PublicUserProfile, response_model_exclude_none=True
)
def public_profile(username: str, session: DB, response: Response):
    response.headers["Cache-Control"] = "no-store"
    # Check visibility even on cache hits. Browsers must never retain a card
    # independently of this check or the shared cache's invalidation generation.
    account = public_account(session, username)
    settings = get_settings()
    if not settings.cache_enabled:
        response.headers["X-Cache"] = "BYPASS"
        record_cache_read("public_profile", "bypass", "disabled")
        return public_profile_value(session, account)
    cache = get_cache()
    try:
        lookup = cache.lookup(f"public-profile:v2:{account.id}", "public", observe=False)
    except CacheUnavailable:
        response.headers["X-Cache"] = "BYPASS"
        record_cache_read("public_profile", "bypass", "cache_unavailable")
        return public_profile_value(session, account)
    try:
        if lookup.body is not None:
            with suppress(ValueError):
                value = PublicUserProfile.model_validate_json(lookup.body)
                response.headers["X-Cache"] = "HIT"
                record_cache_read("public_profile", "hit")
                return value
        # Re-read after acquiring the generation: a save between the initial
        # visibility query and lookup must not seed the new cache with old fields.
        session.expire(account)
        account = public_account(session, username)
        value = public_profile_value(session, account)
        with suppress(CacheUnavailable):
            cache.publish(
                lookup, value.model_dump_json().encode(), settings.cache_public_profile_ttl_seconds
            )
        response.headers["X-Cache"] = "MISS"
        record_cache_read("public_profile", "miss")
        return value
    finally:
        with suppress(CacheUnavailable):
            cache.release(lookup)


def public_profile_value(session, account):
    value = profile_value(session, account)
    return PublicUserProfile(
        username=account.username,
        display_name=value.display_name,
        avatar_url=value.avatar_url,
        avatar_variants=value.avatar_variants,
        bio=value.bio,
        about=value.about,
        links=value.links,
        location=value.location,
        stack=[item for item in value.stack if item.status == "active"],
        reading_streak=value.reading_streak,
        dev_card=value.dev_card,
    )


@public_router.get("/{username}/reading-heatmap", response_model=UserReadingHeatmap)
def public_heatmap(username: str, session: DB, response: Response, year: int | None = None):
    response.headers["Cache-Control"] = "no-store"
    account = public_account(session, username)
    return heatmap_value(session, account.id, year)


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
    previous = FeedSettings.model_validate(
        session.scalar(select(UserAccount.feed_settings).where(UserAccount.id == account))
    )
    session.execute(
        update(UserAccount)
        .where(UserAccount.id == account)
        .values(feed_settings=payload.model_dump())
    )
    if previous.languages != payload.languages:
        request_recommendation_refresh(session, account)
    session.commit()
    return payload


@router.get("/appearance", response_model=UserAppearanceSettings)
def appearance_settings(user: User, session: DB):
    value = session.scalar(
        select(UserAccount.appearance_settings).where(
            UserAccount.id == uuid.UUID(user.user_id),
            UserAccount.issuer == user.issuer,
            UserAccount.subject == user.subject,
            UserAccount.organization_id == user.organization_id,
        )
    )
    if value is None:
        raise HTTPException(401, "User account unavailable")
    return UserAppearanceSettings.model_validate(value)


@router.put("/appearance", response_model=UserAppearanceSettings)
def save_appearance_settings(payload: UserAppearanceSettings, user: User, session: DB):
    account = lock_account(session, user)
    session.execute(
        update(UserAccount)
        .where(UserAccount.id == account)
        .values(appearance_settings=payload.model_dump())
    )
    session.commit()
    return payload
