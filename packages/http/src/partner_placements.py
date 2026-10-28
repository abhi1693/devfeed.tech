"""Strict context-only placement requests shared by public and personalized APIs."""

import uuid
from typing import Literal, Self, TypedDict

from devfeed_core.partner_ad_formats import AdFormat, default_ad_formats
from devfeed_core.partner_connection_logos import PlacementPlatform
from devfeed_core.partner_link_rel import LinkRelationships
from devfeed_core.partner_placement_types import PlacementData
from devfeed_core.partner_surfaces import PromotionSurface
from devfeed_core.partner_taglines import Tagline
from devfeed_core.schemas import ArticleOut
from devfeed_core.search_suggestions import normalize_query
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class PlacementsData(TypedDict):
    items: list[PlacementData]


SEARCH_PLACEMENT_RESULT_LIMIT = 120


class SearchPlacementContext(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=200)
    results: dict[Literal["articles", "topics", "sources", "tags"], list[uuid.UUID]]

    @field_validator("query")
    @classmethod
    def normalized_query(cls, value: str) -> str:
        return normalize_query(value, casefold=False)

    @model_validator(mode="after")
    def bounded_results(self) -> Self:
        count = sum(len(values) for values in self.results.values())
        if not 1 <= count <= SEARCH_PLACEMENT_RESULT_LIMIT:
            raise ValueError("Supply between one and 120 loaded search results")
        if any(len(values) != len(set(values)) for values in self.results.values()):
            raise ValueError("Search result identifiers must be unique within each section")
        return self


class PlacementQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    surface: PromotionSurface
    ad_formats: list[AdFormat] = Field(
        default_factory=default_ad_formats, min_length=1, max_length=4
    )
    article_id: uuid.UUID | None = None
    article_ids: list[uuid.UUID] = Field(default_factory=list, max_length=24)
    search: SearchPlacementContext | None = None
    allow_sponsored_fallback: bool = False
    topic_id: uuid.UUID | None = None
    exclude_product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def valid_context(self) -> Self:
        if self.search is not None and (
            self.surface != "search" or self.article_ids or "article_ids" in self.model_fields_set
        ):
            raise ValueError("Search result context is exclusive to the Search surface")
        if len(self.ad_formats) != len(set(self.ad_formats)):
            raise ValueError("Ad format selections must be unique")
        if self.surface == "search" and self.ad_formats != ["engagement"]:
            raise ValueError("Search supports only contextual Engagement placements")
        if self.surface == "header" and self.ad_formats != ["header_sponsorship"]:
            raise ValueError("Header supports only Header sponsorship placements")
        if "sponsored_content" in self.ad_formats and self.surface != "feed":
            raise ValueError("Sponsored content is only supported in feeds")
        if "header_sponsorship" in self.ad_formats and self.surface != "header":
            raise ValueError("Header sponsorship requires the header surface")
        if self.surface == "header":
            contexts = (
                self.article_id is not None,
                self.topic_id is not None,
                bool(self.article_ids),
            )
            if sum(contexts) > 1:
                raise ValueError("Supply at most one header context")
        elif (self.article_id is not None) != (self.surface == "article") or (
            self.topic_id is not None
        ) != (self.surface == "topic"):
            raise ValueError("Supply exactly the context required by the selected surface")
        if self.allow_sponsored_fallback and self.surface != "feed":
            raise ValueError("Sponsored fallback is only supported for the feed surface")
        if self.article_ids and self.surface not in {"feed", "search", "header"}:
            raise ValueError(
                "Visible article context is only supported for feed, search and header"
            )
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
    link_rel: LinkRelationships
    partner_name: str
    platform: PlacementPlatform | None = None
    attribution: str | None = Field(default=None, max_length=300)
    # Campaign backing for allocation/reporting; all reader placements must display
    # Sponsored and omit dismissal controls regardless of this value.
    sponsored: bool
    reason: str
    receipt: str


class PlacementsOut(BaseModel):
    items: list[PlacementOut]
