"""One connection policy for direct Redis and Sentinel-managed Valkey/Redis."""

from typing import Any

from redis.backoff import ExponentialWithJitterBackoff
from redis.connection import SSLConnection, parse_url
from redis.retry import Retry
from redis.sentinel import Sentinel, SentinelConnectionPool

from devfeed_core.config import Settings
from redis import Redis


class _SentinelRedis(Redis):
    def close(self) -> None:
        super().close()
        # master_for owns its data pool; also release the discovery pools.
        pool = self.connection_pool
        if isinstance(pool, SentinelConnectionPool):
            for sentinel in pool.sentinel_manager.sentinels:
                sentinel.close()


def create_redis(settings: Settings, **overrides: Any) -> Redis:
    options: dict[str, Any] = {
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
        "retry": Retry(ExponentialWithJitterBackoff(base=1, cap=10), retries=3),
    }
    options.update(overrides)
    if not settings.redis_sentinel_nodes:
        return Redis.from_url(settings.redis_url, **options)

    # The URL continues to define the logical DB, data credentials and TLS.
    # Only Sentinel may supply the writable primary's address.
    options.update(parse_url(settings.redis_url))
    options.pop("host", None)
    options.pop("port", None)
    if options.pop("connection_class", None) is SSLConnection:
        options["ssl"] = True
    discovery: dict[str, Any] = {
        "socket_connect_timeout": options["socket_connect_timeout"],
        "socket_timeout": options["socket_timeout"],
        "retry": options["retry"],
        "ssl": settings.redis_sentinel_ssl,
    }
    if settings.redis_sentinel_username:
        discovery["username"] = settings.redis_sentinel_username
    if settings.redis_sentinel_password:
        discovery["password"] = settings.redis_sentinel_password.get_secret_value()
    sentinel = Sentinel(settings.redis_sentinel_nodes, sentinel_kwargs=discovery)
    return sentinel.master_for(
        settings.redis_sentinel_master, redis_class=_SentinelRedis, **options
    )
