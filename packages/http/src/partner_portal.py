"""Shared schemas and reporting queries for partner and administrator services."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal, Self, TypedDict

from devfeed_core.cache_events import mark_dirty
from devfeed_core.config import get_settings
from devfeed_core.json_types import JsonRecord
from devfeed_core.models import (
    Article,
    ArticleImageJob,
    ArticleTopic,
    PartnerAccount,
    PartnerAsset,
    PartnerManualProduct,
    PartnerProduct,
    PartnerProductPromotion,
    Topic,
)
from devfeed_core.partner_ad_formats import (
    AUTOMATIC_AD_FORMATS,
    AdFormat,
    default_ad_formats,
    validate_product_formats,
)
from devfeed_core.partner_audiences import AudienceInput
from devfeed_core.partner_connection_logos import PlacementPlatform
from devfeed_core.partner_link_rel import (
    LINK_REL_OPTIONS,
    LinkRel,
    LinkRelationships,
    default_link_rel,
    effective_link_rel,
    validate_link_rel,
)
from devfeed_core.partner_placement_destinations import (
    account_product_creative,
    devfeed_outbound_url,
)
from devfeed_core.partner_product_images import (
    product_ad_image_current,
    product_ad_image_status,
    product_image_url,
    request_product_ad_image,
)
from devfeed_core.partner_product_logos import product_logo_url
from devfeed_core.partner_promotions import (
    account_product_source,
    associated_products_query,
    ensure_automatic_assets,
)
from devfeed_core.partner_surfaces import (
    PROMOTION_SURFACES,
    PromotionSurface,
    default_campaign_surfaces,
    default_promotion_surfaces,
)
from devfeed_core.partner_taglines import Tagline
from devfeed_core.partner_tiers import PartnerTier, PromotionKind, tier_policy
from devfeed_core.partner_tools import ProductAssessment, eligible
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut
from devfeed_core.urls import validate_public_url
from fastapi import HTTPException
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)
from sqlalchemy import (
    and_,
    case,
    func,
    literal,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql import Select

from devfeed_http.partner_analytics_reporting import MetricTotals


class InventoryProductData(TypedDict):
    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    name: str
    technologies: list[str]
    status: str
    excluded: bool
    globally_excluded: bool
    commercial_approved: bool
    approval_source: Literal["qualified", "trusted"] | None
    ad_formats: list[str] | None
    link_rel: list[str] | None
    effective_link_rel: list[LinkRel]
    configurable_link_rel: bool
    link_rel_options: list[LinkRel]
    promotion_surfaces: list[str] | None
    ad_image_url: str | None
    product_url: str
    tagline: str
    pricing: str
    assessment: JsonRecord
    revision: int
    source: Literal["manual", "imported"]
    editable: bool
    active: bool
    manual_active: bool | None


class InventoryPageData(TypedDict):
    items: list[InventoryProductData]
    total: int
    limit: int
    offset: int


class AccountsData(TypedDict):
    items: Sequence[PartnerAccount]
    total: int


class SponsorArticlesData(TypedDict):
    items: list[ArticleOut]
    total: int


Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

AssetSort = Literal["name", "impressions", "clicks", "ctr"]
WorkspaceAssetSort = Literal["name", "account", "impressions", "clicks", "ctr"]
SortOrder = Literal["asc", "desc"]
AdImageStatus = Literal["default", "queued", "running", "ready", "failed"]


MemberSubject = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$"
    ),
]


class PartnershipTierOut(BaseModel):
    tier: PartnerTier
    benefits: list[str]
    allowed_promotion_kinds: list[PromotionKind]
    default_promotion_kind: PromotionKind
    advanced_targeting: bool
    allowed_ad_formats: list[AdFormat]
    configurable_link_rel: bool
    link_rel_options: list[LinkRel]


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    tier: PartnerTier
    status: Literal["active", "paused"] = "active"
    organic_enabled: bool = True
    auto_include_products: bool = True
    promotion_surfaces: list[PromotionSurface] = Field(
        default_factory=default_promotion_surfaces, min_length=1, max_length=len(PROMOTION_SURFACES)
    )
    promotion_kind: PromotionKind | None = None
    approval_mode: Literal["qualified", "trusted"] = "qualified"
    outbound_destination: Literal["product_url", "listing_url"] = "product_url"
    audience: AudienceInput = Field(default_factory=AudienceInput)
    ad_formats: list[AdFormat] = Field(default_factory=default_ad_formats, max_length=3)
    link_rel: LinkRelationships = Field(default_factory=default_link_rel)

    @model_validator(mode="after")
    def unique_surfaces(self) -> Self:
        if len(self.ad_formats) != len(set(self.ad_formats)) or not set(self.ad_formats).issubset(
            AUTOMATIC_AD_FORMATS
        ):
            raise ValueError(
                "Choose unique automatic formats; sponsored content requires a campaign"
            )
        if (
            self.promotion_kind is not None
            and self.promotion_kind not in tier_policy(self.tier)["allowed_promotion_kinds"]
        ):
            raise ValueError("Promotion type is not included in this partnership tier")
        if len(self.promotion_surfaces) != len(set(self.promotion_surfaces)):
            raise ValueError("Promotion surfaces must be unique")
        return self


class AccountOut(AccountInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    benefits: list[str]
    allowed_promotion_kinds: list[PromotionKind]
    effective_promotion_kind: PromotionKind
    promotion_kind_source: Literal["tier", "account"]
    advanced_targeting: bool
    allowed_ad_formats: list[AdFormat]
    configurable_link_rel: bool
    link_rel_options: list[LinkRel]


class PartnerPreferencesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outbound_destination: Literal["product_url", "listing_url"] | None = None
    audience: AudienceInput | None = None
    ad_formats: list[AdFormat] | None = Field(default=None, max_length=3)
    link_rel: LinkRelationships | None = None

    @model_validator(mode="after")
    def supplied_preference(self) -> Self:
        if (
            self.outbound_destination is None
            and self.audience is None
            and self.ad_formats is None
            and self.link_rel is None
        ):
            raise ValueError("Supply at least one partner preference")
        if "link_rel" in self.model_fields_set and self.link_rel is None:
            raise ValueError("Account link relationships must be a selection")
        return self


class AudienceTopicOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    kind: str
    status: str


class AudienceTopicsOut(BaseModel):
    items: list[AudienceTopicOut]
    total: int


class DestinationPreviewProduct(BaseModel):
    id: uuid.UUID
    name: str
    product_url: str
    listing_url: str | None


class DestinationPreviewOut(BaseModel):
    product: DestinationPreviewProduct | None


class CreativePreviewProduct(DestinationPreviewProduct):
    ad_image_status: AdImageStatus
    tagline: Tagline
    logo_url: str | None
    image_url: str | None
    platform: PlacementPlatform | None = None
    link_rel: list[LinkRel]


class CreativePreviewOut(BaseModel):
    product: CreativePreviewProduct | None


class SponsorArticlesOut(BaseModel):
    items: list[ArticleOut]
    total: int


def _preview_product(
    session: Session, account: PartnerAccount, product_id: uuid.UUID | None = None
) -> tuple[PartnerProduct, str] | None:
    """Read actual accepted inventory without issuing a placement or reserving capacity."""
    qualified = and_(
        PartnerProduct.status == "approved",
        PartnerProduct.verified_at >= datetime.now(UTC) - timedelta(days=90),
    )
    accepted = qualified
    if account.approval_mode == "trusted":
        accepted = or_(
            qualified,
            and_(
                PartnerProductPromotion.commercial_approved_at.is_not(None),
                PartnerProductPromotion.approval_source == "trusted",
            ),
        )
    products = session.scalars(
        select(PartnerProduct)
        .outerjoin(
            PartnerProductPromotion,
            and_(
                PartnerProductPromotion.account_id == account.id,
                PartnerProductPromotion.product_id == PartnerProduct.id,
            ),
        )
        .where(
            PartnerProduct.id.in_(
                associated_products_query(account.id).with_only_columns(PartnerProduct.id)
            ),
            PartnerProduct.excluded.is_(False),
            or_(
                PartnerProductPromotion.product_id.is_(None),
                PartnerProductPromotion.excluded.is_(False),
            ),
            accepted,
            *([PartnerProduct.id == product_id] if product_id is not None else []),
        )
        .order_by(case((qualified, 0), else_=1), func.lower(PartnerProduct.name), PartnerProduct.id)
        .limit(100)
    )
    for product in products:
        product_url = devfeed_outbound_url(product.product_url)
        if product_url is not None:
            return product, product_url
    return None


def destination_preview(session: Session, account: PartnerAccount) -> DestinationPreviewOut:
    selected = _preview_product(session, account)
    if selected is None:
        return DestinationPreviewOut(product=None)
    product, product_url = selected
    creative = account_product_creative(session, account, product)
    if creative is None:
        return DestinationPreviewOut(product=None)
    return DestinationPreviewOut(
        product=DestinationPreviewProduct(
            id=product.id,
            name=product.name,
            product_url=product_url,
            listing_url=creative.listing_url,
        )
    )


def creative_preview(
    session: Session, account: PartnerAccount, product_id: uuid.UUID | None = None
) -> CreativePreviewOut:
    selected = _preview_product(session, account, product_id)
    if selected is None:
        return CreativePreviewOut(product=None)
    product, product_url = selected
    creative = account_product_creative(session, account, product)
    if creative is None:
        return CreativePreviewOut(product=None)
    policy = session.get(PartnerProductPromotion, (account.id, product.id))
    return CreativePreviewOut(
        product=CreativePreviewProduct(
            id=product.id,
            name=product.name,
            tagline=creative.tagline,
            product_url=product_url,
            listing_url=creative.listing_url,
            logo_url=product_logo_url(product),
            platform=creative.platform,
            image_url=product_image_url(product, policy),
            ad_image_status=product_ad_image_status(session, policy),
            link_rel=effective_link_rel(account, policy),
        )
    )


class AccountsOut(BaseModel):
    items: list[AccountOut]
    total: int


class MemberInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: uuid.UUID


class MemberOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    issuer: str
    subject: str
    user_id: uuid.UUID | None = None
    name: str | None = None
    email: str | None = None


class AssetInput(BaseModel):
    name: Name
    kind: Literal["product", "ad"]
    product_id: uuid.UUID | None = None
    status: Literal["draft", "active", "paused", "ended"] = "draft"

    @model_validator(mode="after")
    def product_required(self) -> Self:
        if self.kind == "product" and self.product_id is None:
            raise ValueError("Product placements require a catalog product")
        return self


class AssetOut(AssetInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    automatic: bool = False
    campaign_id: uuid.UUID | None = None
    ad_format: AdFormat = "feed"
    article_id: uuid.UUID | None = None


class CampaignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    status: Literal["draft", "active", "paused", "ended"] = "draft"
    targeting: Literal["all", "selected", "filters"] = "all"
    audience: AudienceInput | None = None
    ad_format: AdFormat = "feed"
    article_id: uuid.UUID | None = None
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    topic_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    technologies: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    ] = Field(default_factory=list, max_length=100)
    surfaces: list[PromotionSurface] = Field(
        default_factory=default_promotion_surfaces, min_length=1, max_length=len(PROMOTION_SURFACES)
    )
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    priority: int = Field(default=50, ge=1, le=100)
    daily_impression_limit: int | None = Field(default=None, ge=1, le=2**63 - 1)

    @model_validator(mode="after")
    def valid_agreement(self) -> Self:
        if (self.ad_format == "sponsored_content") != (self.article_id is not None):
            raise ValueError("Supply an article only for sponsored content campaigns")
        if "surfaces" not in self.model_fields_set:
            self.surfaces = default_campaign_surfaces(self.ad_format)
        if self.ad_format != "engagement" and "search" in self.surfaces:
            raise ValueError("Search placements require the Engagement ad format")
        if self.ad_format == "header_sponsorship" and self.surfaces != ["header"]:
            raise ValueError("Header sponsorship campaigns require the header surface")
        if self.ad_format != "header_sponsorship" and "header" in self.surfaces:
            raise ValueError("Header placements require the Header sponsorship ad format")
        if self.ad_format == "sponsored_content" and self.surfaces != ["feed"]:
            raise ValueError("This ad format is only supported in feeds")
        if self.ends_at <= self.starts_at:
            raise ValueError("Campaign end must follow its start")
        for values in (self.product_ids, self.topic_ids, self.technologies, self.surfaces):
            normalized = [str(value).casefold() for value in values]
            if len(normalized) != len(set(normalized)):
                raise ValueError("Campaign selections must be unique")
        if self.targeting == "all" and (self.product_ids or self.topic_ids or self.technologies):
            raise ValueError("All-product campaigns cannot contain selections or filters")
        if self.targeting == "selected" and (
            not self.product_ids or self.topic_ids or self.technologies
        ):
            raise ValueError("Selected campaigns require only explicit catalog products")
        if self.targeting == "filters" and (
            self.product_ids or not (self.topic_ids or self.technologies)
        ):
            raise ValueError("Filtered campaigns require topics or technologies")
        return self


class CampaignOut(CampaignInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    account_id: uuid.UUID
    impressions: int = 0
    clicks: int = 0
    ctr: float | None = None
    managed: bool = False


class CampaignsOut(BaseModel):
    items: list[CampaignOut]
    total: int


class ProductPromotionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excluded: bool | None = None
    promotion_surfaces: list[PromotionSurface] | None = Field(
        default=None, max_length=len(PROMOTION_SURFACES)
    )
    ad_formats: list[AdFormat] | None = Field(default=None, max_length=4)
    link_rel: LinkRelationships | None = None

    ad_image_url: str | None = Field(default=None, max_length=2048)

    @field_validator("ad_image_url")
    @classmethod
    def valid_image_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = validate_public_url(value)
        if not normalized.startswith("https://"):
            raise ValueError("Ad images require a public HTTPS URL")
        return normalized

    @model_validator(mode="after")
    def valid_policy(self) -> Self:
        if not self.model_fields_set:
            raise ValueError("Supply at least one product promotion setting")
        if "excluded" in self.model_fields_set and self.excluded is None:
            raise ValueError("Product exclusion must be true or false")
        for values in (self.promotion_surfaces, self.ad_formats):
            if values is not None and len(values) != len(set(values)):
                raise ValueError("Product promotion selections must be unique")
        return self


class ProductPromotionOut(ProductPromotionInput):
    model_config = ConfigDict(from_attributes=True)
    account_id: uuid.UUID
    product_id: uuid.UUID
    excluded: bool
    image_url: str | None
    default_image_url: str | None
    ad_image_status: AdImageStatus
    effective_link_rel: list[LinkRel]
    account_link_rel: list[LinkRel]
    configurable_link_rel: bool
    link_rel_options: list[LinkRel]


class InventoryProductOut(BaseModel):
    id: uuid.UUID
    name: str
    technologies: list[str]
    status: str
    excluded: bool
    globally_excluded: bool
    commercial_approved: bool = False
    approval_source: Literal["qualified", "trusted"] | None = None
    promotion_surfaces: list[PromotionSurface] | None = None
    ad_formats: list[AdFormat] | None = None
    link_rel: LinkRelationships | None = None
    effective_link_rel: list[LinkRel]
    configurable_link_rel: bool
    link_rel_options: list[LinkRel]
    ad_image_url: str | None = None
    product_url: str
    tagline: Tagline
    pricing: Literal["free", "freemium", "paid", "unknown"]
    assessment: ProductAssessment
    revision: int
    source: Literal["manual", "imported"]
    editable: bool
    active: bool
    manual_active: bool | None


class InventoryOut(BaseModel):
    items: list[InventoryProductOut]
    total: int


class WorkspaceInventoryProductOut(InventoryProductOut):
    account_id: uuid.UUID
    account_name: str


class WorkspaceInventoryOut(BaseModel):
    items: list[WorkspaceInventoryProductOut]
    total: int
    limit: int
    offset: int


class MetricInput(BaseModel):
    day: date
    impressions: int = Field(ge=0, le=2**63 - 1)
    clicks: int = Field(ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def valid_day(self) -> Self:
        if self.day > datetime.now(UTC).date():
            raise ValueError("Measurement day cannot be in the future")
        return self


class Totals(BaseModel):
    impressions: int
    clicks: int
    ctr: float | None
    measured_days: int


class AssetMetrics(AssetOut, Totals):
    ad_formats: list[AdFormat] = Field(default_factory=list)
    aggregated: bool = False


class Trend(BaseModel):
    day: date
    impressions: int
    clicks: int


class FormatTotals(Totals):
    ad_format: AdFormat


class ReportSummary(BaseModel):
    start: date
    end: date
    totals: Totals
    trend: list[Trend]
    asset_total: int
    last_updated_at: datetime | None
    format_totals: list[FormatTotals] = Field(default_factory=list)


class Dashboard(ReportSummary):
    account: AccountOut
    assets: list[AssetMetrics]


class WorkspaceAssetMetrics(AssetMetrics):
    account_id: uuid.UUID
    account_name: str


class WorkspaceDashboard(ReportSummary):
    account_total: int
    assets: list[WorkspaceAssetMetrics]


def validate_product(payload: AssetInput, session: Session) -> None:
    if payload.product_id is not None and session.get(PartnerProduct, payload.product_id) is None:
        raise HTTPException(422, "Catalog product not found")


def asset_access(session: Session, account_id: uuid.UUID, asset_id: uuid.UUID) -> PartnerAsset:
    asset = session.scalar(
        select(PartnerAsset).where(
            PartnerAsset.id == asset_id, PartnerAsset.account_id == account_id
        )
    )
    if asset is None:
        raise HTTPException(404, "Partner asset not found")
    return asset


def totals(impressions: int, clicks: int, measured_days: int | None) -> MetricTotals:
    impressions, clicks = int(impressions or 0), int(clicks or 0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "ctr": clicks / impressions * 100 if impressions else None,
        "measured_days": int(measured_days or 0),
    }


def sponsor_articles(
    session: Session,
    *,
    q: str = "",
    article_id: uuid.UUID | None = None,
    offset: int = 0,
    limit: int = 25,
) -> SponsorArticlesData:
    query = select(Article).where(
        visible_article(),
        select(ArticleTopic.article_id)
        .join(Topic)
        .where(
            ArticleTopic.article_id == Article.id,
            ArticleTopic.role.in_(["primary", "supporting"]),
            Topic.status == "active",
        )
        .exists(),
    )
    if q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Article.title.ilike(f"%{term}%", escape="\\"))
    if article_id is not None:
        query = query.where(Article.id == article_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(
        query.order_by(Article.feed_at.desc(), Article.id).offset(offset).limit(limit)
    ).all()
    return {"items": ArticleOut.from_articles(rows), "total": total or 0}


def inventory_product_view(
    account: PartnerAccount,
    product: PartnerProduct,
    policy: PartnerProductPromotion | None,
    now: datetime,
    manual: PartnerManualProduct | None = None,
    *,
    active: bool = True,
) -> InventoryProductData:
    """Build policy summaries from a loaded account-product policy without extra SQL."""
    excluded = bool(policy and policy.excluded)
    trusted = bool(
        account.approval_mode == "trusted"
        and policy
        and policy.commercial_approved_at
        and policy.approval_source == "trusted"
    )
    return InventoryProductData(
        id=product.id,
        account_id=account.id,
        account_name=account.name,
        name=product.name,
        technologies=product.technologies,
        status=product.status,
        excluded=excluded,
        globally_excluded=product.excluded,
        commercial_approved=active
        and not excluded
        and not product.excluded
        and not product.merged_into_id
        and (eligible(product, now) or trusted),
        approval_source="trusted"
        if trusted
        else "qualified"
        if product.status == "approved"
        else None,
        ad_formats=policy.ad_formats if policy else None,
        link_rel=policy.link_rel if policy else None,
        effective_link_rel=effective_link_rel(account, policy),
        configurable_link_rel=account.configurable_link_rel,
        link_rel_options=list(LINK_REL_OPTIONS),
        promotion_surfaces=policy.promotion_surfaces if policy else None,
        ad_image_url=policy.ad_image_url if policy else None,
        product_url=product.product_url,
        tagline=product.tagline,
        pricing=product.pricing,
        assessment=product.assessment or {},
        revision=product.revision,
        source="manual" if manual else "imported",
        editable=bool(manual and product.metadata_account_id == account.id),
        active=active,
        manual_active=bool(manual.active) if manual else None,
    )


def inventory_page(
    session: Session,
    accounts_query: Select[PartnerAccount],
    *,
    q: str = "",
    product_id: uuid.UUID | None = None,
    offset: int = 0,
    limit: int = 100,
    include_archived: bool = False,
) -> InventoryPageData:
    query = associated_products_query(include_archived=include_archived).where(
        PartnerAccount.id.in_(accounts_query.with_only_columns(PartnerAccount.id).order_by(None))
    )
    if q.strip():
        from devfeed_core.text_search import text_search

        query = query.where(text_search(q.strip(), PartnerProduct.name))
    if product_id is not None:
        query = query.where(PartnerProduct.id == product_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.execute(
        query.add_columns(
            PartnerProductPromotion,
            PartnerManualProduct,
            account_product_source(PartnerAccount.id, PartnerProduct.id)
            if include_archived
            else literal(True),
        )
        .outerjoin(
            PartnerProductPromotion,
            and_(
                PartnerProductPromotion.account_id == PartnerAccount.id,
                PartnerProductPromotion.product_id == PartnerProduct.id,
            ),
        )
        .outerjoin(
            PartnerManualProduct,
            and_(
                PartnerManualProduct.account_id == PartnerAccount.id,
                PartnerManualProduct.product_id == PartnerProduct.id,
            ),
        )
        .order_by(PartnerProduct.name, PartnerAccount.name, PartnerAccount.id, PartnerProduct.id)
        .offset(offset)
        .limit(limit)
    ).all()
    now = datetime.now(UTC)
    return {
        "items": [
            inventory_product_view(account, product, policy, now, manual, active=active)
            for account, product, policy, manual, active in rows
        ],
        "total": total or 0,
        "limit": limit,
        "offset": offset,
    }


def inventory_products(
    session: Session,
    account: PartnerAccount,
    *,
    q: str = "",
    product_id: uuid.UUID | None = None,
    offset: int = 0,
    limit: int = 100,
    include_archived: bool = False,
) -> InventoryPageData:
    return inventory_page(
        session,
        select(PartnerAccount).where(PartnerAccount.id == account.id),
        q=q,
        product_id=product_id,
        offset=offset,
        limit=limit,
        include_archived=include_archived,
    )


def _product_promotion_policy(
    session: Session, account: PartnerAccount, product_id: uuid.UUID, *, lock: bool = False
) -> PartnerProductPromotion:
    if (
        session.execute(
            associated_products_query(account.id).where(PartnerProduct.id == product_id)
        ).first()
        is None
    ):
        raise HTTPException(404, "Product not found in this account's active resolved catalog")
    if lock:
        session.scalar(
            select(PartnerProduct).where(PartnerProduct.id == product_id).with_for_update()
        )
    row = session.get(PartnerProductPromotion, (account.id, product_id))
    return row or PartnerProductPromotion(
        account_id=account.id, product_id=product_id, excluded=False, ad_image_revision=0
    )


def _product_promotion_out(
    session: Session,
    account: PartnerAccount,
    policy: PartnerProductPromotion,
    product: PartnerProduct,
    *,
    status: AdImageStatus | None = None,
) -> ProductPromotionOut:
    return ProductPromotionOut(
        account_id=policy.account_id,
        product_id=policy.product_id,
        excluded=bool(policy.excluded),
        promotion_surfaces=policy.promotion_surfaces,
        ad_formats=policy.ad_formats,
        link_rel=policy.link_rel,
        effective_link_rel=effective_link_rel(account, policy),
        account_link_rel=effective_link_rel(account),
        configurable_link_rel=account.configurable_link_rel,
        link_rel_options=list(LINK_REL_OPTIONS),
        ad_image_url=policy.ad_image_url,
        image_url=product_image_url(product, policy),
        default_image_url=product_image_url(product),
        ad_image_status=status or product_ad_image_status(session, policy),
    )


def product_promotion(
    session: Session, account: PartnerAccount, product_id: uuid.UUID
) -> ProductPromotionOut:
    policy = _product_promotion_policy(session, account, product_id)
    product = session.get(PartnerProduct, product_id)
    assert product is not None
    return _product_promotion_out(session, account, policy, product)


def list_product_promotions(session: Session, account_id: uuid.UUID) -> list[ProductPromotionOut]:
    """Read policy, artwork and latest revision-bound image jobs in one grouped query."""
    statuses = (
        select(ArticleImageJob.product_id, ArticleImageJob.status)
        .join(
            PartnerProductPromotion,
            (PartnerProductPromotion.account_id == ArticleImageJob.account_id)
            & (PartnerProductPromotion.product_id == ArticleImageJob.product_id)
            & (PartnerProductPromotion.ad_image_revision == ArticleImageJob.subject_revision),
        )
        .where(
            ArticleImageJob.account_id == account_id,
            ArticleImageJob.operation == "product-ad-image",
            PartnerProductPromotion.ad_image_url.is_not(None),
        )
        .distinct(ArticleImageJob.product_id)
        .order_by(
            ArticleImageJob.product_id,
            ArticleImageJob.created_at.desc(),
            ArticleImageJob.id.desc(),
        )
        .subquery()
    )
    rows = session.execute(
        select(PartnerProductPromotion, PartnerProduct, PartnerAccount, statuses.c.status)
        .join(PartnerProduct, PartnerProduct.id == PartnerProductPromotion.product_id)
        .join(PartnerAccount, PartnerAccount.id == PartnerProductPromotion.account_id)
        .outerjoin(statuses, statuses.c.product_id == PartnerProductPromotion.product_id)
        .where(PartnerProductPromotion.account_id == account_id)
        .order_by(PartnerProductPromotion.product_id)
    ).all()
    return [
        _product_promotion_out(
            session,
            account,
            policy,
            product,
            status="default"
            if not policy.ad_image_url
            else "ready"
            if product_ad_image_current(policy)
            else status
            if status in {"queued", "running"}
            else "failed",
        )
        for policy, product, account, status in rows
    ]


def save_product_promotion(
    session: Session, account: PartnerAccount, product_id: uuid.UUID, payload: ProductPromotionInput
) -> ProductPromotionOut:
    previous = _product_promotion_policy(session, account, product_id, lock=True)
    values = payload.model_dump(mode="json", exclude_unset=True)
    if "link_rel" in values:
        try:
            validate_link_rel(account.tier, values["link_rel"])
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        if values["link_rel"] != previous.link_rel:
            mark_dirty(session, "partner_product_promotions")
    if values.get("ad_image_url") is not None and not get_settings().image_storage_enabled:
        raise HTTPException(422, "Enable managed image storage before setting a custom ad image")
    if "ad_image_url" in values and (
        values["ad_image_url"] != previous.ad_image_url or values["ad_image_url"] is None
    ):
        values.update(managed_ad_image={}, ad_image_revision=(previous.ad_image_revision or 0) + 1)
    if "ad_formats" in values:
        try:
            validate_product_formats(
                account.tier, values["ad_formats"], previous=previous.ad_formats
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
    session.execute(
        insert(PartnerProductPromotion)
        .values(account_id=account.id, product_id=product_id, **values)
        .on_conflict_do_update(
            index_elements=[PartnerProductPromotion.account_id, PartnerProductPromotion.product_id],
            set_=values,
        )
    )
    if "ad_image_url" in values:
        request_product_ad_image(session, account.id, product_id)
    ensure_automatic_assets(session, account.id, product_ids=[product_id])
    session.flush()
    row = session.scalar(
        select(PartnerProductPromotion)
        .where(
            PartnerProductPromotion.account_id == account.id,
            PartnerProductPromotion.product_id == product_id,
        )
        .execution_options(populate_existing=True)
    )

    product = session.get(PartnerProduct, product_id)
    assert product is not None
    assert row is not None
    return _product_promotion_out(session, account, row, product)
