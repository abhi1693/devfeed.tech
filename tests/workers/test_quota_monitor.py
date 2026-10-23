import json
from types import SimpleNamespace

from devfeed_aggregator import quota_monitor


class Cache:
    def __init__(self):
        self.data = {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value, *, nx=False, ex=None):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True


def test_polling_is_shared_and_only_allowlisted_quota_is_retained(monkeypatch):
    cache, calls = Cache(), []
    now = 1789430400
    settings = SimpleNamespace(
        ai_enabled=True, ai_quota_pacing_enabled=True, ai_quota_reserve_percent=20
    )
    monkeypatch.setattr(quota_monitor, "get_settings", lambda: settings)
    monkeypatch.setattr(quota_monitor, "create_redis", lambda _: cache)
    monkeypatch.setattr(quota_monitor.time, "time", lambda: now)
    monkeypatch.setattr(quota_monitor, "quota_key", lambda: "quota")
    response = {
        "accountId": "private-account",
        "rateLimits": {
            "primary": {"usedPercent": 5, "windowDurationMins": 10080, "resetsAt": now + 5 * 86400}
        },
    }

    def read():
        calls.append(1)
        return response

    monkeypatch.setattr(quota_monitor, "CodexClient", lambda _: SimpleNamespace(read_quota=read))
    quota_monitor.refresh_quota()
    quota_monitor.refresh_quota()
    assert len(calls) == 1
    first = json.loads(cache.data["quota"])
    assert "private-account" not in cache.data["quota"]
    del cache.data["quota:poll"]
    response["rateLimits"]["primary"]["usedPercent"] = 10
    quota_monitor.refresh_quota()
    assert json.loads(cache.data["quota"])["ceiling_percent"] == first["ceiling_percent"]
