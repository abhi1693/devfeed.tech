"""Quota reservations retain the same identity across database retries."""

from unittest.mock import Mock

import pytest
from devfeed_core.rate_limits import (
    RESERVED_RATE_LIMIT_SCRIPT,
    RateLimitBudget,
    consume_reserved_rate_limits,
)


@pytest.mark.parametrize("retry", [0, 42])
def test_reservation_and_budgets_share_one_atomic_redis_operation(retry):
    redis = Mock()
    redis.eval.return_value = retry
    assert (
        consume_reserved_rate_limits(
            redis,
            [RateLimitBudget("global", 3000, 60), RateLimitBudget("asset", 1000, 60)],
            reservation_key="delivery:impression",
            reservation_ttl=3600,
        )
        == retry
    )
    redis.eval.assert_called_once_with(
        RESERVED_RATE_LIMIT_SCRIPT,
        3,
        "global",
        "asset",
        "delivery:impression",
        3000,
        60,
        1000,
        60,
        3600,
    )


def test_empty_reservation_budgets_do_not_contact_redis():
    redis = Mock()
    assert (
        consume_reserved_rate_limits(redis, [], reservation_key="event", reservation_ttl=3600) == 0
    )
    redis.eval.assert_not_called()


def test_reservations_require_a_positive_lifetime():
    redis = Mock()
    with pytest.raises(ValueError, match="positive lifetime"):
        consume_reserved_rate_limits(
            redis, [RateLimitBudget("global", 1, 60)], reservation_key="event", reservation_ttl=0
        )
    redis.eval.assert_not_called()
