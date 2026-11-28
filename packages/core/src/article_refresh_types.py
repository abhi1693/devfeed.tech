"""Allowlisted private maintenance inputs; these models are never public DTOs."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import Field, model_validator

from devfeed_core.analysis import AnalysisResult
from devfeed_core.schemas import ContentFormat, ContentType, DatabaseText, InputModel


class RefreshFields(InputModel):
    title: DatabaseText = Field(max_length=500)
    summary: DatabaseText = Field(max_length=60_000)
    author: DatabaseText | None = Field(default=None, max_length=200)
    published_at: datetime | None = None
    metadata_source_type: Literal["publisher", "aggregator", "page"] | None = None
    ai_title: DatabaseText | None = Field(default=None, max_length=200)
    ai_summary: DatabaseText | None = Field(default=None, max_length=6000)
    ai_description: DatabaseText | None = Field(default=None, max_length=2000)
    language: DatabaseText | None = Field(default=None, max_length=35)
    content_type: ContentType = "article"
    content_format: ContentFormat = "article"


class RefreshTopic(InputModel):
    topic_id: uuid.UUID
    role: Literal["primary", "supporting", "comparison", "incidental"]
    relevance: float = Field(ge=0, le=1)
    evidence: DatabaseText = Field(max_length=2000)
    origin: DatabaseText = Field(max_length=20)


class RefreshTag(InputModel):
    tag_id: uuid.UUID
    origin: DatabaseText = Field(max_length=20)


class RefreshContent(InputModel):
    text: DatabaseText = Field(max_length=60_000)
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    url: DatabaseText = Field(max_length=2048)
    method: DatabaseText = Field(max_length=30)
    retrieved_at: datetime


class RefreshVariant(InputModel):
    key: DatabaseText = Field(min_length=1, max_length=2048)
    width: int = Field(gt=0, le=40_000_000)


class RefreshImage(InputModel):
    source_url: DatabaseText = Field(min_length=1, max_length=2048)
    hash: DatabaseText = Field(min_length=1, max_length=128)
    original_key: DatabaseText = Field(min_length=1, max_length=2048)
    version: DatabaseText = Field(min_length=1, max_length=20)
    variants: list[RefreshVariant] = Field(max_length=32)
    source_width: int | None = Field(default=None, gt=0, le=40_000_000)
    original_bytes: int | None = Field(default=None, ge=0)
    thumbnail_version: DatabaseText | None = Field(default=None, max_length=20)
    thumbnail_layout: DatabaseText | None = Field(default=None, max_length=100)
    subject_revision: int | None = Field(default=None, ge=0)
    page_url: DatabaseText | None = Field(default=None, max_length=2048)
    page_final_url: DatabaseText | None = Field(default=None, max_length=2048)
    discovery_method: DatabaseText | None = Field(default=None, max_length=100)
    icon_url: DatabaseText | None = Field(default=None, max_length=2048)


class RefreshDraft(InputModel):
    protocol: Literal[1] = 1
    fields: RefreshFields
    topics: list[RefreshTopic] = Field(max_length=100)
    tags: list[RefreshTag] = Field(max_length=200)
    content: RefreshContent | None = None
    analysis: AnalysisResult | None = None

    @model_validator(mode="after")
    def bounded(self) -> Self:
        if len(self.model_dump_json().encode()) > 1_048_576:
            raise ValueError("Maintenance inputs exceed the bounded private payload")
        return self
