"""Private reader audience matching, independent of product capability filters."""

import uuid
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Literal, Self, TypedDict, get_args

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from devfeed_core.models import (
    PartnerAccount,
    PartnerCampaign,
    Topic,
    UserAccount,
    UserInterest,
    UserRecommendationState,
    UserStackAssociation,
    UserTopic,
)
from devfeed_core.partner_tiers import tier_policy


class AudienceTopicData(TypedDict):
    id: uuid.UUID
    name: str
    slug: str
    kind: str
    status: str


class AudienceTopicsData(TypedDict):
    items: list[AudienceTopicData]
    total: int


ExperienceLevel = Literal["aspiring", "entry_level", "mid_level", "senior"]
AudienceDimension = Literal["languages", "tools"]
TOOL_KINDS = (
    "tool",
    "platform",
    "framework",
    "library",
    "database",
    "runtime",
    "operating_system",
    "software",
    "service",
    "product",
    "package",
    "program",
    "technology",
)


class AudienceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    experience_levels: list[ExperienceLevel] = Field(default_factory=list, max_length=4)
    language_topic_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    tool_topic_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_selections(self) -> Self:
        for values in (self.experience_levels, self.language_topic_ids, self.tool_topic_ids):
            if len(values) != len(set(values)):
                raise ValueError("Audience selections must be unique")
        return self

    @property
    def populated(self) -> bool:
        return bool(self.experience_levels or self.language_topic_ids or self.tool_topic_ids)


def effective_audience(
    account: PartnerAccount, campaign: PartnerCampaign | None = None
) -> AudienceInput | None:
    raw = getattr(campaign, "audience", None) if campaign is not None else None
    if raw is None:
        raw = getattr(account, "audience", None)
        if raw is None:
            raw = {}
    try:
        return AudienceInput.model_validate(raw)
    except (ValidationError, TypeError):
        # Invalid stored policy must never widen its intended audience.
        return None


def audience_entitled(account: PartnerAccount, campaign: PartnerCampaign | None = None) -> bool:
    """Shared issuance/tracking tier fence, without retaining reader identity."""
    audience = effective_audience(account, campaign)
    return bool(
        audience is not None
        and (not audience.populated or tier_policy(account.tier)["advanced_targeting"])
    )


def audience_topics_current(
    session: Session, audience: AudienceInput, *, lock: bool = False
) -> bool:
    wanted = set(audience.language_topic_ids) | set(audience.tool_topic_ids)
    if not wanted:
        return True
    query = select(Topic.id, Topic.kind).where(Topic.id.in_(wanted), Topic.status == "active")
    if lock:
        query = query.with_for_update(read=True, skip_locked=True)
    kinds = dict(session.execute(query).all())
    return audience_topic_kinds_match(audience, kinds)


def audience_topic_kinds_match(audience: AudienceInput, kinds: dict[uuid.UUID, str]) -> bool:
    """Validate each selection against a current taxonomy snapshot, including missing IDs."""
    return all(
        kinds.get(identifier) == "language" for identifier in audience.language_topic_ids
    ) and all(kinds.get(identifier) in TOOL_KINDS for identifier in audience.tool_topic_ids)


def validate_audience(
    session: Session, tier: str, audience: AudienceInput, *, previous: object = None
) -> None:
    """Allow explicit clearing or unchanged retained policy, never a silent downgrade reset."""
    if not audience.populated:
        return
    previous_value = None
    if previous is not None:
        with suppress(ValidationError):
            previous_value = AudienceInput.model_validate(previous)
    if not tier_policy(tier)["advanced_targeting"]:
        if audience == previous_value:
            return
        raise ValueError("Advanced audience targeting requires Platinum or Diamond")
    if not audience_topics_current(session, audience, lock=True):
        raise ValueError("Audience topics must be active languages or supported developer tools")


@dataclass
class ReaderAudience:
    experience_level: str | None = None
    topic_ids: set[uuid.UUID] = field(default_factory=set)


