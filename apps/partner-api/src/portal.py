"""Reporting and limited marketing preferences scoped to live account memberships."""

import uuid
from typing import Annotated

from devfeed_core.models import PartnerAccount
from devfeed_core.partner_ad_formats import AdFormat, validate_account_formats
from devfeed_core.partner_audiences import (
    AudienceDimension,
    audience_topic_options,
    validate_audience,
)
from devfeed_core.partner_promotions import request_promotion_enrollment
from devfeed_core.text_search import text_search
from devfeed_http.body_limits import BodyLimitedRoute
from devfeed_http.image_uploads import limit_image_upload
from devfeed_http.operation_policies import GatewayPolicy
from devfeed_http.partner_ad_images import (
    save_product_promotion_with_images,
    upload_product_ad_image,
)
from devfeed_http.partner_portal import (
    AccountOut,
    AccountsOut,
    AssetSort,
    AudienceTopicsOut,
    CreativePreviewOut,
    Dashboard,
    DestinationPreviewOut,
    InventoryOut,
    PartnerPreferencesInput,
    ProductPromotionInput,
    ProductPromotionOut,
    SortOrder,
    creative_preview,
    destination_preview,
    inventory_products,
    product_promotion,
    reporting_dashboard,
)
from devfeed_http.partner_products import (
    ManualProductInput,
    ManualProductUpdate,
    ProductDetailOut,
    archive_manual_product,
    create_manual_product,
    limit_product_write,
    product_detail,
    update_manual_product,
)
from fastapi import APIRouter, Depends, File, HTTPException, Query, Response, UploadFile
from sqlalchemy import func, select

from devfeed_partner_api.access import PartnerAccountAccess
from devfeed_partner_api.auth import Partner, require_partner
from devfeed_partner_api.dependencies import DB, get_redis

router = APIRouter(
    prefix="/v1/partner",
    tags=["partner"],
    dependencies=[Depends(require_partner)],
    route_class=BodyLimitedRoute,
)


@router.post(
    "/accounts/{account_id}/products",
    response_model=ProductDetailOut,
    status_code=201,
    operation_id="partnerProductCreate",
    openapi_extra=GatewayPolicy(body_limit=65536).bounded_openapi_extra(),
)
def create_product(
    account_id: uuid.UUID, payload: ManualProductInput, session: DB, partner: Partner
):
    access = PartnerAccountAccess(session, partner)
    access.require(account_id)
    limit_product_write(get_redis(), partner, account_id)
    account, existing = access.lock_manual(account_id, url=payload.product_url)
    result = create_manual_product(session, account, payload, existing)
    session.commit()
    return result


@router.get(
    "/accounts/{account_id}/products/{product_id:uuid}",
    response_model=ProductDetailOut,
    operation_id="partnerProductGet",
)
def get_product(account_id: uuid.UUID, product_id: uuid.UUID, session: DB, partner: Partner):
    return product_detail(
        session, PartnerAccountAccess(session, partner).require(account_id), product_id
    )


@router.patch(
    "/accounts/{account_id}/products/{product_id:uuid}",
    response_model=ProductDetailOut,
    operation_id="partnerProductUpdate",
    openapi_extra=GatewayPolicy(body_limit=65536).bounded_openapi_extra(),
)
def update_product(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: ManualProductUpdate,
    session: DB,
    partner: Partner,
):
    access = PartnerAccountAccess(session, partner)
    access.require(account_id)
    limit_product_write(get_redis(), partner, account_id)
    account, product = access.lock_manual(account_id, product_id=product_id)
    result = update_manual_product(session, account, product, payload)
    session.commit()
    return result


@router.delete(
    "/accounts/{account_id}/products/{product_id:uuid}",
    status_code=204,
    response_class=Response,
    operation_id="partnerProductArchive",
    openapi_extra=GatewayPolicy(body_limit=65536).openapi_extra(),
)
def archive_product(account_id: uuid.UUID, product_id: uuid.UUID, session: DB, partner: Partner):
    access = PartnerAccountAccess(session, partner)
    access.require(account_id)
    limit_product_write(get_redis(), partner, account_id)
    account, product = access.lock_manual(account_id, product_id=product_id)
    archive_manual_product(session, account, product)
    session.commit()
    return Response(status_code=204)


@router.get("/accounts", response_model=AccountsOut, operation_id="partnerAccountsList")
def accounts(
    session: DB,
    partner: Partner,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
):
    query = PartnerAccountAccess(session, partner).query()
    if q.strip():
        query = query.where(text_search(q.strip(), PartnerAccount.name))
    return {
        "items": session.scalars(
            query.order_by(PartnerAccount.name, PartnerAccount.id).limit(limit).offset(offset)
        ).all(),
        "total": session.scalar(select(func.count()).select_from(query.subquery())),
    }


@router.get("/accounts/{account_id}", response_model=AccountOut, operation_id="partnerAccountGet")
def get_account(account_id: uuid.UUID, session: DB, partner: Partner):
    return PartnerAccountAccess(session, partner).require(account_id)


