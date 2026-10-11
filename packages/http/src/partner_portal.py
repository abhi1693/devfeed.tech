"""Shared schemas and reporting queries for partner and administrator services."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from devfeed_core.models import (
    PartnerAsset,
    PartnerDailyMetric,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerTrackedDailyMetric,
)
from devfeed_core.partner_placements import account_listing_destination, devfeed_outbound_url
from devfeed_core.partner_promotions import associated_products_query
from devfeed_core.partner_tiers import PartnerTier, PromotionKind, tier_policy
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import Numeric, and_, case, cast, func, or_, select, union_all

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

PromotionSurface = Literal["article", "topic", "feed"]
AssetSort = Literal["name", "impressions", "clicks", "ctr"]
SortOrder = Literal["asc", "desc"]


def default_promotion_surfaces() -> list[PromotionSurface]:
    return ["article", "topic", "feed"]


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


class AccountInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    tier: PartnerTier
    status: Literal["active", "paused"] = "active"
    organic_enabled: bool = True
    auto_include_products: bool = True
    promotion_surfaces: list[PromotionSurface] = Field(
        default_factory=default_promotion_surfaces, min_length=1, max_length=3
    )
    promotion_kind: PromotionKind | None = None
    approval_mode: Literal["qualified", "trusted"] = "qualified"
    outbound_destination: Literal["product_url", "listing_url"] = "product_url"

    @model_validator(mode="after")
    def unique_surfaces(self):
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


class PartnerPreferencesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outbound_destination: Literal["product_url", "listing_url"]


class DestinationPreviewProduct(BaseModel):
    id: uuid.UUID
    name: str
    product_url: str
    listing_url: str | None


class DestinationPreviewOut(BaseModel):
    product: DestinationPreviewProduct | None


def destination_preview(session, account) -> DestinationPreviewOut:
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
        )
        .order_by(case((qualified, 0), else_=1), func.lower(PartnerProduct.name), PartnerProduct.id)
        .limit(100)
    )
    for product in products:
        product_url = devfeed_outbound_url(product.product_url)
        if product_url is not None:
            return DestinationPreviewOut(
                product=DestinationPreviewProduct(
                    id=product.id,
                    name=product.name,
                    product_url=product_url,
                    listing_url=account_listing_destination(session, account.id, product.id),
                )
            )
    return DestinationPreviewOut(product=None)


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
    def product_required(self):
        if self.kind == "product" and self.product_id is None:
            raise ValueError("Product placements require a catalog product")
        return self


class AssetOut(AssetInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    automatic: bool = False
    campaign_id: uuid.UUID | None = None


class CampaignInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    status: Literal["draft", "active", "paused", "ended"] = "draft"
    targeting: Literal["all", "selected", "filters"] = "all"
    product_ids: list[uuid.UUID] = Field(default_factory=list, max_length=500)
    topic_ids: list[uuid.UUID] = Field(default_factory=list, max_length=100)
    technologies: list[
        Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    ] = Field(default_factory=list, max_length=100)
    surfaces: list[PromotionSurface] = Field(
        default_factory=default_promotion_surfaces, min_length=1, max_length=3
    )
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    priority: int = Field(default=50, ge=1, le=100)
    daily_impression_limit: int | None = Field(default=None, ge=1, le=2**63 - 1)

    @model_validator(mode="after")
    def valid_agreement(self):
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
    grandfathered: bool = False


class CampaignsOut(BaseModel):
    items: list[CampaignOut]
    total: int


class ProductPromotionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    excluded: bool


class ProductPromotionOut(ProductPromotionInput):
    model_config = ConfigDict(from_attributes=True)
    account_id: uuid.UUID
    product_id: uuid.UUID


class InventoryProductOut(BaseModel):
    id: uuid.UUID
    name: str
    technologies: list[str]
    status: str
    excluded: bool
    globally_excluded: bool
    commercial_approved: bool = False
    approval_source: Literal["qualified", "trusted"] | None = None


class InventoryOut(BaseModel):
    items: list[InventoryProductOut]
    total: int


class MetricInput(BaseModel):
    day: date
    impressions: int = Field(ge=0, le=2**63 - 1)
    clicks: int = Field(ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def valid_day(self):
        if self.day > datetime.now(UTC).date():
            raise ValueError("Measurement day cannot be in the future")
        return self


class Totals(BaseModel):
    impressions: int
    clicks: int
    ctr: float | None
    measured_days: int


class AssetMetrics(AssetOut, Totals):
    pass


class Trend(BaseModel):
    day: date
    impressions: int
    clicks: int


class Dashboard(BaseModel):
    account: AccountOut
    start: date
    end: date
    totals: Totals
    trend: list[Trend]
    assets: list[AssetMetrics]
    asset_total: int
    last_updated_at: datetime | None


def validate_product(payload: AssetInput, session):
    if payload.product_id is not None and session.get(PartnerProduct, payload.product_id) is None:
        raise HTTPException(422, "Catalog product not found")


def asset_access(session, account_id, asset_id):
    asset = session.scalar(
        select(PartnerAsset).where(
            PartnerAsset.id == asset_id, PartnerAsset.account_id == account_id
        )
    )
    if asset is None:
        raise HTTPException(404, "Partner asset not found")
    return asset


def totals(impressions, clicks, measured_days):
    impressions, clicks = int(impressions or 0), int(clicks or 0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "ctr": clicks / impressions * 100 if impressions else None,
        "measured_days": int(measured_days or 0),
    }


def reporting_dashboard(
    account_id: uuid.UUID,
    session,
    account,
    days=30,
    limit=100,
    offset=0,
    *,
    sort: AssetSort = "name",
    order: SortOrder = "asc",
):
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days - 1)
    metrics = union_all(
        *[
            select(model.asset_id, model.day, model.impressions, model.clicks, model.updated_at)
            .join(PartnerAsset)
            .where(PartnerAsset.account_id == account_id, model.day.between(start, end))
            for model in (PartnerDailyMetric, PartnerTrackedDailyMetric)
        ]
    ).subquery()
    summary = session.execute(
        select(
            func.sum(metrics.c.impressions),
            func.sum(metrics.c.clicks),
            func.count(func.distinct(metrics.c.day)),
            func.max(metrics.c.updated_at),
        )
    ).one()
    trend = (
        session.execute(
            select(
                metrics.c.day,
                func.sum(metrics.c.impressions).label("impressions"),
                func.sum(metrics.c.clicks).label("clicks"),
            )
            .group_by(metrics.c.day)
            .order_by(metrics.c.day)
        )
        .mappings()
        .all()
    )
    asset_metrics = (
        select(
            metrics.c.asset_id,
            func.sum(metrics.c.impressions).label("impressions"),
            func.sum(metrics.c.clicks).label("clicks"),
            func.count(func.distinct(metrics.c.day)).label("measured_days"),
        )
        .group_by(metrics.c.asset_id)
        .subquery()
    )
    # Sort the complete reporting set before paging. Missing measurements and an
    # undefined CTR remain last in either direction, distinct from measured zero.
    sort_value = {
        "name": func.lower(PartnerAsset.name),
        "impressions": asset_metrics.c.impressions,
        "clicks": asset_metrics.c.clicks,
        "ctr": cast(asset_metrics.c.clicks, Numeric) / func.nullif(asset_metrics.c.impressions, 0),
    }[sort]
    ordering = [(sort_value.desc() if order == "desc" else sort_value.asc()).nulls_last()]
    if sort != "name":
        ordering.append(func.lower(PartnerAsset.name).asc())
    ordering.append(PartnerAsset.id.asc())
    rows = session.execute(
        select(
            PartnerAsset,
            asset_metrics.c.impressions,
            asset_metrics.c.clicks,
            asset_metrics.c.measured_days,
        )
        .outerjoin(asset_metrics, PartnerAsset.id == asset_metrics.c.asset_id)
        .where(PartnerAsset.account_id == account_id)
        .order_by(*ordering)
        .limit(limit)
        .offset(offset)
    ).all()
    return {
        "account": account,
        "start": start,
        "end": end,
        "totals": totals(*summary[:3]),
        "last_updated_at": summary[3],
        "trend": trend,
        "assets": [
            {
                **AssetOut.model_validate(asset).model_dump(),
                **totals(impressions, clicks, measured_days),
            }
            for asset, impressions, clicks, measured_days in rows
        ],
        "asset_total": session.scalar(
            select(func.count())
            .select_from(PartnerAsset)
            .where(PartnerAsset.account_id == account_id)
        ),
    }
