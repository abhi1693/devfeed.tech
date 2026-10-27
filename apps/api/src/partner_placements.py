"""Anonymous contextual delivery; account data and credentials stay private."""

from devfeed_core.config import get_settings
from devfeed_core.partner_placements import deliver_placements
from devfeed_core.rate_limits import RateLimitBudget, consume_rate_limits
from devfeed_http.body_limits import BodyLimitedRoute, RequestBodyPolicy
from devfeed_http.partner_placements import PlacementQuery, PlacementsData, PlacementsOut
from fastapi import APIRouter, HTTPException, Request, Response
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from devfeed_api.dependencies import DB, get_rate_limit_redis
from devfeed_api.partner_callers import partner_caller_key

router = APIRouter(
    prefix="/v1/partner-placements", tags=["partner-placements"], route_class=BodyLimitedRoute
)


@router.post(
    "/query",
    response_model=PlacementsOut,
    openapi_extra=RequestBodyPolicy(body_limit=16384).openapi_extra(),
)
def placements(
    payload: PlacementQuery, request: Request, session: DB, response: Response
) -> PlacementsData:
    response.headers["Cache-Control"] = "no-store"
    key = get_settings().partner_tracking_key
    if key is None:
        return {"items": []}
    headers = {"Cache-Control": "no-store"}
    try:
        retry = consume_rate_limits(
            get_rate_limit_redis(),
            [
                RateLimitBudget(
                    f"devfeed:partner-placement-query:caller:{partner_caller_key(request)}:minute",
                    60,
                    60,
                ),
                RateLimitBudget("devfeed:partner-placement-query:minute", 3000, 60),
            ],
        )
        if retry:
            raise HTTPException(
                429, "Placement query limit reached", headers={**headers, "Retry-After": str(retry)}
            )
        items = deliver_placements(session, payload, key.get_secret_value())
        session.commit()
        return {"items": items}
    except (RedisError, SQLAlchemyError):
        session.rollback()
        raise HTTPException(
            503, "Product suggestions temporarily unavailable", headers=headers
        ) from None
