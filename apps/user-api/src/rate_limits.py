"""Atomic Redis fixed-window budgets shared by user-facing endpoints."""

from dataclasses import dataclass
from typing import Protocol


class RedisEvaluator(Protocol):
    def eval(self, script: str, numkeys: int, *keys_and_args: str | int) -> int: ...


@dataclass(frozen=True)
class RateLimitBudget:
    key: str
    limit: int
    window: int


RATE_LIMIT_SCRIPT = """
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


def consume_rate_limits(redis: RedisEvaluator, budgets: list[RateLimitBudget]) -> int:
    """Charge every budget together; return the longest retry interval, or zero.

    Redis evaluates the script atomically. It checks every ceiling before it
    increments any counter, so rejection never consumes a different budget.
    """
    if not budgets:
        return 0
    retry_after = redis.eval(
        RATE_LIMIT_SCRIPT,
        len(budgets),
        *[budget.key for budget in budgets],
        *[value for budget in budgets for value in (budget.limit, budget.window)],
    )
    return max(0, int(retry_after or 0))
