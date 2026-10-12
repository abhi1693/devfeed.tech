"""Reporting and limited marketing preferences scoped to live account memberships."""

import uuid

from devfeed_core.models import PartnerAccount, PartnerMembership
from devfeed_core.partner_ad_formats import AdFormat, validate_account_formats
from devfeed_core.partner_audiences import (
    AudienceDimension,
    audience_topic_options,
    validate_audience,
)
from devfeed_core.partner_promotions import request_promotion_enrollment
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
    save_product_promotion,
)
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from devfeed_partner_api.auth import Partner, PartnerIdentity, require_partner
from devfeed_partner_api.dependencies import DB

router = APIRouter(prefix="/v1/partner", tags=["partner"], dependencies=[Depends(require_partner)])


def visible_accounts(partner: PartnerIdentity):
    return select(PartnerAccount).where(
        PartnerAccount.status == "active",
        select(PartnerMembership.account_id)
        .where(
            PartnerMembership.account_id == PartnerAccount.id,
            PartnerMembership.issuer == partner.issuer,
            PartnerMembership.subject == partner.subject,
        )
        .exists(),
    )


def account_access(session, partner: PartnerIdentity, account_id: uuid.UUID) -> PartnerAccount:
    account = session.scalar(visible_accounts(partner).where(PartnerAccount.id == account_id))
    if account is None:
        raise HTTPException(404, "Partner account not found")
    return account


@router.get("/accounts", response_model=AccountsOut)
def accounts(
    session: DB,
    partner: Partner,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    query = visible_accounts(partner)
    return {
        "items": session.scalars(
            query.order_by(PartnerAccount.name, PartnerAccount.id).limit(limit).offset(offset)
        ).all(),
        "total": session.scalar(select(func.count()).select_from(query.subquery())),
    }


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
        account_access(session, partner, account_id),
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
    return destination_preview(session, account_access(session, partner, account_id))


@router.put(
    "/accounts/{account_id}/preferences",
    response_model=AccountOut,
    operation_id="partnerAccountPreferencesUpdate",
)
def update_preferences(
    account_id: uuid.UUID, payload: PartnerPreferencesInput, session: DB, partner: Partner
):
    # Recheck immutable membership while locking the account; portal members cannot
    # edit commercial trust, tier, product targeting or another account's setting.
    account = session.scalar(
        visible_accounts(partner)
        .where(PartnerAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise HTTPException(404, "Partner account not found")
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
    account = account_access(session, partner, account_id)
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
        session, account_access(session, partner, account_id), product_id=product_id
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
):
    return inventory_products(
        session,
        account_access(session, partner, account_id),
        q=q,
        product_id=product_id,
        offset=offset,
        limit=limit,
    )


@router.get(
    "/accounts/{account_id}/products/{product_id}/promotion",
    response_model=ProductPromotionOut,
    operation_id="partnerProductPromotionGet",
)
def get_product_promotion(
    account_id: uuid.UUID, product_id: uuid.UUID, session: DB, partner: Partner
):
    return product_promotion(session, account_access(session, partner, account_id), product_id)


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
    account = session.scalar(
        visible_accounts(partner)
        .where(PartnerAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise HTTPException(404, "Partner account not found")
    result = save_product_promotion(session, account, product_id, payload)
    session.commit()
    return result
