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
from devfeed_core.partner_tiers import TIER_BENEFITS
from devfeed_http.partner_portal import (
    AccountInput,
    AccountOut,
    AccountsOut,
    AssetInput,
    AssetOut,
    Dashboard,
    MemberInput,
    MemberOut,
    MemberSubject,
    MetricInput,
    PartnershipTierOut,
    asset_access,
    reporting_dashboard,
    validate_product,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from devfeed_admin_api.auth import Admin, AdminIdentity, require_admin
from devfeed_admin_api.dependencies import DB

router = APIRouter(
    prefix="/v1/admin/partner-accounts",
    tags=["admin-partner-accounts"],
    dependencies=[Depends(require_admin)],
)


def require_superuser(admin: Admin) -> AdminIdentity:
    if "superuser" not in admin.roles:
        raise HTTPException(403, "Superuser access required")
    return admin


Superuser = Annotated[AdminIdentity, Depends(require_superuser)]


def account_access(session, _admin, account_id):
    account = session.scalar(select(PartnerAccount).where(PartnerAccount.id == account_id))
    if account is None:
        raise HTTPException(404, "Partner account not found")
    return account


@router.get("/tiers", response_model=list[PartnershipTierOut])
def partnership_tiers(_admin: Superuser):
    return [{"tier": tier, "benefits": list(benefits)} for tier, benefits in TIER_BENEFITS.items()]


@router.get("", response_model=AccountsOut)
def accounts(
    session: DB,
    _admin: Superuser,
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return {
        "items": session.scalars(
            select(PartnerAccount)
            .order_by(PartnerAccount.name, PartnerAccount.id)
            .limit(limit)
            .offset(offset)
        ).all(),
        "total": session.scalar(select(func.count()).select_from(PartnerAccount)),
    }


@router.get("/{account_id}/dashboard", response_model=Dashboard)
def dashboard(
    account_id: uuid.UUID,
    session: DB,
    admin: Superuser,
    days: int = Query(30, ge=1, le=365),
    limit: int = Query(100, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return reporting_dashboard(
        account_id, session, account_access(session, admin, account_id), days, limit, offset
    )


@router.post("", response_model=AccountOut, status_code=201)
def create_account(payload: AccountInput, _superuser: Superuser, session: DB):
    account = PartnerAccount(**payload.model_dump())
    session.add(account)
    session.commit()
    return account


@router.put("/{account_id}", response_model=AccountOut)
def update_account(account_id: uuid.UUID, payload: AccountInput, superuser: Superuser, session: DB):
    account = account_access(session, superuser, account_id)
    for key, value in payload.model_dump().items():
        setattr(account, key, value)
    session.commit()
    return account


@router.get("/{account_id}/members", response_model=list[MemberOut])
def members(account_id: uuid.UUID, superuser: Superuser, session: DB):
    account_access(session, superuser, account_id)
    return session.scalars(
        select(PartnerMembership)
        .where(PartnerMembership.account_id == account_id)
        .order_by(PartnerMembership.subject)
    ).all()


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
    account_access(session, superuser, account_id)
    validate_product(payload, session)
    asset = PartnerAsset(account_id=account_id, **payload.model_dump())
    session.add(asset)
    session.commit()
    return asset


@router.put("/{account_id}/assets/{asset_id}", response_model=AssetOut)
def update_asset(
    account_id: uuid.UUID,
    asset_id: uuid.UUID,
    payload: AssetInput,
    superuser: Superuser,
    session: DB,
):
    account_access(session, superuser, account_id)
    asset = asset_access(session, account_id, asset_id)
    validate_product(payload, session)
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
