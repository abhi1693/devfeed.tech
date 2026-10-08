"""Read-only reporting, scoped to live account memberships for every portal identity."""

import uuid

from devfeed_core.models import PartnerAccount, PartnerMembership
from devfeed_http.partner_portal import AccountsOut, Dashboard, reporting_dashboard
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
):
    return reporting_dashboard(
        account_id, session, account_access(session, partner, account_id), days, limit, offset
    )
