"""Partner account management belongs exclusively to the authenticated admin API."""

import uuid
from datetime import UTC, datetime
from typing import Annotated

from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerDailyMetric,
    PartnerMembership,
    UserAccount,
)
from devfeed_core.partner_ad_formats import AdFormat, validate_account_formats
from devfeed_core.partner_audiences import (
    AudienceDimension,
    audience_topic_options,
    validate_audience,
)
from devfeed_core.partner_promotions import (
    request_promotion_enrollment,
)
from devfeed_core.partner_tiers import TIER_BENEFITS, promotion_kind_allowed, tier_policy
from devfeed_core.text_search import text_search
from devfeed_http.body_limits import BodyLimitedRoute
from devfeed_http.image_uploads import limit_image_upload
from devfeed_http.operation_policies import GatewayPolicy
from devfeed_http.partner_ad_images import (
    lock_image_account,
    lock_image_product,
    save_product_promotion_with_images,
    upload_product_ad_image,
)
from devfeed_http.partner_campaigns import CampaignService
from devfeed_http.partner_portal import (
    AccountInput,
    AccountOut,
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
    MemberInput,
    MemberOut,
    MemberSubject,
    MetricInput,
    PartnershipTierOut,
    ProductPromotionInput,
    ProductPromotionOut,
    SponsorArticlesOut,
    asset_access,
    creative_preview,
    destination_preview,
    inventory_products,
    list_product_promotions,
    product_promotion,
    reporting_dashboard,
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
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from devfeed_admin_api.auth import Admin, AdminIdentity, require_admin
from devfeed_admin_api.dependencies import DB, get_redis

router = APIRouter(
    prefix="/v1/admin/partner-accounts",
    tags=["admin-partner-accounts"],
    dependencies=[Depends(require_admin)],
    route_class=BodyLimitedRoute,
)


def require_superuser(admin: Admin) -> AdminIdentity:
    if "superuser" not in admin.roles:
        raise HTTPException(403, "Superuser access required")
    return admin


Superuser = Annotated[AdminIdentity, Depends(require_superuser)]


def account_access(session, _admin, account_id, *, lock=False):
    query = select(PartnerAccount).where(PartnerAccount.id == account_id)
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    account = session.scalar(query)
    if account is None:
        raise HTTPException(404, "Partner account not found")
    return account


def write_manual_account(session, admin, account_id, *, product_id=None, url=None):
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
):
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
def get_product(account_id: uuid.UUID, product_id: uuid.UUID, session: DB, superuser: Superuser):
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
):
    account_access(session, superuser, account_id)
    limit_product_write(get_redis(), superuser, account_id)
    account, product = write_manual_account(session, superuser, account_id, product_id=product_id)
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
):
    account_access(session, superuser, account_id)
    limit_product_write(get_redis(), superuser, account_id)
    account, product = write_manual_account(session, superuser, account_id, product_id=product_id)
    archive_manual_product(session, account, product)
    session.commit()
    return Response(status_code=204)


@router.get("/tiers", response_model=list[PartnershipTierOut])
def partnership_tiers(_admin: Superuser):
    return [
        {"tier": tier, "benefits": list(benefits), **tier_policy(tier)}
        for tier, benefits in TIER_BENEFITS.items()
    ]


@router.get("", response_model=AccountsOut)
def accounts(
    session: DB,
    _admin: Superuser,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
):
    filters = [text_search(q.strip(), PartnerAccount.name)] if q.strip() else []
    return {
        "items": session.scalars(
            select(PartnerAccount)
            .where(*filters)
            .order_by(PartnerAccount.name, PartnerAccount.id)
            .limit(limit)
            .offset(offset)
        ).all(),
        "total": session.scalar(select(func.count()).select_from(PartnerAccount).where(*filters)),
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
):
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
):
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
):
    account_access(session, admin, account_id)
    return sponsor_articles(session, q=q, article_id=article_id, offset=offset, limit=limit)


@router.get(
    "/{account_id}/creative-preview",
    response_model=CreativePreviewOut,
    operation_id="adminPartnerCreativePreview",
)
def preview_creative(
    account_id: uuid.UUID, session: DB, admin: Superuser, product_id: uuid.UUID | None = None
):
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
):
    account = account_access(session, admin, account_id)
    return audience_topic_options(
        session, dimension, account=account, q=q, topic_id=topic_id, offset=offset, limit=limit
    )


@router.get("/{account_id}/dashboard", response_model=Dashboard)
def dashboard(
    account_id: uuid.UUID,
    session: DB,
    admin: Superuser,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    ad_format: AdFormat | None = None,
):
    return reporting_dashboard(
        account_id,
        session,
        account_access(session, admin, account_id),
        days,
        limit,
        offset,
        ad_format=ad_format,
    )


@router.get(
    "/{account_id}/destination-preview",
    response_model=DestinationPreviewOut,
    operation_id="adminPartnerDestinationPreview",
)
def preview_destination(account_id: uuid.UUID, session: DB, admin: Superuser):
    return destination_preview(session, account_access(session, admin, account_id))