@router.get("/accounts/{account_id}/dashboard", response_model=Dashboard)
def dashboard(
    account_id: uuid.UUID,
    session: DB,
    partner: Partner,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
    sort: AssetSort = Query("name"),
    order: SortOrder = Query("asc"),
    ad_format: AdFormat | None = None,
):
    return reporting_dashboard(
        account_id,
        session,
        PartnerAccountAccess(session, partner).require(account_id),
        days,
        limit,
        offset,
        sort=sort,
        order=order,
        ad_format=ad_format,
    )


@router.get(
    "/accounts/{account_id}/destination-preview",
    response_model=DestinationPreviewOut,
    operation_id="partnerDestinationPreview",
)
def preview_destination(account_id: uuid.UUID, session: DB, partner: Partner):
    return destination_preview(session, PartnerAccountAccess(session, partner).require(account_id))


@router.put(
    "/accounts/{account_id}/preferences",
    response_model=AccountOut,
    operation_id="partnerAccountPreferencesUpdate",
)
def update_preferences(
    account_id: uuid.UUID, payload: PartnerPreferencesInput, session: DB, partner: Partner
):
    account = PartnerAccountAccess(session, partner).lock(account_id)
    if payload.audience is not None:
        try:
            validate_audience(session, account.tier, payload.audience, previous=account.audience)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        account.audience = payload.audience.model_dump(mode="json")
    if payload.outbound_destination is not None:
        account.outbound_destination = payload.outbound_destination
    if payload.ad_formats is not None:
        try:
            validate_account_formats(account.tier, payload.ad_formats, previous=account.ad_formats)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None
        account.ad_formats = payload.ad_formats
    session.flush()
    request_promotion_enrollment(session, account.id)
    session.commit()
    return account


@router.get(
    "/accounts/{account_id}/audience-topics",
    response_model=AudienceTopicsOut,
    operation_id="partnerAudienceTopics",
)
def audience_topics(
    account_id: uuid.UUID,
    session: DB,
    partner: Partner,
    dimension: AudienceDimension,
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    topic_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(25, ge=1, le=100),
):
    account = PartnerAccountAccess(session, partner).require(account_id)
    return audience_topic_options(
        session, dimension, account=account, q=q, topic_id=topic_id, offset=offset, limit=limit
    )


@router.get(
    "/accounts/{account_id}/creative-preview",
    response_model=CreativePreviewOut,
    operation_id="partnerCreativePreview",
)
def preview_creative(
    account_id: uuid.UUID, session: DB, partner: Partner, product_id: uuid.UUID | None = None
):
    return creative_preview(
        session, PartnerAccountAccess(session, partner).require(account_id), product_id=product_id
    )


@router.get(
    "/accounts/{account_id}/inventory",
    response_model=InventoryOut,
    operation_id="partnerInventoryList",
)
def inventory(
    account_id: uuid.UUID,
    session: DB,
    partner: Partner,
    q: str = Query("", max_length=200, pattern=r"^[^\x00]*$"),
    product_id: uuid.UUID | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=200),
    include_archived: bool = False,
):
    return inventory_products(
        session,
        PartnerAccountAccess(session, partner).require(account_id),
        q=q,
        product_id=product_id,
        offset=offset,
        limit=limit,
        include_archived=include_archived,
    )


@router.get(
    "/accounts/{account_id}/products/{product_id}/promotion",
    response_model=ProductPromotionOut,
    operation_id="partnerProductPromotionGet",
)
def get_product_promotion(
    account_id: uuid.UUID, product_id: uuid.UUID, session: DB, partner: Partner
):
    return product_promotion(
        session, PartnerAccountAccess(session, partner).require(account_id), product_id
    )


@router.put(
    "/accounts/{account_id}/products/{product_id}/promotion",
    response_model=ProductPromotionOut,
    operation_id="partnerProductPromotionUpdate",
)
def update_product_promotion(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    payload: ProductPromotionInput,
    session: DB,
    partner: Partner,
):
    account = PartnerAccountAccess(session, partner).lock_product(account_id, product_id)
    return save_product_promotion_with_images(session, account, product_id, payload)


@router.post(
    "/accounts/{account_id}/products/{product_id}/ad-image",
    response_model=ProductPromotionOut,
    operation_id="partnerProductAdImageUpload",
    openapi_extra=GatewayPolicy(
        body_limit=5 * 1024 * 1024 + 65536, body_timeout_ms=10_000
    ).bounded_openapi_extra(rejection="image"),
)
def upload_ad_image(
    account_id: uuid.UUID,
    product_id: uuid.UUID,
    file: Annotated[UploadFile, File(description="JPEG, PNG or WebP, up to 5 MB")],
    session: DB,
    partner: Partner,
):
    access = PartnerAccountAccess(session, partner)
    access.require(account_id)
    limit_image_upload(get_redis(), partner, account_id)
    account = access.lock_product(account_id, product_id)
    return upload_product_ad_image(session, account, product_id, file)
