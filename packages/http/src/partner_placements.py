"""Strict context-only placement requests shared by public and personalized APIs."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlacementQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: Literal["article", "topic", "feed"]
    article_id: uuid.UUID | None = None
    topic_id: uuid.UUID | None = None
    exclude_product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_context(self):
        if (self.article_id is not None) != (self.surface == "article") or (
            self.topic_id is not None
        ) != (self.surface == "topic"):
            raise ValueError("Supply exactly the context required by the selected surface")
        return self


class PlacementOut(BaseModel):
    product_id: uuid.UUID
    asset_id: uuid.UUID
    campaign_id: uuid.UUID | None
    name: str
    description: str
    url: str
    partner_name: str
    sponsored: bool
    reason: str
    receipt: str
    frequency_cap: int


class PlacementsOut(BaseModel):
    items: list[PlacementOut]
