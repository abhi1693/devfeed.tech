from types import SimpleNamespace

from devfeed_core import client_lifecycle


class CachedProvider:
    def __init__(self, resource, events, *, cached=True):
        self.resource = resource
        self.events = events
        self.cached = cached

    def __call__(self):
        self.events.append("get")
        return self.resource

    def cache_info(self):
        return SimpleNamespace(currsize=int(self.cached))

    def cache_clear(self):
        self.events.append("clear")
        self.cached = False


def test_close_shared_clients_closes_only_initialized_resources(monkeypatch):
    events = []
    engine = SimpleNamespace(dispose=lambda: events.append("engine"))
    redis = SimpleNamespace(close=lambda: events.append("redis"))
    engine_provider = CachedProvider(engine, events)
    redis_provider = CachedProvider(redis, events)
    monkeypatch.setattr(client_lifecycle, "close_cache", lambda: events.append("cache"))
    monkeypatch.setattr(client_lifecycle, "get_engine", engine_provider)

    client_lifecycle.close_shared_clients(redis_provider)

    assert events == ["cache", "get", "engine", "get", "redis", "clear"]


def test_close_shared_clients_does_not_create_unused_clients(monkeypatch):
    events = []
    engine_provider = CachedProvider(None, events, cached=False)
    redis_provider = CachedProvider(None, events, cached=False)
    monkeypatch.setattr(client_lifecycle, "close_cache", lambda: events.append("cache"))
    monkeypatch.setattr(client_lifecycle, "get_engine", engine_provider)

    client_lifecycle.close_shared_clients(redis_provider)

    assert events == ["cache", "clear"]
