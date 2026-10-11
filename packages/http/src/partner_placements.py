"""Strict context-only placement requests shared by public and personalized APIs."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlacementQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: Literal["article", "topic", "feed"]
    article_id: uuid.UUID | None = None
    article_ids: list[uuid.UUID] = Field(default_factory=list, max_length=24)
    allow_sponsored_fallback: bool = False
    topic_id: uuid.UUID | None = None
    exclude_product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_context(self):
        if (self.article_id is not None) != (self.surface == "article") or (
            self.topic_id is not None
        ) != (self.surface == "topic"):
            raise ValueError("Supply exactly the context required by the selected surface")
        if self.allow_sponsored_fallback and self.surface != "feed":
            raise ValueError("Sponsored fallback is only supported for the feed surface")
        if self.article_ids and self.surface != "feed":
            raise ValueError("Visible article context is only supported for the feed surface")
        return self


class PlacementImageVariant(BaseModel):
    url: str
    width: int


class PlacementOut(BaseModel):
    product_id: uuid.UUID
    asset_id: uuid.UUID
    campaign_id: uuid.UUID | None
    name: str
    description: str
    logo_url: str | None = None
    image_url: str | None = None
    image_variants: list[PlacementImageVariant] = Field(default_factory=list)
    url: str
    partner_name: str
    # Campaign backing for allocation/reporting; all reader placements must display
    # Sponsored and omit dismissal controls regardless of this value.
    sponsored: bool
    reason: str
    receipt: str


class PlacementsOut(BaseModel):
    items: list[PlacementOut]
