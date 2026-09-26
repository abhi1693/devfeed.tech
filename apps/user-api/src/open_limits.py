"""Bound outbound-click tracking without trusting visitor cookies as abuse-proof identities."""

from fastapi import HTTPException
from redis.exceptions import RedisError

from devfeed_user_api.dependencies import get_redis
from devfeed_user_api.rate_limits import RateLimitBudget, consume_rate_limits


def limit_open_requests(article_id, viewer_key: str, *, anonymous: bool) -> None:
    # Authenticated users have the same request budget, but do not consume the
    # shared anonymous allowance. Keys contain hashes, never identities or IPs.
    prefix = "devfeed:user:open-limits:"
    budgets = [
        RateLimitBudget(f"{prefix}viewer:{viewer_key}:minute", 30, 60),
        RateLimitBudget(f"{prefix}viewer:{viewer_key}:hour", 180, 3600),
    ]
    if anonymous:
        # Cookie rotation cannot bypass these shared ceilings. They deliberately
        # cap untrusted influence on both popularity and database write volume.
        budgets.extend(
            [
                RateLimitBudget(f"{prefix}article:{article_id}:minute", 20, 60),
                RateLimitBudget(f"{prefix}article:{article_id}:hour", 100, 3600),
                RateLimitBudget(f"{prefix}anonymous:minute", 120, 60),
                RateLimitBudget(f"{prefix}anonymous:hour", 1000, 3600),
            ]
        )
    try:
        retry_after = consume_rate_limits(get_redis(), budgets)
    except RedisError as exc:
        raise HTTPException(503, "View tracking is temporarily unavailable") from exc
    if retry_after:
        raise HTTPException(
            429,
            "Too many article opens. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
