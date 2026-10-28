"""Partner account management belongs exclusively to the authenticated admin API."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, TypedDict

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerCampaign,
    PartnerMembership,
    PartnerProduct,
    UserAccount,
)
from devfeed_core.partner_ad_formats import AdFormat, validate_account_formats
from devfeed_core.partner_audiences import (
    AudienceDimension,
    AudienceTopicsData,
    audience_topic_options,
    validate_audience,
)
from devfeed_core.partner_link_rel import LINK_REL_OPTIONS, LinkRel, validate_link_rel
from devfeed_core.partner_promotions import (
    request_promotion_enrollment,
)
from devfeed_core.partner_tiers import (
    TIER_BENEFITS,
    TierPolicy,
    promotion_kind_allowed,
    tier_policy,
)
from devfeed_core.text_search import text_search
from devfeed_http.body_limits import BodyLimitedRoute
from devfeed_http.dependencies import run_database
from devfeed_http.image_uploads import limit_image_upload
from devfeed_http.operation_policies import GatewayPolicy
from devfeed_http.partner_ad_images import (
    lock_image_account,
    lock_image_product,
    save_product_promotion_with_images,
    upload_product_ad_image,
)
from devfeed_http.partner_analytics_reporting import (
    ReportingAsset,
    ReportingMetrics,
    reporting_assets,
    reporting_metrics,
    require_partner_history,
)
from devfeed_http.partner_campaigns import CampaignMetadata, CampaignService
from devfeed_http.partner_portal import (
    AccountInput,
    AccountOut,
    AccountsData,
    AccountsOut,
    AssetInput,
    AssetOut,
    AudienceTopicsOut,
    CampaignInput,
    CampaignOut,
    CampaignsOut,
    CreativePreviewOut,
    Dashboard,
    DestinationPreviewOut,
    InventoryOut,
    InventoryPageData,
    InventoryProductData,
    MemberInput,
    MemberOut,
    MemberSubject,
    MetricInput,
    PartnershipTierOut,
    ProductPromotionInput,
    ProductPromotionOut,
    SponsorArticlesData,
    SponsorArticlesOut,
    asset_access,
    creative_preview,
    destination_preview,
    inventory_products,
    list_product_promotions,
    product_promotion,
    sponsor_articles,
    validate_product,
)
from devfeed_http.partner_products import (
    ManualProductInput,
    ManualProductUpdate,
    ProductDetailOut,
    archive_manual_product,
    create_manual_product,
    limit_product_write,
    lock_manual_identity,
    product_detail,
    update_manual_product,
)
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from devfeed_admin_api.auth import Admin, AdminIdentity, require_admin
from devfeed_admin_api.dependencies import DB, DBFactory, get_redis

router = APIRouter(
    prefix="/v1/admin/partner-accounts",
    tags=["admin-partner-accounts"],
    dependencies=[Depends(require_admin)],
    route_class=BodyLimitedRoute,
)


class DashboardData(ReportingMetrics):
    account: AccountOut


class PartnershipTierData(TierPolicy):
    tier: str
    benefits: list[str]
    link_rel_options: list[LinkRel]


class CampaignsData(TypedDict):
    items: list[CampaignOut]
    total: int


def require_superuser(admin: Admin) -> AdminIdentity:
    if "superuser" not in admin.roles:
        raise HTTPException(403, "Superuser access required")
    return admin


Superuser = Annotated[AdminIdentity, Depends(require_superuser)]


def account_access(
    session: Session, _admin: AdminIdentity, account_id: uuid.UUID, *, lock: bool = False
) -> PartnerAccount:
    query = select(PartnerAccount).where(PartnerAccount.id == account_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    account = session.scalar(query)
    if account is None:
        raise HTTPException(404, "Partner account not found")
    return account


def write_manual_account(
    session: Session,
    admin: AdminIdentity,
    account_id: uuid.UUID,
    *,
    product_id: uuid.UUID | None = None,
    url: str | None = None,
) -> tuple[PartnerAccount, PartnerProduct | None]:
    account_access(session, admin, account_id)
    product = lock_manual_identity(session, account_id, product_id=product_id, url=url)
    return account_access(session, admin, account_id, lock=True), product


@router.post(
    "/{account_id}/products",
    response_model=ProductDetailOut,
    status_code=201,
    operation_id="adminPartnerProductCreate",
    openapi_extra=GatewayPolicy(body_limit=65536).bounded_openapi_extra(),
)
def create_product(
    account_id: uuid.UUID, payload: ManualProductInput, session: DB, superuser: Superuser
) -> InventoryProductData:
    account_access(session, superuser, account_id)
    limit_product_write(get_redis(), superuser, account_id)
    account, existing = write_manual_account(
        session, superuser, account_id, url=payload.product_url
    )
    result = create_manual_product(session, account, payload, existing)
    session.commit()
    return result


@router.get(
    "/{account_id}/products/{product_id:uuid}",
    response_model=ProductDetailOut,
    operation_id="adminPartnerProductGet",
)
def get_product(
    account_id: uuid.UUID, product_id: uuid.UUID, session: DB, superuser: Superuser
) -> InventoryProductData:
    return product_detail(session, account_access(session, superuser, account_id), product_id)


@router.patch(
    "/{account_id}/products/{product_id:uuid}",
    response_model=ProductDetailOut,
    operation_id="adminPartnerProductUpdate",
    openapi_extra=GatewayPolicy(body_limit=65536).bounded_openapi_extra(),
)
def update_product(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: ManualProductUpdate,
    session: DB,
    superuser: Superuser,
) -> InventoryProductData:
    account_access(session, superuser, account_id)
    limit_product_write(get_redis(), superuser, account_id)
    account, product = write_manual_account(session, superuser, account_id, product_id=product_id)
    assert product is not None
    result = update_manual_product(session, account, product, payload)
    session.commit()
    return result


@router.delete(
    "/{account_id}/products/{product_id:uuid}",
    status_code=204,
    response_class=Response,
    operation_id="adminPartnerProductArchive",
)
def archive_product(
    account_id: uuid.UUID, product_id: uuid.UUID, session: DB, superuser: Superuser
) -> Response:
    account_access(session, superuser, account_id)
    limit_product_write(get_redis(), superuser, account_id)
    account, product = write_manual_account(session, superuser, account_id, product_id=product_id)
    assert product is not None
    archive_manual_product(session, account, product)
    session.commit()
    return Response(status_code=204)


@router.get("/tiers", response_model=list[PartnershipTierOut])
def partnership_tiers(_admin: Superuser) -> list[PartnershipTierData]:
    return [
        {
            "tier": tier,
            "benefits": list(benefits),
            "link_rel_options": list(LINK_REL_OPTIONS),
            **tier_policy(tier),
        }
        for tier, benefits in TIER_BENEFITS.items()
    ]


@router.get("", response_model=AccountsOut)
def accounts(
    session: DB,
    _admin: Superuser,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
) -> AccountsData:
    filters = [text_search(q.strip(), PartnerAccount.name)] if q.strip() else []
    return {
        "items": session.scalars(
            select(PartnerAccount)
            .where(*filters)
            .order_by(PartnerAccount.name, PartnerAccount.id)
            .limit(limit)
            .offset(offset)
        ).all(),
        "total": session.scalar(select(func.count()).select_from(PartnerAccount).where(*filters))
        or 0,
    }


@router.get(
    "/audience-topics",
    response_model=AudienceTopicsOut,
    operation_id="adminPartnerAudienceTopicsCatalog",
)
def audience_catalog(
    session: DB,
    _admin: Superuser,
    dimension: AudienceDimension,
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    topic_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
) -> AudienceTopicsData:
    return audience_topic_options(
        session, dimension, q=q, topic_id=topic_id, offset=offset, limit=limit
    )


@router.get(
    "/sponsor-articles",
    response_model=SponsorArticlesOut,
    operation_id="adminPartnerSponsorArticlesCatalog",
)
def sponsor_article_catalog(
    session: DB,
    _admin: Superuser,
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    article_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
) -> SponsorArticlesData:
    return sponsor_articles(session, q=q, article_id=article_id, offset=offset, limit=limit)


@router.get(
    "/{account_id}/sponsor-articles",
    response_model=SponsorArticlesOut,
    operation_id="adminPartnerSponsorArticles",
)
def account_sponsor_articles(
    account_id: uuid.UUID,
    session: DB,
    admin: Superuser,
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    article_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
) -> SponsorArticlesData:
    account_access(session, admin, account_id)
    return sponsor_articles(session, q=q, article_id=article_id, offset=offset, limit=limit)


@router.get(
    "/{account_id}/creative-preview",
    response_model=CreativePreviewOut,
    operation_id="adminPartnerCreativePreview",
)
def preview_creative(
    account_id: uuid.UUID, session: DB, admin: Superuser, product_id: uuid.UUID | None = None
) -> CreativePreviewOut:
    return creative_preview(
        session, account_access(session, admin, account_id), product_id=product_id
    )


@router.get(
    "/{account_id}/audience-topics",
    response_model=AudienceTopicsOut,
    operation_id="adminPartnerAudienceTopics",
)
def audience_topics(
    account_id: uuid.UUID,
    session: DB,
    admin: Superuser,
    dimension: AudienceDimension,
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    topic_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
) -> AudienceTopicsData:
    account = account_access(session, admin, account_id)
    return audience_topic_options(
        session, dimension, account=account, q=q, topic_id=topic_id, offset=offset, limit=limit
    )


def _dashboard_metadata(
    session: Session, admin: AdminIdentity, account_id: uuid.UUID, ad_format: AdFormat | None
) -> tuple[AccountOut, list[ReportingAsset]]:
    account = AccountOut.model_validate(account_access(session, admin, account_id))
    assets = reporting_assets(
        session, select(PartnerAccount).where(PartnerAccount.id == account_id), ad_format
    )
    return account, assets


@router.get("/{account_id}/dashboard", response_model=Dashboard)
async def dashboard(
    account_id: uuid.UUID,
    factory: DBFactory,
    admin: Superuser,
    request: Request,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ad_format: AdFormat | None = None,
) -> DashboardData:
    account, assets = await run_database(factory, _dashboard_metadata, admin, account_id, ad_format)
    client = request.app.state.analytics.client
    try:
        await require_partner_history(client)
        metrics = await reporting_metrics(
            assets,
            DirectEngagementAnalytics(client),
            days,
            limit,
            offset,
        )
    except AnalyticsUnavailable:
        raise HTTPException(503, "Partner analytics is being prepared") from None
    return {"account": account, **metrics}


@router.get(
    "/{account_id}/destination-preview",
    response_model=DestinationPreviewOut,
    operation_id="adminPartnerDestinationPreview",
)
def preview_destination(
    account_id: uuid.UUID, session: DB, admin: Superuser
) -> DestinationPreviewOut:
    return destination_preview(session, account_access(session, admin, account_id))


@router.post("", response_model=AccountOut, status_code=201)
def create_account(payload: AccountInput, _superuser: Superuser, session: DB) -> PartnerAccount:
    try:
        validate_link_rel(payload.tier, payload.link_rel)
        validate_account_formats(payload.tier, payload.ad_formats)
        validate_audience(session, payload.tier, payload.audience)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    account = PartnerAccount(**payload.model_dump(mode="json"))
    session.add(account)
    session.commit()
    return account


@router.put("/{account_id}", response_model=AccountOut)
def update_account(
    account_id: uuid.UUID, payload: AccountInput, superuser: Superuser, session: DB
) -> PartnerAccount:
    account = account_access(session, superuser, account_id, lock=True)
    try:
        if "link_rel" in payload.model_fields_set:
            validate_link_rel(payload.tier, payload.link_rel, previous=account.link_rel)
        validate_account_formats(payload.tier, payload.ad_formats, previous=account.ad_formats)
        validate_audience(session, payload.tier, payload.audience, previous=account.audience)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    for key, value in payload.model_dump(mode="json").items():
        if key == "link_rel" and key not in payload.model_fields_set:
            continue
        setattr(account, key, value)
    session.flush()
    request_promotion_enrollment(session, account_id)
    session.commit()
    return account


@router.get("/{account_id}/members", response_model=list[MemberOut])
def members(account_id: uuid.UUID, superuser: Superuser, session: DB) -> list[MemberOut]:
    account_access(session, superuser, account_id)
    rows = session.execute(
        select(PartnerMembership, UserAccount)
        .outerjoin(
            UserAccount,
            (UserAccount.issuer == PartnerMembership.issuer)
            & (UserAccount.subject == PartnerMembership.subject)
            & (UserAccount.organization_id == superuser.organization_id),
        )
        .where(PartnerMembership.account_id == account_id)
        .order_by(PartnerMembership.subject)
    ).all()
    return [
        MemberOut(
            issuer=membership.issuer,
            subject=membership.subject,
            user_id=user.id if user else None,
            name=user.name if user else None,
            email=user.email if user else None,
        )
        for membership, user in rows
    ]


@router.put("/{account_id}/members", status_code=204, response_class=Response)
def add_member(
    account_id: uuid.UUID, payload: MemberInput, superuser: Superuser, session: DB
) -> Response:
    account_access(session, superuser, account_id)
    user = session.get(UserAccount, payload.user_id)
    if (
        user is None
        or user.issuer != superuser.issuer
        or user.organization_id != superuser.organization_id
    ):
        raise HTTPException(404, "User not found in this identity organization")
    # Resolve immutable identity on the server; clients cannot supply a forged subject.
    session.execute(
        insert(PartnerMembership)
        .values(account_id=account_id, issuer=user.issuer, subject=user.subject)
        .on_conflict_do_nothing()
    )
    session.commit()
    return Response(status_code=204)


@router.delete("/{account_id}/members/{subject}", status_code=204, response_class=Response)
def remove_member(
    account_id: uuid.UUID, subject: MemberSubject, superuser: Superuser, session: DB
) -> Response:
    account_access(session, superuser, account_id)
    member = session.get(PartnerMembership, (account_id, superuser.issuer, subject))
    if member is not None:
        session.delete(member)
        session.commit()
    return Response(status_code=204)


@router.post("/{account_id}/assets", response_model=AssetOut, status_code=201)
def create_asset(
    account_id: uuid.UUID, payload: AssetInput, superuser: Superuser, session: DB
) -> PartnerAsset:
    account = account_access(session, superuser, account_id, lock=True)
    if not promotion_kind_allowed(account, payload.kind):
        raise HTTPException(422, "Promotion type is not included in this partnership tier")
    validate_product(payload, session)
    asset = PartnerAsset(account_id=account_id, **payload.model_dump())
    session.add(asset)
    session.commit()
    return asset


@router.get(
    "/{account_id}/assets/{asset_id}",
    response_model=AssetOut,
    operation_id="adminPartnerAssetGet",
)
def get_asset(
    account_id: uuid.UUID, asset_id: uuid.UUID, session: DB, superuser: Superuser
) -> PartnerAsset:
    account_access(session, superuser, account_id)
    return asset_access(session, account_id, asset_id)


@router.put("/{account_id}/assets/{asset_id}", response_model=AssetOut)
def update_asset(
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: AssetInput,
    superuser: Superuser,
    session: DB,
) -> PartnerAsset:
    account = account_access(session, superuser, account_id, lock=True)
    if not promotion_kind_allowed(account, payload.kind):
        raise HTTPException(422, "Promotion type is not included in this partnership tier")
    asset = asset_access(session, account_id, asset_id)
    validate_product(payload, session)
    if asset.automatic and (asset.product_id != payload.product_id or asset.kind != payload.kind):
        raise HTTPException(
            409, "Automatic attribution is immutable; use account defaults or campaigns"
        )
    for key, value in payload.model_dump().items():
        setattr(asset, key, value)
    session.commit()
    return asset


def _prepare_metric_write(
    session: Session,
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    superuser: AdminIdentity,
    day: date,
) -> tuple[int, datetime]:
    account_access(session, superuser, account_id)
    asset_access(session, account_id, asset_id)
    # Order revisions for one day's replacement, then release this transaction
    # before ClickHouse I/O. Its version resolves delayed concurrent inserts.
    lock_name = f"partner-metrics:{asset_id}:{day.isoformat()}"
    session.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_name, 0))))
    revision = session.scalar(select(func.txid_current()))
    assert revision is not None
    return int(revision), datetime.now(UTC)


@router.put("/{account_id}/assets/{asset_id}/metrics", status_code=204, response_class=Response)
async def record_metrics(
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: MetricInput,
    superuser: Superuser,
    factory: DBFactory,
    request: Request,
) -> Response:
    try:
        revision, observed = await run_database(
            factory, _prepare_metric_write, account_id, asset_id, superuser, payload.day
        )
        await DirectEngagementAnalytics(request.app.state.analytics.client).partner_totals(
            [
                {
                    "asset_id": asset_id,
                    **payload.model_dump(),
                    "revision": revision,
                    "updated_at": observed,
                }
            ]
        )
    except AnalyticsUnavailable:
        raise HTTPException(503, "Partner analytics unavailable") from None
    return Response(status_code=204)


def _campaign_page(
    session: Session, account_id: uuid.UUID, superuser: AdminIdentity, limit: int, offset: int
) -> CampaignMetadata:
    account = account_access(session, superuser, account_id)
    return CampaignService(session, account).page(limit=limit, offset=offset)


def _campaign_metadata(
    session: Session, account_id: uuid.UUID, campaign_id: uuid.UUID, superuser: AdminIdentity
) -> CampaignMetadata:
    account = account_access(session, superuser, account_id)
    return CampaignService(session, account).get(campaign_id)


@router.get(
    "/{account_id}/campaigns", response_model=CampaignsOut, operation_id="adminPartnerCampaignsList"
)
async def campaigns(
    account_id: uuid.UUID,
    factory: DBFactory,
    superuser: Superuser,
    request: Request,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> CampaignsData:
    metadata = await run_database(factory, _campaign_page, account_id, superuser, limit, offset)
    client = request.app.state.analytics.client
    try:
        await require_partner_history(client)
        return {
            "items": await metadata.reports(DirectEngagementAnalytics(client)),
            "total": metadata.total,
        }
    except AnalyticsUnavailable:
        raise HTTPException(503, "Partner analytics is being prepared") from None


@router.get(
    "/{account_id}/campaigns/{campaign_id}",
    response_model=CampaignOut,
    operation_id="adminPartnerCampaignGet",
)
async def campaign(
    account_id: uuid.UUID,
    campaign_id: uuid.UUID,
    factory: DBFactory,
    superuser: Superuser,
    request: Request,
) -> CampaignOut:
    metadata = await run_database(factory, _campaign_metadata, account_id, campaign_id, superuser)
    client = request.app.state.analytics.client
    try:
        await require_partner_history(client)
        return (await metadata.reports(DirectEngagementAnalytics(client)))[0]
    except AnalyticsUnavailable:
        raise HTTPException(503, "Partner analytics is being prepared") from None


@router.post(
    "/{account_id}/campaigns",
    response_model=CampaignOut,
    status_code=201,
    operation_id="adminPartnerCampaignCreate",
)
def create_campaign(
    account_id: uuid.UUID, payload: CampaignInput, session: DB, superuser: Superuser
) -> PartnerCampaign:
    account = account_access(session, superuser, account_id, lock=True)
    campaign = CampaignService(session, account).save(payload)
    session.commit()
    return campaign


def _update_campaign(
    session: Session,
    account_id: uuid.UUID,
    campaign_id: uuid.UUID,
    payload: CampaignInput,
    superuser: AdminIdentity,
) -> CampaignMetadata:
    account = account_access(session, superuser, account_id, lock=True)
    service = CampaignService(session, account)
    campaign = service.save(payload, campaign_id=campaign_id)
    session.commit()
    return service.metadata([campaign])


@router.put(
    "/{account_id}/campaigns/{campaign_id}",
    response_model=CampaignOut,
    operation_id="adminPartnerCampaignUpdate",
)
async def update_campaign(
    account_id: uuid.UUID,
    campaign_id: uuid.UUID,
    payload: CampaignInput,
    factory: DBFactory,
    superuser: Superuser,
    request: Request,
) -> CampaignOut:
    client = request.app.state.analytics.client
    try:
        await require_partner_history(client)
        metadata = await run_database(
            factory, _update_campaign, account_id, campaign_id, payload, superuser
        )
        return (await metadata.reports(DirectEngagementAnalytics(client)))[0]
    except AnalyticsUnavailable:
        raise HTTPException(503, "Partner analytics is being prepared") from None


@router.get(
    "/{account_id}/inventory", response_model=InventoryOut, operation_id="adminPartnerInventoryList"
)
def inventory(
    account_id: uuid.UUID,
    session: DB,
    superuser: Superuser,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    product_id: uuid.UUID | None = None,
    include_archived: bool = False,
) -> InventoryPageData:
    account = account_access(session, superuser, account_id)
    return inventory_products(
        session,
        account,
        q=q,
        product_id=product_id,
        offset=offset,
        limit=limit,
        include_archived=include_archived,
    )


@router.get(
    "/{account_id}/products/promotions",
    response_model=list[ProductPromotionOut],
    operation_id="adminPartnerProductPromotionsList",
)
def product_promotions(
    account_id: uuid.UUID, session: DB, superuser: Superuser
) -> list[ProductPromotionOut]:
    account_access(session, superuser, account_id)
    return list_product_promotions(session, account_id)


@router.put(
    "/{account_id}/products/{product_id}/promotion",
    response_model=ProductPromotionOut,
    operation_id="adminPartnerProductPromotionUpdate",
)
def update_product_promotion(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: ProductPromotionInput,
    session: DB,
    superuser: Superuser,
) -> ProductPromotionOut:
    account_access(session, superuser, account_id)
    lock_image_product(session, account_id, product_id)
    account = lock_image_account(
        session, select(PartnerAccount).where(PartnerAccount.id == account_id)
    )
    if account is None:
        raise HTTPException(404, "Partner account not found")
    return save_product_promotion_with_images(session, account, product_id, payload)


@router.get(
    "/{account_id}/products/{product_id}/promotion",
    response_model=ProductPromotionOut,
    operation_id="adminPartnerProductPromotionGet",
)
def get_product_promotion(
    account_id: uuid.UUID, product_id: uuid.UUID, session: DB, superuser: Superuser
) -> ProductPromotionOut:
    return product_promotion(session, account_access(session, superuser, account_id), product_id)


@router.post(
    "/{account_id}/products/{product_id}/ad-image",
    response_model=ProductPromotionOut,
    operation_id="adminPartnerProductAdImageUpload",
    openapi_extra=GatewayPolicy(
        body_limit=5 * 1024 * 1024 + 65536, body_timeout_ms=10_000
    ).bounded_openapi_extra(rejection="image"),
)
def upload_ad_image(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP, up to 5 MB")],
    session: DB,
    superuser: Superuser,
) -> ProductPromotionOut:
    account_access(session, superuser, account_id)
    limit_image_upload(get_redis(), superuser, account_id)
    lock_image_product(session, account_id, product_id)
    account = lock_image_account(
        session, select(PartnerAccount).where(PartnerAccount.id == account_id)
    )
    if account is None:
        raise HTTPException(404, "Partner account not found")
    return upload_product_ad_image(session, account, product_id, file)


@router.get("/{account_id}", response_model=AccountOut, operation_id="adminPartnerAccountGet")
def get_account(account_id: uuid.UUID, session: DB, admin: Superuser) -> PartnerAccount:
    return account_access(session, admin, account_id)
