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


_BUDGET_SCRIPT = """
local function consume_budgets(count)
    local retry_after = 0
    for i = 1, count do
        local key = KEYS[i]
        if tonumber(redis.call('GET', key) or '0') >= tonumber(ARGV[2*i-1]) then
            retry_after = math.max(retry_after, redis.call('TTL', key), 1)
        end
    end
    if retry_after > 0 then return retry_after end
    for i = 1, count do
        local key = KEYS[i]
        local current = redis.call('INCR', key)
        if current == 1 then redis.call('EXPIRE', key, ARGV[2*i]) end
    end
    return 0
end
"""

RATE_LIMIT_SCRIPT = _BUDGET_SCRIPT + "\nreturn consume_budgets(#KEYS)"

RESERVED_RATE_LIMIT_SCRIPT = (
    _BUDGET_SCRIPT
    + """
local reservation = KEYS[#KEYS]
if redis.call('EXISTS', reservation) == 1 then return 0 end
local retry_after = consume_budgets(#KEYS - 1)
if retry_after == 0 then
    redis.call('SET', reservation, '1', 'EX', ARGV[#ARGV])
end
return retry_after
"""
)


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


def consume_reserved_rate_limits(
    redis: RedisEvaluator,
    budgets: list[RateLimitBudget],
    *,
    reservation_key: str,
    reservation_ttl: int,
) -> int:
    """Reserve capacity once across retries of the same database event.

    A reservation only remembers the quota charge. Callers must still claim and
    commit the event in their database; a rolled-back transaction remains retriable.
    """
    if reservation_ttl <= 0:
        raise ValueError("A quota reservation must have a positive lifetime")
    if not budgets:
        return 0
    retry_after = redis.eval(
        RESERVED_RATE_LIMIT_SCRIPT,
        len(budgets) + 1,
        *[budget.key for budget in budgets],
        reservation_key,
        *[value for budget in budgets for value in (budget.limit, budget.window)],
        reservation_ttl,
    )
    return max(0, int(retry_after or 0))
