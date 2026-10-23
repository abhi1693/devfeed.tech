"""Preview deadlines bound slow streaming responses, alongside socket timeouts."""

from contextlib import nullcontext
from types import SimpleNamespace

import httpcore
import pytest
from devfeed_core import partner_connectors
from devfeed_core.feeds import fetcher
from devfeed_core.feeds.fetcher import FeedError
from devfeed_core.partner_tools import ProductInput
from devfeed_http import partner_connector_previews as previews
from devfeed_http.partner_connector_previews import ConnectorPreviewService, public_diagnostic
from fastapi import HTTPException
from pydantic import ValidationError

from tests.feeds.test_fetcher import use_pool
from tests.partners.jobs.test_worker_identity_boundaries_unit import incoming
from tests.support.partner_matching import product


def test_slow_drip_response_hits_preview_deadline_before_full_download(monkeypatch):
    clock = SimpleNamespace(now=100.0, chunks=0)
    monkeypatch.setattr(previews, "time", SimpleNamespace(monotonic=lambda: clock.now))
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
    config = partner_connectors.ConnectorConfig(base_url="https://catalog.example")
    with pytest.raises(HTTPException) as failure:
        ConnectorPreviewService("preview-test", config).products()
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
    config = partner_connectors.ConnectorConfig(
        base_url="https://catalog.example", auth={"mode": "bearer"}
    )
    with pytest.raises(HTTPException) as failure:
        ConnectorPreviewService(
            "preview-test", config, credential="private-preview-token"
        ).response()
    assert failure.value.status_code == 422
    assert len(pool.requests) == 1
    assert pool.requests[0][1]["Authorization"] == "Bearer private-preview-token"


def test_mapping_preview_shares_one_deadline_and_keeps_saved_configuration(monkeypatch):
    monkeypatch.setattr(previews, "time", SimpleNamespace(monotonic=lambda: 100.0))
    calls = []

    def request(client, url):
        calls.append((url, client.config.timeout_seconds, client.deadline))
        if "/details/" not in url:
            return {"items": [{"id": str(index)} for index in range(5)], "nextCursor": "next"}
        if url.endswith("/1"):
            raise ValueError("private API credential")
        return {
            "id": url.rsplit("/", 1)[-1],
            "name": "API Checker",
            "url": "https://checker.example",
            "listing_url": "https://catalog.example/tools/checker",
            "tagline": "Check API compatibility before deploying software.",
        }

    monkeypatch.setattr(partner_connectors.ConnectorClient, "request", request)
    config = partner_connectors.ConnectorConfig(
        base_url="https://catalog.example", detail_path="/details/{id}", timeout_seconds=30
    )
    result = ConnectorPreviewService("preview-test", config).products()
    assert result.discovered == 5 and result.has_next_page
    assert [product.external_id for product in result.products] == ["0", "2"]
    assert len(result.errors) == 1 and "private" not in result.model_dump_json()
    assert len(calls) == 4 and {(timeout, deadline) for _, timeout, deadline in calls} == {
        (5, 120.0)
    }
    assert config.timeout_seconds == 30


def test_preview_keeps_shorter_configured_timeout_and_raw_diagnostics(monkeypatch):
    calls = []

    def request(client, url):
        calls.append(client.config.timeout_seconds)
        return {"unknown": {"items": []}, "access_token": "private-token"}

    monkeypatch.setattr(partner_connectors.ConnectorClient, "request", request)
    config = partner_connectors.ConnectorConfig(
        base_url="https://catalog.example", timeout_seconds=2
    )
    service = ConnectorPreviewService("preview-test", config)
    result = service.response()
    assert result.data == {"unknown": {"items": []}, "access_token": "[redacted]"}
    with pytest.raises(HTTPException) as failure:
        service.products()
    assert failure.value.status_code == 422
    assert calls == [2, 2] and config.timeout_seconds == 2