def reader_audience(
    session: Session, user_id: uuid.UUID | str | None, now: datetime
) -> ReaderAudience | None:
    if user_id is None:
        return None
    # Preferences/profile writes share this account lock. A busy profile fails
    # closed for targeted ads; untargeted contextual inventory remains available.
    profile = session.scalar(
        select(UserAccount.profile)
        .where(UserAccount.id == user_id)
        .with_for_update(read=True, skip_locked=True)
    )
    if profile is None:
        return None
    level = profile.get("experience_level")
    result = ReaderAudience(level if level in get_args(ExperienceLevel) else None)
    followed = session.scalars(
        select(UserTopic.topic_id)
        .join(Topic)
        .where(UserTopic.user_id == user_id, Topic.status == "active")
        .limit(200)
        .with_for_update(read=True, of=[UserTopic, Topic], skip_locked=True)
    )
    result.topic_ids.update(followed)
    result.topic_ids.update(
        session.scalars(
            select(UserStackAssociation.topic_id)
            .join(Topic)
            .where(
                UserStackAssociation.user_id == user_id,
                UserStackAssociation.section.in_(["primary", "hobby", "learning"]),
                Topic.status == "active",
            )
            .limit(100)
            .with_for_update(read=True, of=[UserStackAssociation, Topic], skip_locked=True)
        )
    )
    result.topic_ids.update(
        session.scalars(
            select(UserInterest.topic_id)
            .join(UserRecommendationState, UserRecommendationState.user_id == UserInterest.user_id)
            .join(Topic, Topic.id == UserInterest.topic_id)
            .where(
                UserInterest.user_id == user_id,
                UserInterest.weight > 0,
                Topic.status == "active",
                UserRecommendationState.invalidated.is_(False),
                UserRecommendationState.computed_at >= now - timedelta(hours=6),
                UserRecommendationState.expires_at > now,
            )
            .order_by(UserInterest.weight.desc(), UserInterest.topic_id)
            .limit(200)
            .with_for_update(
                read=True, of=[UserInterest, UserRecommendationState, Topic], skip_locked=True
            )
        )
    )
    return result


def audience_matches(audience: AudienceInput, reader: ReaderAudience | None) -> bool:
    if not audience.populated:
        return True
    if reader is None:
        return False
    return (
        (not audience.experience_levels or reader.experience_level in audience.experience_levels)
        and (
            not audience.language_topic_ids
            or bool(set(audience.language_topic_ids) & reader.topic_ids)
        )
        and (not audience.tool_topic_ids or bool(set(audience.tool_topic_ids) & reader.topic_ids))
    )


def audience_topic_options(
    session: Session,
    dimension: AudienceDimension,
    *,
    account: PartnerAccount | None = None,
    q: str = "",
    topic_id: uuid.UUID | None = None,
    offset: int = 0,
    limit: int = 25,
) -> AudienceTopicsData:
    """Search active taxonomy; resolve saved unavailable selections without admitting them."""
    kinds = ("language",) if dimension == "languages" else TOOL_KINDS
    query = select(Topic).where(Topic.kind.in_(kinds), Topic.status == "active")
    if topic_id is not None:
        selected = False
        if account is not None:
            key = "language_topic_ids" if dimension == "languages" else "tool_topic_ids"
            criteria = effective_audience(account)
            selected = criteria is not None and topic_id in getattr(criteria, key)
            if not selected:
                selected = bool(
                    session.scalar(
                        select(
                            select(PartnerCampaign.id)
                            .where(
                                PartnerCampaign.account_id == account.id,
                                PartnerCampaign.audience[key].contains([str(topic_id)]),
                            )
                            .exists()
                        )
                    )
                )
        if selected:
            query = select(Topic)
        query = query.where(Topic.id == topic_id)
    elif q.strip():
        query = query.where(
            or_(
                Topic.name.icontains(q.strip(), autoescape=True),
                Topic.slug.icontains(q.strip(), autoescape=True),
            )
        )
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(
        query.order_by(func.lower(Topic.name), Topic.id).offset(offset).limit(limit)
    )
    return {
        "items": [
            AudienceTopicData(
                id=row.id, name=row.name, slug=row.slug, kind=row.kind, status=row.status
            )
            for row in rows
        ],
        "total": total or 0,
    }
