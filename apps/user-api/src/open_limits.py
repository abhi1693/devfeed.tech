"""Bound outbound-click tracking without trusting visitor cookies as abuse-proof identities."""

from fastapi import HTTPException
from redis.exceptions import RedisError

from devfeed_user_api.dependencies import get_redis

# Check every budget before charging any of them. A throttled visitor cannot
# exhaust the article/global budget through repeated rejected requests.
LIMIT_SCRIPT = """
local retry_after = 0
for i, key in ipairs(KEYS) do
    if tonumber(redis.call('GET', key) or '0') >= tonumber(ARGV[2*i-1]) then
        retry_after = math.max(retry_after, redis.call('TTL', key), 1)
    end
end
if retry_after > 0 then return retry_after end
for i, key in ipairs(KEYS) do
    local count = redis.call('INCR', key)
    if count == 1 then redis.call('EXPIRE', key, ARGV[2*i]) end
end
return 0
"""


def limit_open_requests(article_id, viewer_key: str, *, anonymous: bool) -> None:
    # Authenticated users have the same request budget, but do not consume the
    # shared anonymous allowance. Keys contain hashes, never identities or IPs.
    prefix = "devfeed:user:open-limits:"
    budgets = [
        (f"{prefix}viewer:{viewer_key}:minute", 30, 60),
        (f"{prefix}viewer:{viewer_key}:hour", 180, 3600),
    ]
    if anonymous:
        # Cookie rotation cannot bypass these shared ceilings. They deliberately
        # cap untrusted influence on both popularity and database write volume.
        budgets.extend(
            [
                (f"{prefix}article:{article_id}:minute", 20, 60),
                (f"{prefix}article:{article_id}:hour", 100, 3600),
                (f"{prefix}anonymous:minute", 120, 60),
                (f"{prefix}anonymous:hour", 1000, 3600),
            ]
        )
    try:
        retry_after = get_redis().eval(
            LIMIT_SCRIPT,
            len(budgets),
            *[key for key, _, _ in budgets],
            *[value for _, limit, window in budgets for value in (limit, window)],
        )
    except RedisError as exc:
        raise HTTPException(503, "View tracking is temporarily unavailable") from exc
    if retry_after:
        raise HTTPException(
            429,
            "Too many article opens. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )
