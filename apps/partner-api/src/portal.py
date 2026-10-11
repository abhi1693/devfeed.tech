"""Reporting and limited marketing preferences scoped to live account memberships."""

import uuid

from devfeed_core.models import PartnerAccount, PartnerMembership
from devfeed_http.partner_portal import (
    AccountOut,
    AccountsOut,
    AssetSort,
    Dashboard,
    DestinationPreviewOut,
    PartnerPreferencesInput,
    SortOrder,
    destination_preview,
    reporting_dashboard,
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
    # edit commercial trust, tier, campaign targeting or another account's setting.
    account = session.scalar(
        visible_accounts(partner)
        .where(PartnerAccount.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise HTTPException(404, "Partner account not found")
    account.outbound_destination = payload.outbound_destination
    session.commit()
    return account
