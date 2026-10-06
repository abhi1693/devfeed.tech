"""Explicit Web Push recipients; matching never grants notification consent."""

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import Select, exists, select
from sqlalchemy.orm import Session

from devfeed_core.models import (
    Source,
    Topic,
    UserAccount,
    UserSource,
    UserTopic,
    WebPushSubscription,
)

MAX_AUDIENCE_IDS = 1000


class PushAudience(BaseModel):
    """One or more accounts, all accounts, or followers of any listed interest."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: Literal["users", "all", "segment"]
    user_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=MAX_AUDIENCE_IDS)
    topic_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=MAX_AUDIENCE_IDS)
    source_ids: tuple[uuid.UUID, ...] = Field(default=(), max_length=MAX_AUDIENCE_IDS)

    @field_validator("user_ids", "topic_ids", "source_ids")
    @classmethod
    def unique_ids(cls, value):
        # A canonical order gives semantically identical requests identical storage.
        return tuple(sorted(set(value)))

    @model_validator(mode="after")
    def valid_audience(self):
        if self.kind == "users":
            if not self.user_ids or self.topic_ids or self.source_ids:
                raise ValueError("User audiences require only one or more account IDs")
        elif self.kind == "all":
            if self.user_ids or self.topic_ids or self.source_ids:
                raise ValueError("All-user audiences cannot contain recipient filters")
        elif self.user_ids or not (self.topic_ids or self.source_ids):
            raise ValueError("Segments require followed topics or sources and no account IDs")
        return self

    @classmethod
    def users(cls, *user_ids: uuid.UUID) -> Self:
        return cls(kind="users", user_ids=user_ids)

    @classmethod
    def all(cls) -> Self:
        return cls(kind="all")

    @classmethod
    def segment(
        cls,
        *,
        topic_ids: tuple[uuid.UUID, ...] | list[uuid.UUID] = (),
        source_ids: tuple[uuid.UUID, ...] | list[uuid.UUID] = (),
    ) -> Self:
        return cls(kind="segment", topic_ids=tuple(topic_ids), source_ids=tuple(source_ids))


def _audience_accounts(audience: PushAudience, created_at: datetime) -> Select[tuple[uuid.UUID]]:
    """Apply immutable recipient filters against current membership and catalog state."""
    accounts = select(UserAccount.id).where(UserAccount.created_at <= created_at)
    if audience.kind == "users":
        return accounts.where(UserAccount.id.in_(audience.user_ids))
    if audience.kind == "all":
        return accounts

    memberships = []
    if audience.topic_ids:
        memberships.append(
            select(UserTopic.user_id.label("user_id"))
            .join(Topic, Topic.id == UserTopic.topic_id)
            .where(
                UserTopic.topic_id.in_(audience.topic_ids),
                UserTopic.created_at <= created_at,
                Topic.status == "active",
            )
        )
    if audience.source_ids:
        memberships.append(
            select(UserSource.user_id.label("user_id"))
            .join(Source, Source.id == UserSource.source_id)
            .where(
                UserSource.source_id.in_(audience.source_ids),
                UserSource.created_at <= created_at,
                Source.approval_status == "approved",
            )
        )
    # Keep construction without model validation fail-closed too. UNION combines
    # topic and source matches with OR and prevents a recipient appearing twice.
    if not memberships:
        raise ValueError("Push audience segment requires a topic or source criterion")
    matching = memberships[0].union(*memberships[1:]) if len(memberships) > 1 else memberships[0]
    recipients = matching.subquery()
    return accounts.where(UserAccount.id.in_(select(recipients.c.user_id)))


def web_push_recipient_ids(
    audience: PushAudience,
    kind: str,
    created_at: datetime,
    now: datetime,
    *,
    after: uuid.UUID | None = None,
    batch: int = 100,
) -> Select[tuple[uuid.UUID]]:
    """A bounded UUID page of accounts with existing consent for this specific kind."""
    if not 1 <= batch <= MAX_AUDIENCE_IDS:
        raise ValueError("Web Push recipient batches must contain between 1 and 1000 accounts")
    recipients = _audience_accounts(audience, created_at).where(
        exists(
            select(WebPushSubscription.id).where(
                WebPushSubscription.user_id == UserAccount.id,
                WebPushSubscription.enabled.is_(True),
                WebPushSubscription.authorization_expires_at > now,
                WebPushSubscription.created_at <= created_at,
                WebPushSubscription.allowed_kinds.contains([kind]),
            )
        )
    )
    if after is not None:
        recipients = recipients.where(UserAccount.id > after)
    return recipients.order_by(UserAccount.id).limit(batch)


def audience_contains(
    session: Session, audience: PushAudience, user_id: uuid.UUID, created_at: datetime
) -> bool:
    """Recheck current audience membership; transport checks browser consent separately."""
    return (
        session.scalar(_audience_accounts(audience, created_at).where(UserAccount.id == user_id))
        is not None
    )
