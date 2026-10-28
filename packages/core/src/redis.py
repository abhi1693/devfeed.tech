"""One connection policy for direct Redis and Sentinel-managed Valkey/Redis."""

from collections.abc import Mapping
from typing import Any, cast

from redis.asyncio import Redis as AsyncRedis
from redis.asyncio.retry import Retry as AsyncRetry
from redis.asyncio.sentinel import Sentinel as AsyncSentinel
from redis.asyncio.sentinel import SentinelConnectionPool as AsyncSentinelConnectionPool
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


class _AsyncSentinelRedis(AsyncRedis):
    async def aclose(self, close_connection_pool: bool | None = None) -> None:
        pool = self.connection_pool
        try:
            await super().aclose(close_connection_pool=close_connection_pool)
        finally:
            if isinstance(pool, AsyncSentinelConnectionPool):
                for sentinel in pool.sentinel_manager.sentinels:
                    await sentinel.aclose()


def _options(retry: Retry | AsyncRetry, overrides: Mapping[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {
        "socket_connect_timeout": 3,
        "socket_timeout": 3,
        "retry": retry,
    }
    # Redis 6.4+ clients probe Redis 8.2 maintenance notifications on every new
    # connection. Valkey rejects that optional command, inflating ERR counters.
    # Sentinel/retry already own reconnection here. Older supported clients do
    # not offer this option and do not send the unsupported handshake.
    try:
        from redis.maint_notifications import MaintNotificationsConfig
    except ImportError:
        pass
    else:
        options["maint_notifications_config"] = MaintNotificationsConfig(enabled=False)
    options.update(overrides)
    return options


def _sentinel_options(settings: Settings, options: dict[str, Any]) -> dict[str, Any]:
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
    if "maint_notifications_config" in options:
        discovery["maint_notifications_config"] = options["maint_notifications_config"]
    if settings.redis_sentinel_username:
        discovery["username"] = settings.redis_sentinel_username
    if settings.redis_sentinel_password:
        discovery["password"] = settings.redis_sentinel_password.get_secret_value()
    return discovery


def create_redis(settings: Settings, **overrides: Any) -> Redis:
    options = _options(Retry(ExponentialWithJitterBackoff(base=1, cap=10), retries=3), overrides)
    if not settings.redis_sentinel_nodes:
        return Redis.from_url(settings.redis_url, **options)

    # The URL continues to define the logical DB, data credentials and TLS.
    # Only Sentinel may supply the writable primary's address.
    discovery = _sentinel_options(settings, options)
    sentinel = Sentinel(settings.redis_sentinel_nodes, sentinel_kwargs=discovery)
    return cast(
        Redis,
        sentinel.master_for(settings.redis_sentinel_master, redis_class=_SentinelRedis, **options),
    )


def create_async_redis(settings: Settings, **overrides: Any) -> AsyncRedis:
    """Use the same Redis and Sentinel connection policy without blocking an event loop."""
    options = _options(
        AsyncRetry(ExponentialWithJitterBackoff(base=1, cap=10), retries=3), overrides
    )
    if not settings.redis_sentinel_nodes:
        return AsyncRedis.from_url(settings.redis_url, **options)
    if settings.redis_sentinel_master is None:
        raise ValueError("Redis Sentinel requires a master name")
    discovery = _sentinel_options(settings, options)
    sentinel = AsyncSentinel(settings.redis_sentinel_nodes, sentinel_kwargs=discovery)
    return cast(
        AsyncRedis,
        sentinel.master_for(
            settings.redis_sentinel_master, redis_class=_AsyncSentinelRedis, **options
        ),
    )
