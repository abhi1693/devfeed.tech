"""Receipt-authorized anonymous partner impression and click collection."""

from devfeed_core.config import get_settings
from devfeed_core.partner_tracking import (
    EventKind,
    InactivePartnerAsset,
    PartnerTrackingTokens,
    record_delivery_event,
)
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_api.dependencies import DB, get_rate_limit_redis

router = APIRouter(prefix="/v1/partner-tracking", tags=["partner-tracking"])


class PartnerEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt: str = Field(min_length=1, max_length=256)
    kind: EventKind


@router.post("/events", status_code=204, response_class=Response)
def track_event(body: PartnerEvent, session: DB):
    headers = {"Cache-Control": "no-store"}
    key = get_settings().partner_tracking_key
    receipt = PartnerTrackingTokens(key.get_secret_value()).verify(body.receipt) if key else None
    if receipt is None:
        raise HTTPException(403, "Invalid partner delivery receipt", headers=headers)
    try:
        retry = consume_rate_limits(
            get_rate_limit_redis(),
            [
                RateLimitBudget("devfeed:partner-events:minute", 3000, 60),
                RateLimitBudget(f"devfeed:partner-events:{receipt.asset_id}:minute", 1000, 60),
            ],
        )
    except RedisError:
        raise HTTPException(503, "Partner tracking unavailable", headers=headers) from None
    if retry:
        raise HTTPException(
            429, "Partner event limit reached", headers={**headers, "Retry-After": str(retry)}
        )
    try:
        record_delivery_event(session, receipt, body.kind)
        session.commit()
    except InactivePartnerAsset:
        raise HTTPException(403, "Partner placement is inactive", headers=headers) from None
    except SQLAlchemyError:
        session.rollback()
        raise HTTPException(503, "Partner tracking unavailable", headers=headers) from None
    return Response(status_code=204, headers=headers)
