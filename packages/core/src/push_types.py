"""Web Push message contracts and explicit, consent-scoped notification types."""

import json
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    JsonValue,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import select
from sqlalchemy.orm import Session

from devfeed_core.models import (
    Article,
    DailyMustReadPush,
    UserAccount,
    UserMustRead,
    UserRecommendation,
    WebPushEvent,
)
from devfeed_core.must_reads import read_snapshot, recommendation_eligibility
from devfeed_core.push_audience import PushAudience
from devfeed_core.user_settings import FeedSettings


class WebPushMessage(BaseModel):
    """Plain text, a local action and private type-specific server context."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    kind: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,99}$")]
    title: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
    action_url: Annotated[str, StringConstraints(pattern=r"^/[a-zA-Z0-9/_-]+$", max_length=500)]
    context: dict[str, JsonValue]

    @field_validator("action_url")
    @classmethod
    def local_action(cls, value):
        if value.startswith("//"):
            raise ValueError("Browser notification actions must stay inside the reader")
        return value

    @model_validator(mode="after")
    def bounded_wire_content(self):
        content = {"title": self.title, "body": self.body, "url": self.action_url}
        if len(json.dumps(content, ensure_ascii=False).encode()) > 2800:
            raise ValueError("Browser notification content exceeds the payload limit")
        return self


@dataclass(frozen=True)
class PushType:
    """Adding a type requires publication rules, recipient rules and new consent."""

    validate: Callable[[WebPushMessage, PushAudience], None]
    prepare: Callable[[Session, WebPushEvent, uuid.UUID, datetime], WebPushMessage | None]


class DailyContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: uuid.UUID
    user_id: uuid.UUID


def _validate_daily(message: WebPushMessage, audience: PushAudience):
    context = DailyContext.model_validate(message.context)
    if audience.kind != "users" or audience.user_ids != (context.user_id,):
        raise ValueError("Personalized Must Reads require their claimed account's audience")
    if not re.fullmatch(r"/articles/[^/]+", message.action_url):
        raise ValueError("Must Read notifications must open their article")
    if message.body != "Your personalized must-read for today.":
        raise ValueError("Must Read consent does not authorize custom messages")


def _prepare_daily(session, event, user_id, now):
    message = WebPushMessage.model_validate(event.payload)
    context = DailyContext.model_validate(message.context)
    if context.user_id != user_id:
        return None
    claim = session.scalar(
        select(DailyMustReadPush).where(
            DailyMustReadPush.id == context.claim_id,
            DailyMustReadPush.event_id == event.id,
            DailyMustReadPush.user_id == user_id,
        )
    )
    if claim is None or claim.expires_at <= now or claim.expires_at != event.expires_at:
        return None
    account = session.get(UserAccount, user_id)
    if account is None:
        return None
    snapshot = session.get(UserMustRead, (user_id, claim.selection_date))
    articles, _, read_ids = read_snapshot(
        session,
        snapshot,
        FeedSettings.model_validate(account.feed_settings),
        include_details=False,
    )
    article = next((item for item in articles if item.id == claim.article_id), None)
    eligible = session.scalar(
        select(Article.id)
        .join(UserRecommendation, UserRecommendation.article_id == Article.id)
        .where(
            Article.id == claim.article_id,
            UserRecommendation.user_id == user_id,
            recommendation_eligibility(user_id),
        )
    )
    if (
        article is None
        or claim.article_id in read_ids
        or eligible is None
        or message.title != claim.title
        or message.action_url != f"/articles/{article.slug}"
    ):
        return None
    return message


# Deliberately register only the existing product behavior. An additional type
# also needs its own browser receiver and an explicit per-kind consent flow.
PUSH_TYPES: dict[str, PushType] = {
    "daily_must_read": PushType(validate=_validate_daily, prepare=_prepare_daily)
}


def get_push_type(kind: str) -> PushType:
    try:
        return PUSH_TYPES[kind]
    except KeyError as exc:
        raise ValueError("Unsupported browser notification type") from exc
