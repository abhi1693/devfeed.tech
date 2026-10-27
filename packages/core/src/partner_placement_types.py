"""Structural request and delivery contracts shared by placement producers and APIs."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Literal, Protocol, TypedDict

from devfeed_core.managed_images import ImageVariant
from devfeed_core.partner_connection_logos import PlacementPlatform
from devfeed_core.partner_link_rel import LinkRel
from devfeed_core.schemas import ArticleOut

type SearchResultKind = Literal["articles", "topics", "sources", "tags"]


class PlacementSearch(Protocol):
    @property
    def query(self) -> str: ...

    @property
    def results(self) -> Mapping[SearchResultKind, Sequence[uuid.UUID]]: ...


class PlacementRequest(Protocol):
    @property
    def surface(self) -> str: ...

    @property
    def ad_formats(self) -> Sequence[str]: ...

    @property
    def article_id(self) -> uuid.UUID | None: ...

    @property
    def article_ids(self) -> Sequence[uuid.UUID]: ...

    @property
    def topic_id(self) -> uuid.UUID | None: ...

    @property
    def exclude_product_ids(self) -> Sequence[uuid.UUID]: ...

    @property
    def allow_sponsored_fallback(self) -> bool: ...

    @property
    def search(self) -> PlacementSearch | None: ...


class PlacementData(TypedDict):
    product_id: uuid.UUID
    asset_id: uuid.UUID
    campaign_id: uuid.UUID | None
    name: str
    tagline: str
    logo_url: str | None
    image_url: str | None
    image_variants: list[ImageVariant]
    link_rel: list[LinkRel]
    url: str
    partner_name: str
    platform: PlacementPlatform | None
    attribution: str | None
    sponsored: bool
    ad_format: str
    article: ArticleOut | None
    reason: str
    receipt: str