@router.post("", response_model=AccountOut, status_code=201)
def create_account(payload: AccountInput, _superuser: Superuser, session: DB):
    try:
        validate_account_formats(payload.tier, payload.ad_formats)
        validate_audience(session, payload.tier, payload.audience)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    account = PartnerAccount(**payload.model_dump(mode="json"))
    session.add(account)
    session.commit()
    return account


@router.put("/{account_id}", response_model=AccountOut)
def update_account(account_id: uuid.UUID, payload: AccountInput, superuser: Superuser, session: DB):
    account = account_access(session, superuser, account_id, lock=True)
    try:
        validate_account_formats(payload.tier, payload.ad_formats, previous=account.ad_formats)
        validate_audience(session, payload.tier, payload.audience, previous=account.audience)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    for key, value in payload.model_dump(mode="json").items():
        setattr(account, key, value)
    session.flush()
    request_promotion_enrollment(session, account_id)
    session.commit()
    return account


@router.get("/{account_id}/members", response_model=list[MemberOut])
def members(account_id: uuid.UUID, superuser: Superuser, session: DB):
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
def add_member(account_id: uuid.UUID, payload: MemberInput, superuser: Superuser, session: DB):
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
def remove_member(account_id: uuid.UUID, subject: MemberSubject, superuser: Superuser, session: DB):
    account_access(session, superuser, account_id)
    member = session.get(PartnerMembership, (account_id, superuser.issuer, subject))
    if member is not None:
        session.delete(member)
        session.commit()
    return Response(status_code=204)


@router.post("/{account_id}/assets", response_model=AssetOut, status_code=201)
def create_asset(account_id: uuid.UUID, payload: AssetInput, superuser: Superuser, session: DB):
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
def get_asset(account_id: uuid.UUID, asset_id: uuid.UUID, session: DB, superuser: Superuser):
    account_access(session, superuser, account_id)
    return asset_access(session, account_id, asset_id)


@router.put("/{account_id}/assets/{asset_id}", response_model=AssetOut)
def update_asset(
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: AssetInput,
    superuser: Superuser,
    session: DB,
):
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


@router.put("/{account_id}/assets/{asset_id}/metrics", status_code=204, response_class=Response)
def record_metrics(
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: MetricInput,
    superuser: Superuser,
    session: DB,
):
    account_access(session, superuser, account_id)
    asset_access(session, account_id, asset_id)
    # Replace the day's trusted totals atomically, so retrying an import cannot double count.
    values = {**payload.model_dump(), "updated_at": datetime.now(UTC)}
    session.execute(
        insert(PartnerDailyMetric)
        .values(asset_id=asset_id, **values)
        .on_conflict_do_update(
            index_elements=[PartnerDailyMetric.asset_id, PartnerDailyMetric.day], set_=values
        )
    )
    session.commit()
    return Response(status_code=204)


@router.get(
    "/{account_id}/campaigns", response_model=CampaignsOut, operation_id="adminPartnerCampaignsList"
)
def campaigns(
    account_id: uuid.UUID,
    session: DB,
    superuser: Superuser,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    account = account_access(session, superuser, account_id)
    return CampaignService(session, account).page(limit=limit, offset=offset)


@router.get(
    "/{account_id}/campaigns/{campaign_id}",
    response_model=CampaignOut,
    operation_id="adminPartnerCampaignGet",
)
def campaign(account_id: uuid.UUID, campaign_id: uuid.UUID, session: DB, superuser: Superuser):
    account = account_access(session, superuser, account_id)
    return CampaignService(session, account).get(campaign_id)


@router.post(
    "/{account_id}/campaigns",
    response_model=CampaignOut,
    status_code=201,
    operation_id="adminPartnerCampaignCreate",
)
def create_campaign(
    account_id: uuid.UUID, payload: CampaignInput, session: DB, superuser: Superuser
):
    account = account_access(session, superuser, account_id, lock=True)
    campaign = CampaignService(session, account).save(payload)
    session.commit()
    return campaign


@router.put(
    "/{account_id}/campaigns/{campaign_id}",
    response_model=CampaignOut,
    operation_id="adminPartnerCampaignUpdate",
)
def update_campaign(
    account_id: uuid.UUID,
    campaign_id: uuid.UUID,
    payload: CampaignInput,
    session: DB,
    superuser: Superuser,
):
    account = account_access(session, superuser, account_id, lock=True)
    service = CampaignService(session, account)
    campaign = service.save(payload, campaign_id=campaign_id)
    session.commit()
    return service.reports([campaign])[0]


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
):
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
def product_promotions(account_id: uuid.UUID, session: DB, superuser: Superuser):
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
):
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
):
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
):
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
def get_account(account_id: uuid.UUID, session: DB, admin: Superuser):
    return account_access(session, admin, account_id)
