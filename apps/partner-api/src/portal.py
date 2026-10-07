"""Membership-scoped partner reporting and superuser account administration."""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from devfeed_core.models import (
    PartnerAccount,
    PartnerAsset,
    PartnerDailyMetric,
    PartnerMembership,
    PartnerProduct,
    PartnerTrackedDailyMetric,
)
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import func, select, union_all
from sqlalchemy.dialects.postgresql import insert

from devfeed_partner_api.auth import Partner, PartnerIdentity, require_partner
from devfeed_partner_api.dependencies import DB

router = APIRouter(prefix="/v1/partner", tags=["partner"], dependencies=[Depends(require_partner)])
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


def require_superuser(partner: Partner) -> PartnerIdentity:
    if "superuser" not in partner.roles:
        raise HTTPException(403, "Superuser access required")
    return partner


Superuser = Annotated[PartnerIdentity, Depends(require_superuser)]


def visible_accounts(partner: PartnerIdentity):
    query = select(PartnerAccount)
    if "superuser" not in partner.roles:
        query = query.where(
            PartnerAccount.status == "active",
            select(PartnerMembership.account_id)
            .where(
                PartnerMembership.account_id == PartnerAccount.id,
                PartnerMembership.issuer == partner.issuer,
                PartnerMembership.subject == partner.subject,
            )
            .exists(),
        )
    return query


def account_access(session, partner: PartnerIdentity, account_id: uuid.UUID) -> PartnerAccount:
    # Resolve membership on every request, never from cookies or a caller's selected account.
    account = session.scalar(visible_accounts(partner).where(PartnerAccount.id == account_id))
    if account is None:
        raise HTTPException(404, "Partner account not found")
    return account


class AccountInput(BaseModel):
    name: Name
    tier: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    benefits: list[Name] = Field(default_factory=list, max_length=50)
    status: Literal["active", "paused"] = "active"


class AccountOut(AccountInput):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID


class AccountsOut(BaseModel):
    items: list[AccountOut]
    total: int


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


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(payload: AccountInput, _superuser: Superuser, session: DB):
    account = PartnerAccount(**payload.model_dump())
    session.add(account)
    session.commit()
    return account


@router.put("/accounts/{account_id}", response_model=AccountOut)
def update_account(account_id: uuid.UUID, payload: AccountInput, superuser: Superuser, session: DB):
    account = account_access(session, superuser, account_id)
    for key, value in payload.model_dump().items():
        setattr(account, key, value)
    session.commit()
    return account


MemberSubject = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True, min_length=1, max_length=200, pattern=r"^[A-Za-z0-9_-]+$"
    ),
]


class MemberInput(BaseModel):
    subject: MemberSubject


class MemberOut(MemberInput):
    model_config = ConfigDict(from_attributes=True)
    issuer: str


@router.get("/accounts/{account_id}/members", response_model=list[MemberOut])
def members(account_id: uuid.UUID, superuser: Superuser, session: DB):
    account_access(session, superuser, account_id)
    return session.scalars(
        select(PartnerMembership)
        .where(PartnerMembership.account_id == account_id)
        .order_by(PartnerMembership.subject)
    ).all()


@router.put("/accounts/{account_id}/members", status_code=204, response_class=Response)
def add_member(account_id: uuid.UUID, payload: MemberInput, superuser: Superuser, session: DB):
    account_access(session, superuser, account_id)
    # Membership always uses this portal's configured, validated issuer.
    session.execute(
        insert(PartnerMembership)
        .values(account_id=account_id, issuer=superuser.issuer, subject=payload.subject)
        .on_conflict_do_nothing()
    )
    session.commit()
    return Response(status_code=204)


@router.delete("/accounts/{account_id}/members/{subject}", status_code=204, response_class=Response)
def remove_member(account_id: uuid.UUID, subject: MemberSubject, superuser: Superuser, session: DB):
    account_access(session, superuser, account_id)
    member = session.get(PartnerMembership, (account_id, superuser.issuer, subject))
    if member is not None:
        session.delete(member)
        session.commit()
    return Response(status_code=204)


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


def validate_product(payload: AssetInput, session):
    if payload.product_id is not None and session.get(PartnerProduct, payload.product_id) is None:
        raise HTTPException(422, "Catalog product not found")


@router.post("/accounts/{account_id}/assets", response_model=AssetOut, status_code=201)
def create_asset(account_id: uuid.UUID, payload: AssetInput, superuser: Superuser, session: DB):
    account_access(session, superuser, account_id)
    validate_product(payload, session)
    asset = PartnerAsset(account_id=account_id, **payload.model_dump())
    session.add(asset)
    session.commit()
    return asset


def asset_access(session, account_id, asset_id):
    asset = session.scalar(
        select(PartnerAsset).where(
            PartnerAsset.id == asset_id, PartnerAsset.account_id == account_id
        )
    )
    if asset is None:
        raise HTTPException(404, "Partner asset not found")
    return asset


@router.put("/accounts/{account_id}/assets/{asset_id}", response_model=AssetOut)
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


class MetricInput(BaseModel):
    day: date
    impressions: int = Field(ge=0, le=2**63 - 1)
    clicks: int = Field(ge=0, le=2**63 - 1)

    @model_validator(mode="after")
    def valid_day(self):
        if self.day > datetime.now(UTC).date():
            raise ValueError("Measurement day cannot be in the future")
        return self


@router.put(
    "/accounts/{account_id}/assets/{asset_id}/metrics", status_code=204, response_class=Response
)
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


def totals(impressions, clicks, measured_days):
    impressions, clicks = int(impressions or 0), int(clicks or 0)
    return {
        "impressions": impressions,
        "clicks": clicks,
        "ctr": clicks / impressions * 100 if impressions else None,
        "measured_days": int(measured_days or 0),
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
    account = account_access(session, partner, account_id)
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
