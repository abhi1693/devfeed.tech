"""Sentinel discovery must retain isolation and use managed data connections."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from devfeed_core import cache
from devfeed_core.config import Settings
from devfeed_core.redis import configure_redis_io, create_async_redis, create_redis
from pydantic import ValidationError
from redis.asyncio.connection import SSLConnection as AsyncSSLConnection
from redis.asyncio.sentinel import (
    SentinelConnectionPool as AsyncSentinelConnectionPool,
)
from redis.asyncio.sentinel import SentinelManagedSSLConnection as AsyncSentinelManagedSSLConnection
from redis.sentinel import SentinelConnectionPool, SentinelManagedSSLConnection


def settings(**values):
    return Settings(
        _env_file=None,
        database_url="postgresql+psycopg://localhost/test",
        redis_url=values.pop("redis_url", "redis://ignored:6379/10"),
        **values,
    )


def test_direct_connection_retains_database_and_timeout():
    with create_redis(settings(), socket_timeout=0.2) as client:
        assert not isinstance(client.connection_pool, SentinelConnectionPool)
        assert client.connection_pool.connection_kwargs["db"] == 10
        assert client.connection_pool.connection_kwargs["socket_timeout"] == 0.2
        assert not client.connection_pool.maint_notifications_enabled()


@pytest.mark.parametrize("sentinel", [False, True])
@pytest.mark.parametrize("configured,expected", [(85, 1), (0.2, 0.2), (None, 1), (0, 1)])
def test_owned_io_policy_bounds_data_discovery_and_previously_idle_connections(
    sentinel, configured, expected
):
    config = settings(
        redis_url="redis://data-user:data-pass@ignored:6379/10",
        **(
            {"redis_sentinel_nodes": [("sentinel", 26379)], "redis_sentinel_master": "valkey"}
            if sentinel
            else {}
        ),
    )
    with create_redis(
        config, socket_timeout=configured, socket_connect_timeout=configured
    ) as client:
        clients = [client]
        if sentinel:
            clients.extend(client.connection_pool.sentinel_manager.sentinels)
        old = []
        for current in clients:
            pool = current.connection_pool
            idle = pool.make_connection()
            pool._available_connections.append(idle)
            old.append(idle)
        configure_redis_io(client, socket_timeout=1, socket_connect_timeout=1)
        for current, previous in zip(clients, old, strict=True):
            pool = current.connection_pool
            assert not pool._available_connections
            fresh = pool.make_connection()
            assert fresh is not previous
            assert fresh.socket_timeout == fresh.socket_connect_timeout == expected
            assert fresh.retry.get_retries() == 0
        options = client.connection_pool.connection_kwargs
        assert options["db"] == 10 and options["username"] == "data-user"
        assert options["password"] == "data-pass"


def test_owned_io_policy_overrides_long_url_timeouts_and_rejects_active_pools():
    config = settings(
        redis_url="redis://ignored:6379/10?socket_timeout=85&socket_connect_timeout=85"
    )
    with create_redis(config, socket_timeout=1, socket_connect_timeout=1) as client:
        pool = client.connection_pool
        assert pool.connection_kwargs["socket_timeout"] == 85
        active = pool.make_connection()
        pool._in_use_connections.add(active)
        try:
            with pytest.raises(RuntimeError, match="before checking out"):
                configure_redis_io(client, socket_timeout=1, socket_connect_timeout=1)
            assert pool.connection_kwargs["socket_timeout"] == 85
        finally:
            pool._in_use_connections.remove(active)
        configure_redis_io(client, socket_timeout=1, socket_connect_timeout=1)
        assert pool.connection_kwargs["socket_timeout"] == 1
        assert pool.connection_kwargs["socket_connect_timeout"] == 1


def test_async_direct_connection_retains_database_and_timeout():
    async def check():
        client = create_async_redis(settings(), socket_timeout=0.2)
        try:
            pool = client.connection_pool
            assert not isinstance(pool, AsyncSentinelConnectionPool)
            assert pool.connection_kwargs["db"] == 10
            assert pool.connection_kwargs["socket_timeout"] == 0.2
        finally:
            await client.aclose()

    asyncio.run(check())


def test_async_sentinel_rejects_settings_with_missing_master():
    invalid = settings().model_copy(update={"redis_sentinel_nodes": [("sentinel", 26379)]})
    with pytest.raises(ValueError, match="requires a master name"):
        create_async_redis(invalid)


def test_async_sentinel_uses_tls_credentials_follows_primary_and_closes_discovery(monkeypatch):
    config = settings(
        redis_url="rediss://data-user:data-pass@ignored:6379/10",
        redis_sentinel_nodes=[("sentinel-a", 26379), ("sentinel-b", 26379)],
        redis_sentinel_master="valkey",
        redis_sentinel_username="discovery-user",
        redis_sentinel_password="discovery-pass",
        redis_sentinel_ssl=True,
    )

    async def check():
        client = create_async_redis(config, socket_timeout=0.2)
        pool = client.connection_pool
        assert isinstance(pool, AsyncSentinelConnectionPool)
        assert pool.is_master and pool.service_name == "valkey"
        assert pool.connection_class is AsyncSentinelManagedSSLConnection
        assert pool.connection_kwargs["db"] == 10
        assert pool.connection_kwargs["username"] == "data-user"
        assert pool.connection_kwargs["password"] == "data-pass"
        assert "host" not in pool.connection_kwargs and "port" not in pool.connection_kwargs
        nodes = pool.sentinel_manager.sentinels
        assert len(nodes) == 2
        for node in nodes:
            options = node.connection_pool.connection_kwargs
            assert options["username"] == "discovery-user"
            assert options["password"] == "discovery-pass"
            assert options["socket_timeout"] == 0.2
            assert node.connection_pool.connection_class is AsyncSSLConnection
        discover = AsyncMock(side_effect=[("first", 6379), ("second", 6379)])
        disconnect = AsyncMock()
        monkeypatch.setattr(pool.sentinel_manager, "discover_master", discover)
        monkeypatch.setattr(pool, "disconnect", disconnect)
        assert await pool.get_master_address() == ("first", 6379)
        assert await pool.get_master_address() == ("second", 6379)
        assert disconnect.await_count == 2
        assert all(
            call.kwargs == {"inuse_connections": False} for call in disconnect.await_args_list
        )
        for node in nodes:
            monkeypatch.setattr(node, "aclose", AsyncMock())
        await client.aclose()
        for node in nodes:
            node.aclose.assert_awaited_once()

    asyncio.run(check())


def test_feed_sequence_pool_bounds_sentinel_discovery_and_data_without_nested_retries(monkeypatch):
    config = settings(
        redis_sentinel_nodes=[("sentinel-a", 26379), ("sentinel-b", 26379)],
        redis_sentinel_master="valkey",
    )
    monkeypatch.setattr(cache, "get_settings", lambda: config)
    cache._cache_for_process.cache_clear()
    try:
        optional = cache.get_cache()
        required = cache.get_feed_sequence_cache()
        assert required.redis.connection_pool is not optional.redis.connection_pool
        pool = required.redis.connection_pool
        clients = [required.redis, *pool.sentinel_manager.sentinels]
        for client in clients:
            options = client.connection_pool.connection_kwargs
            assert options["socket_connect_timeout"] == options["socket_timeout"] == 1
            assert options["retry"].get_retries() == 0
        assert pool.connection_kwargs["db"] == 10
        assert optional.redis.connection_pool.connection_kwargs["socket_timeout"] == 0.2
    finally:
        cache.close_cache()


def test_sentinel_uses_managed_primary_and_separate_credentials(monkeypatch):
    config = settings(
        redis_url="rediss://data-user:data-pass@ignored:6379/10",
        redis_sentinel_nodes=[("sentinel-a", 26379), ("sentinel-b", 26379)],
        redis_sentinel_master="valkey",
        redis_sentinel_username="discovery-user",
        redis_sentinel_password="discovery-pass",
        redis_sentinel_ssl=True,
    )
    with create_redis(config, socket_timeout=0.2) as client:
        pool = client.connection_pool
        assert isinstance(pool, SentinelConnectionPool)
        assert pool.is_master
        assert pool.service_name == "valkey"
        assert pool.connection_class is SentinelManagedSSLConnection
        options = pool.connection_kwargs
        assert not pool.maint_notifications_enabled()
        assert options["db"] == 10
        assert options["username"] == "data-user"
        assert options["password"] == "data-pass"
        assert "host" not in options and "port" not in options
        discovery = pool.sentinel_manager.sentinels
        assert len(discovery) == 2
        for node in discovery:
            options = node.connection_pool.connection_kwargs
            assert options["username"] == "discovery-user"
            assert options["password"] == "discovery-pass"
            assert options["socket_timeout"] == 0.2
            assert not node.connection_pool.maint_notifications_enabled()
        # Existing clients re-resolve the primary and evict idle connections to
        # its previous address; restarting the process is not needed.
        monkeypatch.setattr(pool.sentinel_manager, "discover_master", lambda _: ("first", 6379))
        assert pool.get_master_address() == ("first", 6379)
        closed = []
        monkeypatch.setattr(pool, "disconnect", lambda **kwargs: closed.append(kwargs))
        monkeypatch.setattr(pool.sentinel_manager, "discover_master", lambda _: ("second", 6379))
        assert pool.get_master_address() == ("second", 6379)
        assert closed == [{"inuse_connections": False}]
        for node in discovery:
            monkeypatch.setattr(node, "close", lambda: closed.append("discovery"))
    assert closed.count("discovery") == 2


@pytest.mark.parametrize(
    "values",
    [
        {"redis_sentinel_master": "valkey"},
        {"redis_sentinel_nodes": [("sentinel", 26379)]},
        {"redis_sentinel_nodes": [("", 26379)], "redis_sentinel_master": "valkey"},
        {"redis_sentinel_nodes": [("sentinel", 0)], "redis_sentinel_master": "valkey"},
        {"redis_sentinel_nodes": [("sentinel", 65536)], "redis_sentinel_master": "valkey"},
        {"redis_sentinel_nodes": [("sentinel", 26379)], "redis_sentinel_master": " "},
        {"redis_sentinel_password": "unconfigured"},
        {
            "redis_url": "unix:///tmp/redis.sock",
            "redis_sentinel_nodes": [("sentinel", 26379)],
            "redis_sentinel_master": "valkey",
        },
    ],
)
def test_incomplete_or_invalid_sentinel_configuration_is_rejected(values):
    with pytest.raises(ValidationError):
        settings(**values)


def test_worker_database_connections_are_released_without_local_pooling(monkeypatch):
    from devfeed_core import db
    from sqlalchemy.pool import NullPool, QueuePool

    monkeypatch.setattr(db, "get_settings", lambda: settings(database_pool_enabled=False))
    # Build only: no database connection is opened by create_engine.
    db.get_engine.cache_clear()
    try:
        engine = db.get_engine()
        assert isinstance(engine.pool, NullPool)
        engine.dispose()
        db.get_engine.cache_clear()
        monkeypatch.setattr(db, "get_settings", lambda: settings(database_pool_enabled=True))
        engine = db.get_engine()
        assert isinstance(engine.pool, QueuePool)
        engine.dispose()
    finally:
        db.get_engine.cache_clear()
