"""Sitemap generation runs at startup and retries without blocking scheduler cycles."""

import threading

from devfeed_aggregator.sitemap_refresher import sitemap_refresher


def test_refresher_retries_failure_and_stops_with_scheduler(monkeypatch):
    completed = threading.Event()
    calls = []

    def refresh():
        calls.append(True)
        if len(calls) == 1:
            raise RuntimeError("temporary cache outage")
        completed.set()

    monkeypatch.setattr("devfeed_aggregator.sitemap_refresher.refresh_snapshot", refresh)
    with sitemap_refresher(interval=0.01):
        assert completed.wait(timeout=2)
    assert len(calls) >= 2
    assert not any(thread.name == "sitemap-refresh" for thread in threading.enumerate())
