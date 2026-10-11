"""Shared schemas and reporting queries for partner and administrator services."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from devfeed_core.models import (
    PartnerAsset,
    PartnerDailyMetric,
    PartnerProduct,
    PartnerTrackedDailyMetric,
)
from devfeed_core.partner_tiers import PartnerTier
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import func, select, union_all

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]

PromotionSurface = Literal["article", "topic", "feed"]


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
    frequency_cap: int = Field(default=3, ge=1, le=20)

    @model_validator(mode="after")
    def unique_surfaces(self):
        if len(self.promotion_surfaces) != len(set(self.promotion_surfaces)):
            raise ValueError("Promotion surfaces must be unique")
        return self


class AccountOut(AccountInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    benefits: list[str]


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


def reporting_dashboard(account_id: uuid.UUID, session, account, days=30, limit=100, offset=0):
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
    rows = session.execute(
        select(
            PartnerAsset,
            asset_metrics.c.impressions,
            asset_metrics.c.clicks,
            asset_metrics.c.measured_days,
        )
        .outerjoin(asset_metrics, PartnerAsset.id == asset_metrics.c.asset_id)
        .where(PartnerAsset.account_id == account_id)
        .order_by(PartnerAsset.name, PartnerAsset.id)
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
