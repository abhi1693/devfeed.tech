"""Strict context-only placement requests shared by public and personalized APIs."""

import uuid

from devfeed_core.partner_ad_formats import AdFormat, default_ad_formats
from devfeed_core.partner_surfaces import PromotionSurface
from devfeed_core.partner_taglines import Tagline
from devfeed_core.schemas import ArticleOut
from pydantic import BaseModel, ConfigDict, Field, model_validator


class PlacementQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: PromotionSurface
    ad_formats: list[AdFormat] = Field(
        default_factory=default_ad_formats, min_length=1, max_length=4
    )
    article_id: uuid.UUID | None = None
    article_ids: list[uuid.UUID] = Field(default_factory=list, max_length=24)
    allow_sponsored_fallback: bool = False
    topic_id: uuid.UUID | None = None
    exclude_product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_context(self):
        if len(self.ad_formats) != len(set(self.ad_formats)):
            raise ValueError("Ad format selections must be unique")
        if self.surface == "search" and self.ad_formats != ["engagement"]:
            raise ValueError("Search supports only contextual Engagement placements")
        if "sponsored_content" in self.ad_formats and self.surface != "feed":
            raise ValueError("Sponsored content is only supported in feeds")
        if "homepage_sponsorship" in self.ad_formats and (
            self.surface != "feed" or not self.allow_sponsored_fallback
        ):
            raise ValueError("Homepage sponsorship requires the unfiltered main feed")
        if (self.article_id is not None) != (self.surface == "article") or (
            self.topic_id is not None
        ) != (self.surface == "topic"):
            raise ValueError("Supply exactly the context required by the selected surface")
        if self.allow_sponsored_fallback and self.surface != "feed":
            raise ValueError("Sponsored fallback is only supported for the feed surface")
        if self.article_ids and self.surface not in {"feed", "search"}:
            raise ValueError("Visible article context is only supported for feed and search")
        return self


class PlacementImageVariant(BaseModel):
    url: str
    width: int


class PlacementOut(BaseModel):
    product_id: uuid.UUID
    ad_format: AdFormat = "feed"
    article: ArticleOut | None = None
    asset_id: uuid.UUID
    campaign_id: uuid.UUID | None
    name: str
    tagline: Tagline
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
