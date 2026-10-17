"""Authenticated source suggestions; approval is a separate, attributed decision."""

import uuid
from dataclasses import replace
from datetime import datetime
from typing import Literal

from devfeed_core import services
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.feeds.validation import FeedValidationError
from devfeed_core.models import Source
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from devfeed_core.schemas import InputModel, Name, SourceCreate, YouTubeTargetKind, source_location
from devfeed_core.source_enrichment import request_enrichment
from devfeed_core.source_types import SourceType
from devfeed_core.youtube_submission import ResourcePreview, provider_error, resource_choices
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, field_validator
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from devfeed_user_api.auth import User
from devfeed_user_api.dependencies import DB, get_redis

router = APIRouter(prefix="/v1/user/sources", tags=["source-suggestions"])
LIMIT = 5
WINDOW = 3600


class SourcePreviewRequest(InputModel):
    feed_url: str = Field(min_length=1, max_length=2048)
    source_type: SourceType
    youtube_target: YouTubeTargetKind | None = None

    @field_validator("feed_url", mode="before")
    @classmethod
    def trim_url(cls, value):
        return value.strip() if isinstance(value, str) else value

    _url = field_validator("feed_url")(source_location)


class SourceSuggestion(SourcePreviewRequest):
    name: Name | None = None


class SourcePreview(ResourcePreview):
    name: str


class SuggestionReceipt(BaseModel):
    id: uuid.UUID
    name: str
    approval_status: Literal["pending"] = "pending"
    created_at: datetime


def limit_requests(user_id: str, *, namespace: str, limit: int, window: int, message: str):
    try:
        retry_after = consume_rate_limits(
            get_redis(),
            [RateLimitBudget(f"devfeed:user:{namespace}:{user_id}", limit, window)],
        )
    except RedisError as exc:
        raise HTTPException(503, "Source suggestions are temporarily unavailable") from exc
    if retry_after:
        raise HTTPException(429, message, headers={"Retry-After": str(retry_after)})


def limit_suggestions(user_id: str):
    limit_requests(
        user_id,
        namespace="source-suggestions",
        limit=LIMIT,
        window=WINDOW,
        message="You can suggest up to five sources per hour. Please try again later.",
    )


def validated_source(body):
    try:
        return services.validate_source(SourceCreate(**body.model_dump(), enabled=True))
    except FeedValidationError as exc:
        message = (
            str(exc)
            if exc.reason in {"insufficient_entries", "no_recent_entries"}
            else "Use a reachable, valid public RSS or Atom feed URL."
        )
        raise HTTPException(422, message) from exc
    except FeedError as exc:
        code, message = provider_error(exc)
        raise HTTPException(code, message) from exc
    except ValueError as exc:
        raise HTTPException(
            422, "Use a valid public feed or supported YouTube resource URL and target."
        ) from exc


@router.post(
    "/suggestions/preview", response_model=SourcePreview, operation_id="user_source_preview"
)
def preview(body: SourcePreviewRequest, user: User):
    limit_requests(
        user.user_id,
        namespace="source-previews",
        limit=20,
        window=WINDOW,
        message="Too many feed lookups. Please try again later.",
    )
    source = validated_source(body)
    return SourcePreview(
        name=source.name,
        provider=source.provider,
        resource_kind=source.resource_kind,
        canonical_url=source.website_url if source.provider == "youtube" else source.feed_url,
        resources=resource_choices(body.feed_url),
    )


def reject_duplicate(session, url, validated=None):
    condition = (
        services.source_identity(validated) if validated is not None else Source.feed_url == url
    )
    if url is None and validated is None:
        return
    if session.scalar(select(Source.id).where(condition)) is not None:
        raise HTTPException(409, "This feed has already been added or suggested.")


@router.post(
    "/suggestions",
    response_model=SuggestionReceipt,
    status_code=201,
    operation_id="user_source_suggest",
)
def suggest(body: SourceSuggestion, user: User, session: DB):
    limit_suggestions(user.user_id)
    with session.begin():
        reject_duplicate(session, body.feed_url)
    validated = validated_source(body)
    validated = replace(
        validated,
        submitted_by={
            "name": (user.name or "DevFeed user")[:200],
            "user_id": user.user_id,
            "verified": True,
        },
    )
    try:
        reject_duplicate(session, validated.feed_url, validated)
        source = services.create_source(session, validated)
        # Profile/relevance work is safe for pending sources; ingestion is not.
        request_enrichment(session, source.id)
        receipt = SuggestionReceipt(id=source.id, name=source.name, created_at=source.created_at)
        session.commit()
    except IntegrityError:
        session.rollback()
        reject_duplicate(session, validated.feed_url, validated)
        raise
    return receipt
