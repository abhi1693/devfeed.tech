"""Sentinel discovery must retain isolation and use managed data connections."""

import pytest
from devfeed_core.config import Settings
from devfeed_core.redis import create_redis
from pydantic import ValidationError
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
