"""Shared schemas and reporting queries for partner and administrator services."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from devfeed_core.models import (
    Article,
    ArticleTopic,
    PartnerAsset,
    PartnerDailyMetric,
    PartnerProduct,
    PartnerProductPromotion,
    PartnerTrackedDailyMetric,
    Topic,
)
from devfeed_core.partner_ad_formats import (
    AUTOMATIC_AD_FORMATS,
    AdFormat,
    default_ad_formats,
    validate_product_formats,
)
from devfeed_core.partner_audiences import AudienceInput
from devfeed_core.partner_placements import account_listing_destination, devfeed_outbound_url
from devfeed_core.partner_product_images import product_image_url
from devfeed_core.partner_product_logos import product_logo_url
from devfeed_core.partner_promotions import (
    associated_products_query,
    commercially_eligible,
    ensure_automatic_assets,
)
from devfeed_core.partner_tiers import PartnerTier, PromotionKind, tier_policy
from devfeed_core.publication import visible_article
from devfeed_core.schemas import ArticleOut
from fastapi import HTTPException
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import (
    Numeric,
    String,
    Uuid,
    and_,
    any_,
    case,
    cast,
    func,
    literal,
    or_,
    select,
    union_all,
)
from sqlalchemy.dialects.postgresql import aggregate_order_by, array, insert

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
    advanced_targeting: bool
    allowed_ad_formats: list[AdFormat]


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
    audience: AudienceInput = Field(default_factory=AudienceInput)
    ad_formats: list[AdFormat] = Field(default_factory=default_ad_formats, max_length=3)

    @model_validator(mode="after")
    def unique_surfaces(self):
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


class PartnerPreferencesInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outbound_destination: Literal["product_url", "listing_url"] | None = None
    audience: AudienceInput | None = None
    ad_formats: list[AdFormat] | None = Field(default=None, max_length=3)

    @model_validator(mode="after")
    def supplied_preference(self):
        if self.outbound_destination is None and self.audience is None and self.ad_formats is None:
            raise ValueError("Supply at least one partner preference")
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
    description: str
    logo_url: str | None
    image_url: str | None


class CreativePreviewOut(BaseModel):
    product: CreativePreviewProduct | None


class SponsorArticlesOut(BaseModel):
    items: list[ArticleOut]
    total: int


def _preview_product(session, account, product_id=None):
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


def destination_preview(session, account) -> DestinationPreviewOut:
    selected = _preview_product(session, account)
    if selected is None:
        return DestinationPreviewOut(product=None)
    product, product_url = selected
    return DestinationPreviewOut(
        product=DestinationPreviewProduct(
            id=product.id,
            name=product.name,
            product_url=product_url,
            listing_url=account_listing_destination(session, account.id, product.id),
        )
    )


def creative_preview(session, account, product_id=None) -> CreativePreviewOut:
    selected = _preview_product(session, account, product_id)
    if selected is None:
        return CreativePreviewOut(product=None)
    product, product_url = selected
    return CreativePreviewOut(
        product=CreativePreviewProduct(
            id=product.id,
            name=product.name,
            description=product.description,
            product_url=product_url,
            listing_url=account_listing_destination(session, account.id, product.id),
            logo_url=product_logo_url(product),
            image_url=product_image_url(product),
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
    def product_required(self):
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
        default_factory=default_promotion_surfaces, min_length=1, max_length=3
    )
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    priority: int = Field(default=50, ge=1, le=100)
    daily_impression_limit: int | None = Field(default=None, ge=1, le=2**63 - 1)

    @model_validator(mode="after")
    def valid_agreement(self):
        if (self.ad_format == "sponsored_content") != (self.article_id is not None):
            raise ValueError("Supply an article only for sponsored content campaigns")
        if self.ad_format in {"homepage_sponsorship", "sponsored_content"}:
            if "surfaces" not in self.model_fields_set:
                self.surfaces = ["feed"]
            elif self.surfaces != ["feed"]:
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
    promotion_surfaces: list[PromotionSurface] | None = Field(default=None, max_length=3)
    ad_formats: list[AdFormat] | None = Field(default=None, max_length=4)

    @model_validator(mode="after")
    def valid_policy(self):
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
    ad_formats: list[AdFormat] = Field(default_factory=list)
    aggregated: bool = False


class Trend(BaseModel):
    day: date
    impressions: int
    clicks: int


class FormatTotals(Totals):
    ad_format: AdFormat


class Dashboard(BaseModel):
    account: AccountOut
    start: date
    end: date
    totals: Totals
    trend: list[Trend]
    assets: list[AssetMetrics]
    asset_total: int
    last_updated_at: datetime | None
    format_totals: list[FormatTotals] = Field(default_factory=list)


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
    ad_format: AdFormat | None = None,
):
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days - 1)
    metrics = union_all(
        *[
            select(model.asset_id, model.day, model.impressions, model.clicks, model.updated_at)
            .join(PartnerAsset)
            .where(
                PartnerAsset.account_id == account_id,
                model.day.between(start, end),
                *([PartnerAsset.ad_format == ad_format] if ad_format is not None else []),
            )
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
    format_totals = session.execute(
        select(
            PartnerAsset.ad_format,
            func.sum(metrics.c.impressions),
            func.sum(metrics.c.clicks),
            func.count(func.distinct(metrics.c.day)),
        )
        .join(PartnerAsset, PartnerAsset.id == metrics.c.asset_id)
        .group_by(PartnerAsset.ad_format)
        .order_by(PartnerAsset.ad_format)
    ).all()
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
    # Ledger identities stay immutable. Reporting follows merged canonical product
    # identities, then combines every format before sorting or paginating products.
    # The visited path and bound also make malformed legacy alias chains finite.
    scoped_assets = (
        select(PartnerAsset)
        .where(
            PartnerAsset.account_id == account_id,
            *([PartnerAsset.ad_format == ad_format] if ad_format is not None else []),
        )
        .cte("report_assets")
    )
    identities = (
        select(
            PartnerProduct.id.label("original_id"),
            PartnerProduct.id.label("canonical_id"),
            PartnerProduct.merged_into_id,
            array([PartnerProduct.id]).label("path"),
            literal(0).label("depth"),
        )
        .where(PartnerProduct.id.in_(select(scoped_assets.c.product_id)))
        .cte("report_identity", recursive=True)
    )
    parent = PartnerProduct.__table__.alias("report_parent")
    identities = identities.union_all(
        select(
            identities.c.original_id,
            parent.c.id,
            parent.c.merged_into_id,
            identities.c.path + array([parent.c.id]),
            identities.c.depth + 1,
        )
        .join(parent, parent.c.id == identities.c.merged_into_id)
        .where(identities.c.depth < 32, parent.c.id != any_(identities.c.path))
    )
    resolved = (
        select(identities.c.original_id, identities.c.canonical_id)
        .distinct(identities.c.original_id)
        .order_by(identities.c.original_id, identities.c.depth.desc())
        .subquery()
    )
    product = PartnerProduct.__table__.alias("report_product")
    canonical_id = func.coalesce(resolved.c.canonical_id, scoped_assets.c.product_id)
    group_key = case(
        (canonical_id.is_not(None), literal("product:") + cast(canonical_id, String)),
        else_=literal("asset:") + cast(scoped_assets.c.id, String),
    )
    collection = (
        select(
            scoped_assets,
            canonical_id.label("canonical_product_id"),
            product.c.name.label("product_name"),
            group_key.label("group_key"),
        )
        .select_from(scoped_assets)
        .outerjoin(resolved, resolved.c.original_id == scoped_assets.c.product_id)
        .outerjoin(product, product.c.id == canonical_id)
        .subquery()
    )
    grouped = (
        select(
            collection.c.group_key,
            cast(func.min(cast(collection.c.id, String)), Uuid).label("id"),
            cast(func.min(cast(collection.c.canonical_product_id, String)), Uuid).label(
                "product_id"
            ),
            func.coalesce(func.min(collection.c.product_name), func.min(collection.c.name)).label(
                "name"
            ),
            case((func.bool_or(collection.c.kind == "ad"), "ad"), else_="product").label("kind"),
            case(
                (func.bool_or(collection.c.status == "active"), "active"),
                (func.bool_or(collection.c.status == "paused"), "paused"),
                (func.bool_or(collection.c.status == "draft"), "draft"),
                else_="ended",
            ).label("status"),
            func.bool_and(collection.c.automatic).label("automatic"),
            (func.count() > 1).label("aggregated"),
            case(
                (func.count() == 1, cast(func.min(cast(collection.c.campaign_id, String)), Uuid)),
                else_=None,
            ).label("campaign_id"),
            case(
                (func.count() == 1, cast(func.min(cast(collection.c.article_id, String)), Uuid)),
                else_=None,
            ).label("article_id"),
            func.min(collection.c.ad_format).label("ad_format"),
            func.array_agg(
                aggregate_order_by(collection.c.ad_format.distinct(), collection.c.ad_format)
            ).label("ad_formats"),
        )
        .group_by(collection.c.group_key)
        .subquery()
    )
    product_metrics = (
        select(
            collection.c.group_key,
            func.sum(metrics.c.impressions).label("impressions"),
            func.sum(metrics.c.clicks).label("clicks"),
            func.count(func.distinct(metrics.c.day)).label("measured_days"),
        )
        .select_from(metrics)
        .join(collection, collection.c.id == metrics.c.asset_id)
        .group_by(collection.c.group_key)
        .subquery()
    )
    # Sort the complete reporting set before paging. Missing measurements and an
    # undefined CTR remain last in either direction, distinct from measured zero.
    sort_value = {
        "name": func.lower(grouped.c.name),
        "impressions": product_metrics.c.impressions,
        "clicks": product_metrics.c.clicks,
        "ctr": cast(product_metrics.c.clicks, Numeric)
        / func.nullif(product_metrics.c.impressions, 0),
    }[sort]
    ordering = [(sort_value.desc() if order == "desc" else sort_value.asc()).nulls_last()]
    if sort != "name":
        ordering.append(func.lower(grouped.c.name).asc())
    ordering.append(grouped.c.id.asc())
    rows = (
        session.execute(
            select(
                grouped,
                product_metrics.c.impressions,
                product_metrics.c.clicks,
                product_metrics.c.measured_days,
            )
            .outerjoin(product_metrics, grouped.c.group_key == product_metrics.c.group_key)
            .order_by(*ordering)
            .limit(limit)
            .offset(offset)
        )
        .mappings()
        .all()
    )
    return {
        "account": account,
        "start": start,
        "end": end,
        "totals": totals(*summary[:3]),
        "format_totals": [
            dict(ad_format=kind, **totals(*values)) for kind, *values in format_totals
        ],
        "last_updated_at": summary[3],
        "trend": trend,
        "assets": [
            {
                **{
                    key: row[key]
                    for key in (
                        "id",
                        "product_id",
                        "name",
                        "kind",
                        "status",
                        "automatic",
                        "aggregated",
                        "ad_format",
                        "ad_formats",
                        "campaign_id",
                        "article_id",
                    )
                },
                **totals(row["impressions"], row["clicks"], row["measured_days"]),
            }
            for row in rows
        ],
        "asset_total": session.scalar(select(func.count()).select_from(grouped)),
    }


def sponsor_articles(session, *, q="", article_id=None, offset=0, limit=25):
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
    return {"items": [ArticleOut.from_article(row) for row in rows], "total": total}


def inventory_products(session, account, *, q="", product_id=None, offset=0, limit=100):
    query = associated_products_query(account.id)
    if q.strip():
        term = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(PartnerProduct.name.ilike(f"%{term}%", escape="\\"))
    if product_id is not None:
        query = query.where(PartnerProduct.id == product_id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.execute(
        query.order_by(PartnerProduct.name, PartnerProduct.id).offset(offset).limit(limit)
    ).all()
    policies = {
        row.product_id: row
        for row in session.scalars(
            select(PartnerProductPromotion).where(
                PartnerProductPromotion.account_id == account.id,
                PartnerProductPromotion.product_id.in_([product.id for _, product in rows]),
            )
        )
    }
    items = []
    for _, product in rows:
        policy = policies.get(product.id)
        excluded = bool(policy and policy.excluded)
        trusted = bool(
            account.approval_mode == "trusted" and policy and policy.commercial_approved_at
        )
        items.append(
            dict(
                id=product.id,
                name=product.name,
                technologies=product.technologies,
                status=product.status,
                excluded=excluded,
                globally_excluded=product.excluded,
                commercial_approved=not excluded
                and commercially_eligible(session, account, product, datetime.now(UTC)),
                approval_source="trusted"
                if trusted
                else "qualified"
                if product.status == "approved"
                else None,
                ad_formats=policy.ad_formats if policy else None,
                promotion_surfaces=policy.promotion_surfaces if policy else None,
            )
        )
    return {"items": items, "total": total}


def product_promotion(session, account, product_id):
    if (
        session.execute(
            associated_products_query(account.id).where(PartnerProduct.id == product_id)
        ).first()
        is None
    ):
        raise HTTPException(404, "Product not found in this account's active resolved catalog")
    row = session.get(PartnerProductPromotion, (account.id, product_id))
    return row or PartnerProductPromotion(
        account_id=account.id, product_id=product_id, excluded=False
    )


def save_product_promotion(session, account, product_id, payload):
    previous = product_promotion(session, account, product_id)
    values = payload.model_dump(mode="json", exclude_unset=True)
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
    ensure_automatic_assets(session, account.id, product_ids=[product_id])
    session.flush()
    return session.scalar(
        select(PartnerProductPromotion)
        .where(
            PartnerProductPromotion.account_id == account.id,
            PartnerProductPromotion.product_id == product_id,
        )
        .execution_options(populate_existing=True)
    )
