"""Preview deadlines bound slow streaming responses, alongside socket timeouts."""

from contextlib import nullcontext
from types import SimpleNamespace

import httpcore
import pytest
from devfeed_core import partner_connectors
from devfeed_core.feeds import fetcher
from devfeed_partner_api import connections
from fastapi import HTTPException
from test_fetcher import use_pool


def test_slow_drip_response_hits_preview_deadline_before_full_download(monkeypatch):
    clock = SimpleNamespace(now=100.0, chunks=0)
    monkeypatch.setattr(connections, "time", SimpleNamespace(monotonic=lambda: clock.now))
    monkeypatch.setattr(
        fetcher,
        "time",
        SimpleNamespace(monotonic=lambda: clock.now, perf_counter=lambda: clock.now),
    )
    monkeypatch.setattr(partner_connectors, "create_redis", lambda *_: nullcontext(None))
    monkeypatch.setattr(partner_connectors, "consume_rate_limits", lambda *_: 0)

    def slow_body():
        for chunk in [b'{"items": [', b" ", b" ", b" ", b"]}"]:
            clock.now += 7
            clock.chunks += 1
            yield chunk

    pool = use_pool(monkeypatch, [httpcore.Response(200, content=slow_body())])
    config = connections.PartnerConnectorConfig(base_url="https://catalog.example")
    with pytest.raises(HTTPException) as failure:
        connections.preview_products("preview-test", config, None)
    assert failure.value.status_code == 422
    assert clock.chunks == 3 and len(pool.requests) == 1
    assert "Check endpoints" in failure.value.detail


def test_cross_origin_redirect_cannot_receive_preview_credential(monkeypatch):
    monkeypatch.setattr(partner_connectors, "create_redis", lambda *_: nullcontext(None))
    monkeypatch.setattr(partner_connectors, "consume_rate_limits", lambda *_: 0)
    pool = use_pool(
        monkeypatch,
        [
            httpcore.Response(302, headers={"location": "https://foreign.example/products"}),
            httpcore.Response(200, content=b'{"items": [], "nextCursor": null}'),
        ],
    )
    config = connections.PartnerConnectorConfig(
        base_url="https://catalog.example", auth={"mode": "bearer", "source": "connection"}
    )
    with pytest.raises(HTTPException) as failure:
        connections.preview_response("preview-test", config, "private-preview-token")
    assert failure.value.status_code == 422
    assert len(pool.requests) == 1
    assert pool.requests[0][1]["Authorization"] == "Bearer private-preview-token"
