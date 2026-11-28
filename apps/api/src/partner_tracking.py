"""Receipt-authorized anonymous partner impression and click collection."""

from __future__ import annotations

from devfeed_core.config import get_settings
from devfeed_core.partner_claim_backfill import LEGACY_CLAIM_AT
from devfeed_core.partner_tracking import (
    RECEIPT_TTL,
    DeliveryClaim,
    DeliveryReceipt,
    EventKind,
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from devfeed_core.rate_limits import (
    RateLimitBudget,
    consume_rate_limits,
    consume_reserved_rate_limits,
)
from devfeed_http.body_limits import BodyLimitedRoute, RequestBodyPolicy
from devfeed_http.dependencies import run_database
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from devfeed_api.dependencies import DBFactory, get_rate_limit_redis
from devfeed_api.partner_callers import partner_caller_key

router = APIRouter(
    prefix="/v1/partner-tracking", tags=["partner-tracking"], route_class=BodyLimitedRoute
)


class PartnerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt: str = Field(min_length=1, max_length=256)
    kind: EventKind


def _claim_event(
    session: Session, receipt: DeliveryReceipt, kind: EventKind, caller: str
) -> DeliveryClaim:
    """Validate and commit the short-lived operational claim off the event loop."""
    headers = {"Cache-Control": "no-store"}
    try:
        redis = get_rate_limit_redis()
        retry = consume_rate_limits(
            redis,
            [RateLimitBudget(f"devfeed:partner-events:caller:{caller}:{kind}:minute", 180, 60)],
        )
    except RedisError:
        raise HTTPException(503, "Partner tracking unavailable", headers=headers) from None
    if retry:
        raise HTTPException(
            429, "Partner event limit reached", headers={**headers, "Retry-After": str(retry)}
        )
    try:
        claim = record_delivery_event(session, receipt, kind)
        if claim:
            retry = consume_reserved_rate_limits(
                redis,
                [
                    RateLimitBudget("devfeed:partner-events:minute", 3000, 60),
                    RateLimitBudget(f"devfeed:partner-events:{receipt.asset_id}:minute", 1000, 60),
                ],
                reservation_key=(
                    f"devfeed:partner-event-budget:{receipt.asset_id}:{receipt.delivery_id}:{kind}"
                ),
                reservation_ttl=RECEIPT_TTL,
            )
            if retry:
                session.rollback()
                raise HTTPException(
                    429,
                    "Partner event limit reached",
                    headers={**headers, "Retry-After": str(retry)},
                )
        if claim.recorded_at != LEGACY_CLAIM_AT:
            session.execute(
                text("""
                INSERT INTO partner_observations
                    (asset_id,kind,identity,day,revision,impressions,clicks,updated_at)
                VALUES (:asset,'partner_event',:identity,:day,0,:impressions,:clicks,:stamp)
                ON CONFLICT (asset_id,kind,identity) DO NOTHING
            """),
                {
                    "asset": receipt.asset_id,
                    "identity": f"{receipt.delivery_id}:{kind}",
                    "day": receipt.issued_at.date(),
                    "impressions": int(kind == "impression"),
                    "clicks": int(kind == "click"),
                    "stamp": claim.recorded_at,
                },
            )
        session.commit()
        return claim
    except InactivePartnerAsset:
        raise HTTPException(403, "Partner placement is inactive", headers=headers) from None
    except (RedisError, SQLAlchemyError):
        session.rollback()
        raise HTTPException(503, "Partner tracking unavailable", headers=headers) from None


@router.post(
    "/events",
    status_code=204,
    response_class=Response,
    openapi_extra=RequestBodyPolicy(body_limit=2048).openapi_extra(),
)
async def track_event(body: PartnerEvent, request: Request, factory: DBFactory) -> Response:
    headers = {"Cache-Control": "no-store"}
    key = get_settings().partner_tracking_key
    receipt = PartnerTrackingTokens(key.get_secret_value()).verify(body.receipt) if key else None
    if receipt is None:
        raise HTTPException(403, "Invalid partner delivery receipt", headers=headers)
    await run_database(factory, _claim_event, receipt, body.kind, partner_caller_key(request))
    return Response(status_code=204, headers=headers)
