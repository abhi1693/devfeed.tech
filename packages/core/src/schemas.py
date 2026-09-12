"""Shared input validation and record representations for the API and CLI."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from devfeed_core.config import get_settings
from devfeed_core.models import Article
from devfeed_core.source_types import SourceType
from devfeed_core.tag_names import normalize_tag_name
from devfeed_core.urls import validate_public_url

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=100)]
Keyword = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
TaxonomyName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]
TagName = Annotated[
    TaxonomyName, BeforeValidator(lambda v: normalize_tag_name(v) if isinstance(v, str) else v)
]
TopicKind = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=50)]
ContentType = Literal["article", "news", "tutorial", "release", "comparison", "opinion"]
ContentFormat = Literal["article", "podcast", "video", "paper", "discussion"]
ApprovalStatus = Literal["pending", "approved", "rejected"]
Language = Annotated[
    str, StringConstraints(pattern=r"^[a-z]{2,3}(?:-[a-z0-9]{2,8})*$", max_length=35)
]
Description = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)]
ReviewNote = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceSubmitter(InputModel):
    name: Name
    profile_url: str | None = Field(default=None, max_length=2048)

    @field_validator("profile_url")
    @classmethod
    def public_profile(cls, value):
        return validate_public_url(value) if value is not None else None


class SourceSubmitterOut(SourceSubmitter):
    verified: bool = False
    user_id: uuid.UUID | None = None


class SourceProfileInput(InputModel):
    description: Description | None = None
    website_url: str | None = Field(default=None, max_length=2048)
    logo_url: str | None = Field(default=None, max_length=2048)
    image_url: str | None = Field(default=None, max_length=2048)
    language: Language | None = None

    @field_validator("language", mode="before")
    @classmethod
    def normalize_language(cls, value):
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("website_url", "logo_url", "image_url")
    @classmethod
    def public_metadata_url(cls, value):
        return validate_public_url(value) if value is not None else None

    @field_validator("description")
    @classmethod
    def clean_description(cls, value):
        from devfeed_core.feeds.parser import plain_text

        if value is None:
            return None
        cleaned = plain_text(value, 500)
        if not cleaned:
            raise ValueError("Description must contain readable text")
        return cleaned


class SourceSubmission(SourceProfileInput):
    name: Name | None = None
    feed_url: str = Field(max_length=2048)
    source_type: SourceType
    submitted_by: SourceSubmitter | None = None

    _validate_url = field_validator("feed_url")(validate_public_url)

    @field_validator("name", mode="before")
    @classmethod
    def empty_name_is_missing(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value


class SourceCreate(SourceSubmission):
    """Trusted operator settings; HTTP submissions use SourceSubmission instead."""

    enabled: bool = True
    poll_interval_seconds: int = Field(default=1800, ge=300, le=604800)


class SourcePatch(SourceProfileInput):
    name: Name | None = None
    enabled: bool | None = None
    poll_interval_seconds: int | None = Field(default=None, ge=300, le=604800)

    @field_validator("name", "enabled", "poll_interval_seconds")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Omit a field to leave it unchanged; null is not supported")
        return value


class SourceDecision(InputModel):
    decision: Literal["approved", "rejected"]
    actor: Name | None = None
    note: ReviewNote | None = None


class SourceReviewOut(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID
    decision: str
    actor: str | None
    note: str | None
    created_at: datetime


class SourceEnrichmentJobOut(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    attempts: int
    available_at: datetime
    dispatched_at: datetime | None
    created_at: datetime
    finished_at: datetime | None
    error: str | None
    changed_fields: list[str]


class SourceRef(ORMModel):
    id: uuid.UUID
    name: str
    source_type: SourceType
    description: str | None = None
    website_url: str | None = None
    logo_url: str | None = None
    image_url: str | None = None
    language: str | None = None


class SourcePublicOut(SourceRef):
    created_at: datetime


class SourceSubmissionOut(SourceRef):
    feed_url: str
    approval_status: ApprovalStatus
    submitted_by: SourceSubmitterOut | None
    created_at: datetime


class ImageJobOut(ORMModel):
    id: uuid.UUID
    article_id: uuid.UUID
    status: str
    attempts: int
    created_at: datetime
    available_at: datetime
    dispatched_at: datetime | None
    finished_at: datetime | None
    http_status: int | None
    outcome: str | None
    image_url: str | None
    method: str | None
    error: str | None


class ArticleEnrichmentJobOut(ORMModel):
    id: uuid.UUID
    article_id: uuid.UUID
    status: str
    attempts: int
    created_at: datetime
    available_at: datetime
    dispatched_at: datetime | None
    finished_at: datetime | None
    http_status: int | None
    outcome: str | None
    changed_fields: list[str]
    result: dict
    error: str | None


class SourceOut(SourceRef):
    relevance_assessment: dict = Field(default_factory=dict)
    full_automation: bool = Field(default_factory=lambda: get_settings().full_automation)
    publication_policy: Literal["manual", "preview", "auto"] = "manual"
    publication_policy_revision: int = 0
    approval_status: ApprovalStatus
    submitted_by: SourceSubmitterOut | None
    submission_channel: str
    reviewed_at: datetime | None
    reviewed_by: str | None
    review_note: str | None
    metadata_enriched_at: datetime | None
    metadata_error: str | None
    updated_at: datetime
    feed_url: str
    enabled: bool
    poll_interval_seconds: int
    next_fetch_at: datetime
    last_attempt_at: datetime | None
    last_success_at: datetime | None
    consecutive_failures: int
    last_error: str | None
    created_at: datetime


class TagWrite(InputModel):
    name: TagName
    slug: Slug
    aliases: list[Keyword] = Field(default_factory=list, max_length=100)
    topic_id: uuid.UUID | None = None
    auto_link_topic: bool = True


class TagPatch(InputModel):
    name: TagName | None = None
    slug: Slug | None = None
    aliases: list[Keyword] | None = Field(default=None, max_length=100)
    topic_id: uuid.UUID | None = None
    auto_link_topic: bool | None = None

    @field_validator("name", "slug", "aliases", "auto_link_topic")
    @classmethod
    def reject_null(cls, value):
        if value is None:
            raise ValueError("Only topic_id can be null; omit other fields to leave them unchanged")
        return value


class TagRef(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    topic_id: uuid.UUID | None = None


class TagPublicOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    aliases: list[str]


class TagOut(TagRef):
    aliases: list[str]
    auto_link_topic: bool = True
    topic_match_status: str = "pending"
    topic_match_checked_at: datetime | None = None


class ArticleOriginOut(ORMModel):
    source_id: uuid.UUID
    original_url: str
    source_metadata: dict


class ArticleTopicOut(ORMModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: str
    role: str
    relevance: float


class ArticleOut(ORMModel):
    id: uuid.UUID
    slug: str
    canonical_url: str
    title: str
    summary: str
    ai_summary: str | None = None
    ai_description: str | None = None
    topics: list[ArticleTopicOut] = Field(default_factory=list)
    published_to_feed_at: datetime | None = None
    metadata_source_type: Literal["publisher", "aggregator", "page"] | None = None
    author: str | None
    image_url: str | None
    language: str | None = Field(
        description="Language of article text, from inference or operator classification; "
        "null for unresolved candidates. Source-declared language remains separate."
    )
    content_type: str
    content_format: str | None = None
    tags: list[str]
    published_at: datetime | None
    feed_at: datetime
    discovered_at: datetime
    sources: list[SourceRef]
    origins: list[ArticleOriginOut]

    @classmethod
    def from_article(cls, article: Article, *, public: bool = True):
        values = {
            key: getattr(article, key)
            for key in cls.model_fields
            if key not in {"sources", "origins", "tags", "topics"}
        }
        values["topics"] = [
            {
                "id": link.topic.id,
                "name": link.topic.name,
                "slug": link.topic.slug,
                "kind": link.topic.kind,
                "role": link.role,
                "relevance": link.relevance,
            }
            for link in sorted(
                article.topic_links, key=lambda item: (-item.relevance, item.topic.slug)
            )
            if link.topic.status == "active"
        ]
        values["tags"] = sorted(tag.slug for tag in article.tags)
        origins = [
            origin
            for origin in article.origins
            if not public or origin.source.approval_status == "approved"
        ]
        values["origins"] = origins
        sources = {origin.source.id: origin.source for origin in origins}
        values["sources"] = sorted(sources.values(), key=lambda source: source.name)
        return cls.model_validate(values)


class FeedPage(BaseModel):
    items: list[ArticleOut]
    next_cursor: str | None


class JobOut(ORMModel):
    id: uuid.UUID
    source_id: uuid.UUID
    status: str
    attempts: int
    created_at: datetime
    available_at: datetime
    dispatched_at: datetime | None
    finished_at: datetime | None
    http_status: int | None
    entries_seen: int
    articles_created: int
    entries_skipped: int
    error: str | None


class FeedOptionsOut(BaseModel):
    content_types: list[str]
    languages: list[str]
    sources: list[SourcePublicOut]
