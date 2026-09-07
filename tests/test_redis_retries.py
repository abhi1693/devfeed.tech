"""Dependency upgrades must not silently lengthen Redis outage handling."""

import pytest
from devfeed_admin_api.dependencies import get_redis as admin_redis
from devfeed_aggregator.queue import get_queue
from devfeed_api.dependencies import get_redis as public_redis
from redis.exceptions import ConnectionError, TimeoutError


@pytest.mark.parametrize("component", ["public", "admin", "queue"])
@pytest.mark.parametrize("error_type", [ConnectionError, TimeoutError])
def test_redis_outages_keep_the_existing_retry_budget(monkeypatch, component, error_type):
    public_redis.cache_clear()
    admin_redis.cache_clear()
    clients = {
        "public": public_redis,
        "admin": admin_redis,
        "queue": lambda: get_queue().connection,
    }
    client = clients[component]()
    attempts = []
    delays = []
    monkeypatch.setattr("redis.retry.sleep", delays.append)

    def unavailable():
        attempts.append(None)
        raise error_type("Simulated Redis outage")

    try:
        policy = client.connection_pool.connection_kwargs["retry"]
        with pytest.raises(error_type):
            policy.call_with_retry(unavailable, lambda error: None)
        # One initial attempt plus three retries, each with at most 10s backoff.
        assert len(attempts) == 4
        assert len(delays) <= 3
        assert all(0 <= delay <= 10 for delay in delays)
    finally:
        client.close()
        public_redis.cache_clear()
        admin_redis.cache_clear()
