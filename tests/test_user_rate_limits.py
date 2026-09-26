from devfeed_user_api.rate_limits import (
    RATE_LIMIT_SCRIPT,
    RateLimitBudget,
    consume_rate_limits,
)


class RateLimitRedis:
    def __init__(self, retry_after):
        self.retry_after = retry_after
        self.calls = []

    def eval(self, *args):
        self.calls.append(args)
        return self.retry_after


def test_multiple_budgets_share_one_atomic_script_call():
    redis = RateLimitRedis(0)
    budgets = [
        RateLimitBudget("viewer:minute", 30, 60),
        RateLimitBudget("article:hour", 100, 3600),
    ]

    assert consume_rate_limits(redis, budgets) == 0
    assert redis.calls == [
        (
            RATE_LIMIT_SCRIPT,
            2,
            "viewer:minute",
            "article:hour",
            30,
            60,
            100,
            3600,
        )
    ]


def test_rate_limiter_returns_redis_retry_interval():
    redis = RateLimitRedis(42)

    assert consume_rate_limits(redis, [RateLimitBudget("viewer:minute", 30, 60)]) == 42
