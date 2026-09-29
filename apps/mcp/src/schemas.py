"""Explicit public projections keep image variants and operational fields out of tool results."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import AliasPath, BaseModel, Field

ContentType = Literal["article", "news", "tutorial", "release", "comparison", "opinion"]
SearchKind = Literal["articles", "topics", "sources", "tags"]


class Source(BaseModel):
    id: UUID
    slug: str
    name: str
    source_type: Literal["publisher", "aggregator"]
    description: str | None = None
    website_url: str | None = None
    language: str | None = None


class Topic(BaseModel):
    id: UUID
    slug: str
    name: str
    kind: str
    description: str | None = None


class ArticleOrigin(BaseModel):
    source_id: UUID
    original_url: str
    discussion_url: str | None = Field(
        default=None, validation_alias=AliasPath("source_metadata", "discussion_url")
    )


class Article(BaseModel):
    id: UUID
    slug: str
    title: str
    canonical_url: str
    summary: str
    ai_summary: str | None = None
    author: str | None = None
    published_at: datetime | None
    feed_at: datetime
    language: str | None
    content_type: str
    topics: list[Topic]
    tags: list[str]
    sources: list[Source]
    origins: list[ArticleOrigin]
    # Deliberately do not imply that previews are full publisher article text.
    content_scope: Literal["metadata_and_preview"] = "metadata_and_preview"


class FeedPage(BaseModel):
    items: list[Article]
    next_cursor: str | None


class TopicPage(BaseModel):
    items: list[Topic]
    next_offset: int | None


class SourcePage(BaseModel):
    items: list[Source]
    next_offset: int | None


class SearchHit(BaseModel):
    id: UUID
    title: str
    description: str
    href: str = Field(description="DevFeed-relative result path; use the ID for get_article.")
    label: str
    published_at: str | None


class SearchSection(BaseModel):
    items: list[SearchHit]
    next_cursor: str | None = Field(description="Next page number, passed as the search page.")


class SearchResults(BaseModel):
    query: str
    sections: dict[SearchKind, SearchSection]
