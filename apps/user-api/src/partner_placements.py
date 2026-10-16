"""Private product selection from the authenticated reader's prepared interests."""

from devfeed_core.config import get_settings
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from devfeed_http.body_limits import BodyLimitedRoute
from devfeed_http.operation_policies import ExtensionTransportPolicy, ReaderGatewayPolicy
from devfeed_http.partner_placements import PlacementQuery, PlacementsOut
from fastapi import APIRouter, HTTPException, Response
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_user_api.auth import BrowserUser
from devfeed_user_api.dependencies import DB, get_redis

router = APIRouter(
    prefix="/v1/user/partner-placements", tags=["personalization"], route_class=BodyLimitedRoute
)


@router.post(
    "/query",
    response_model=PlacementsOut,
    openapi_extra=ReaderGatewayPolicy(
        body_limit=16384,
        body_timeout_ms=2000,
        handler="placement-query",
        extension=ExtensionTransportPolicy(),
    ).bounded_openapi_extra(),
)
def placements(payload: PlacementQuery, user: BrowserUser, session: DB, response: Response):
    response.headers["Cache-Control"] = "no-store"
    key = get_settings().partner_tracking_key
    if key is None:
        return {"items": []}
    headers = {"Cache-Control": "no-store"}
    try:
        retry = consume_rate_limits(
            get_redis(),
            [
                RateLimitBudget(f"devfeed:user:{user.user_id}:partner-placements:minute", 60, 60),
                RateLimitBudget("devfeed:partner-placement-query:minute", 3000, 60),
            ],
        )
        if retry:
            raise HTTPException(
                429, "Placement query limit reached", headers={**headers, "Retry-After": str(retry)}
            )
        items = deliver_placements(session, payload, key.get_secret_value(), user_id=user.user_id)
        session.commit()
        return {"items": items}
    except (RedisError, SQLAlchemyError):
        session.rollback()
        raise HTTPException(
            503, "Product suggestions temporarily unavailable", headers=headers
        ) from None