@pytest.mark.parametrize("method", ["products", "response"])
def test_preview_errors_hide_upstream_secrets_from_responses_and_logs(monkeypatch, caplog, method):
    def request(client, url):
        raise ValueError("private-token upstream internal-address")

    monkeypatch.setattr(partner_connectors.ConnectorClient, "request", request)
    config = partner_connectors.ConnectorConfig(base_url="https://catalog.example")
    with caplog.at_level("INFO"), pytest.raises(HTTPException) as failure:
        getattr(ConnectorPreviewService("preview-test", config), method)()
    assert failure.value.status_code == 422 and failure.value.__suppress_context__
    assert "private-token" not in failure.value.detail
    assert "internal-address" not in failure.value.detail
    assert "private-token" not in caplog.text and "internal-address" not in caplog.text
    assert caplog.records[-1].error_type == "ValueError"


def test_preview_reports_reversed_website_mapping(monkeypatch):
    config = partner_connectors.ConnectorConfig.model_validate(
        partner_connectors.SHIPYARD_CONNECTOR
    )
    record = {
        "id": "test",
        "name": "Test",
        "description": "Test product",
        "productUrl": "https://shipyardhq.dev/products/test",
        "url": "https://shipyardhq.dev/products/test",
    }
    monkeypatch.setattr(
        partner_connectors.ConnectorClient,
        "request",
        lambda *_: {
            "items": [record],
            "nextCursor": None,
        },
    )
    result = ConnectorPreviewService("shipyardhq", config).products()
    assert result.products == []
    assert result.errors == [
        "Product 1: Website URL points to a platform host. Select the product’s own website field."
    ]


def test_preview_reports_missing_product_list_paths(monkeypatch):
    monkeypatch.setattr(partner_connectors.ConnectorClient, "request", lambda *_: {})
    config = partner_connectors.ConnectorConfig(base_url="https://catalog.example")
    with pytest.raises(HTTPException) as failure:
        ConnectorPreviewService("test", config).products()
    assert failure.value.detail == "Product list paths did not match the API response."


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500])
def test_preview_reports_upstream_http_status_without_private_message(monkeypatch, status):
    def request(*_):
        raise fetcher.FeedError("private-token internal-address", status=status)

    monkeypatch.setattr(partner_connectors.ConnectorClient, "request", request)
    config = partner_connectors.ConnectorConfig(base_url="https://catalog.example")
    with pytest.raises(HTTPException) as failure:
        ConnectorPreviewService("test", config).response()
    assert f"HTTP {status}" in failure.value.detail
    assert "private-token" not in failure.value.detail
    assert "internal-address" not in failure.value.detail


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("name", None, "Product name paths"),
        ("productUrl", None, "Website URL paths"),
        ("productUrl", "http://127.0.0.1/private-token", "valid public HTTP"),
        ("url", "https://tool.example/", "Listing URL must belong"),
        ("description", None, "Tagline paths"),
        ("description", {"secret": "private-token"}, "Tagline paths must select text"),
    ],
)
def test_preview_identifies_failing_field(monkeypatch, field, value, message):
    record = {
        "id": "test",
        "name": "Test",
        "description": "Test product",
        "productUrl": "https://tool.example/",
        "url": "https://shipyardhq.dev/products/test",
    }
    record[field] = value
    monkeypatch.setattr(
        partner_connectors.ConnectorClient,
        "request",
        lambda *_: {
            "items": [record],
            "nextCursor": None,
        },
    )
    config = partner_connectors.ConnectorConfig.model_validate(
        partner_connectors.SHIPYARD_CONNECTOR
    )
    result = ConnectorPreviewService("test", config).products()
    assert len(result.errors) == 1 and message in result.errors[0]
    assert "private-token" not in result.model_dump_json()


def test_transport_diagnostic_hides_private_exception_and_preserves_actionable_recovery():
    error = FeedError("private upstream endpoint and credential", reason="transport_error")
    assert (
        public_diagnostic(error, "fallback")
        == "Could not connect to the import API or the request timed out. Check API availability."
    )


def test_unrecognized_validation_field_uses_safe_preview_fallback_without_echoing_rejected_input():
    document = incoming(product()).model_dump(mode="json")
    document["pricing"] = "private upstream value"
    with pytest.raises(ValidationError) as error:
        ProductInput.model_validate(document)
    assert (
        public_diagnostic(error.value, "Could not normalize this imported product")
        == "Could not normalize this imported product"
    )
