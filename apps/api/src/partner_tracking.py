"""Receipt-authorized anonymous partner impression and click collection."""

from devfeed_core.analytics import AnalyticsUnavailable
from devfeed_core.analytics_direct_engagement import DirectEngagementAnalytics
from devfeed_core.config import get_settings
from devfeed_core.partner_claim_backfill import LEGACY_CLAIM_AT
from devfeed_core.partner_tracking import (
    RECEIPT_TTL,
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
from devfeed_http.analytics import analytics_client
from devfeed_http.body_limits import BodyLimitedRoute, RequestBodyPolicy
from devfeed_http.dependencies import run_database
from devfeed_http.partner_analytics_reporting import require_partner_claims
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_api.dependencies import DBFactory, get_rate_limit_redis
from devfeed_api.partner_callers import partner_caller_key

router = APIRouter(
    prefix="/v1/partner-tracking", tags=["partner-tracking"], route_class=BodyLimitedRoute
)


class PartnerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt: str = Field(min_length=1, max_length=256)
    kind: EventKind


def _claim_event(session, receipt, kind, caller):
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
async def track_event(body: PartnerEvent, request: Request, factory: DBFactory):
    headers = {"Cache-Control": "no-store"}
    key = get_settings().partner_tracking_key
    receipt = PartnerTrackingTokens(key.get_secret_value()).verify(body.receipt) if key else None
    if receipt is None:
        raise HTTPException(403, "Invalid partner delivery receipt", headers=headers)
    client = analytics_client(request)
    try:
        await require_partner_claims(client)
    except AnalyticsUnavailable:
        raise HTTPException(
            503, "Partner tracking unavailable", headers={**headers, "Retry-After": "1"}
        ) from None
    claim = await run_database(
        factory, _claim_event, receipt, body.kind, partner_caller_key(request)
    )
    # TODO(REMOVE_AFTER_CLICKHOUSE_BACKFILL): Old receipt claims already belong
    # to the imported baseline; their temporary sentinel prevents double count.
    if claim.recorded_at != LEGACY_CLAIM_AT:
        try:
            await DirectEngagementAnalytics(client).partner_events(
                [
                    {
                        "asset_id": receipt.asset_id,
                        "delivery_id": receipt.delivery_id,
                        "kind": body.kind,
                        "day": receipt.issued_at.date(),
                        "observed_at": claim.recorded_at,
                    }
                ]
            )
        except AnalyticsUnavailable:
            raise HTTPException(
                503, "Partner tracking unavailable", headers={**headers, "Retry-After": "1"}
            ) from None
    return Response(status_code=204, headers=headers)
