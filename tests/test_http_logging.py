import asyncio
import json
import logging

import pytest
from devfeed_core.http_logging import request_log_fields, safe_request_url
from devfeed_core.logging import JsonFormatter, TextFormatter
from devfeed_http.logging import RequestLoggingMiddleware
from fastapi import FastAPI
from fastapi.testclient import TestClient


def test_request_url_preserves_ids_query_filters_and_encoded_path():
    fields = request_log_fields(
        {
            "method": "GET",
            "scheme": "https",
            "headers": [(b"host", b"admin.example:8443")],
            "path": "/v1/topics/日本語/with/slash",
            "raw_path": b"/v1/topics/%E6%97%A5%E6%9C%AC%E8%AA%9E/with%2Fslash",
            "query_string": (
                b"tag=react&tag=typescript&limit=25&q=private-query&token=private-token"
            ),
        }
    )
    assert fields["route"] == "/v1/topics/%E6%97%A5%E6%9C%AC%E8%AA%9E/with%2Fslash"
    assert fields["request_url"] == (
        "https://admin.example:8443"
        + fields["route"]
        + "?tag=react&tag=typescript&limit=25&q=[redacted]&token=[redacted]"
    )
    assert safe_request_url(fields["request_url"]) == fields["request_url"]


@pytest.mark.parametrize("formatter", [JsonFormatter, TextFormatter])
def test_both_formats_redact_secrets_without_removing_request_url(formatter):
    url = (
        "https://operator:private-password@admin.example/v1/admin/auth/callback"
        "?code=private-code&state=private-state&access_token=private-token"
        "&client_secret=private-secret&unknown=private-value&limit=30#private-fragment"
    )
    record = logging.makeLogRecord(
        {
            "name": "devfeed_admin_api.logging",
            "msg": "request_completed",
            "levelname": "INFO",
            "levelno": logging.INFO,
            "method": "GET",
            "request_url": url,
            "status_code": 200,
        }
    )
    output = formatter("admin-api").format(record)
    assert "https://admin.example/v1/admin/auth/callback?code=[redacted]" in output
    assert "limit=30" in output
    assert "private-" not in output


def test_request_url_is_bounded_and_does_not_create_extra_log_lines():
    value = safe_request_url("https://admin.example/a\x00b?limit=1%0AFORGED&token=private")
    assert "/a%00b?limit=[redacted]&token=[redacted]" in value
    assert len(value.splitlines()) == 1
    assert "FORGED" not in value
    value = safe_request_url("https://admin.example/" + "a" * 5000)
    assert len(value) == 4096 + len("[truncated]")
    assert value.endswith("[truncated]")
    assert (
        safe_request_url("https://example.test/path?" + "secret=value&" * 101)
        == "https://example.test/path"
    )
    assert safe_request_url("http://[invalid-host/path") == "[redacted-url]"
    assert safe_request_url("redis://operator:password@redis/0") == "[redacted-url]"


def test_origin_falls_back_to_server_and_ignores_forwarded_headers():
    fields = request_log_fields(
        {
            "method": "GET",
            "scheme": "http",
            "path": "/missing",
            "headers": [
                (b"host", b"attacker@evil.example/fake"),
                (b"x-forwarded-host", b"evil.example"),
            ],
            "server": ("::1", 8001),
            "query_string": b"unknown=\xff",
        }
    )
    assert fields["request_url"] == "http://[::1]:8001/missing?unknown=[redacted]"


@pytest.mark.parametrize("package", ["devfeed_api", "devfeed_admin_api"])
@pytest.mark.parametrize("log_format", ["text", "json"])
def test_both_middlewares_log_concrete_locations_for_all_responses(package, log_format):
    middleware = RequestLoggingMiddleware
    app = FastAPI()
    app.add_middleware(middleware, service=package, logger=logging.getLogger(f"{package}.logging"))
    events = []
    formatter = JsonFormatter(package) if log_format == "json" else TextFormatter(package)

    class Capture(logging.Handler):
        def emit(self, record):
            events.append(formatter.format(record))

    logger = logging.getLogger(f"{package}.logging")
    handler = Capture()
    previous_level = logger.level
    logger.setLevel(logging.DEBUG)
    logger.addHandler(handler)

    @app.get("/v1/admin/sources/{source_id}")
    def source(source_id: int):
        logger.warning("request_validation_failed")
        if source_id == 500:
            raise RuntimeError("private-error")
        return {"id": source_id}

    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            for path, status in [
                ("/v1/admin/sources/123?limit=10&access_token=private-token", 200),
                ("/v1/admin/sources/not-a-number", 422),
                ("/v1/admin/sources/500", 500),
                ("/missing/resource", 404),
            ]:
                events.clear()
                response = client.get(path)
                assert response.status_code == status
                expected = "http://testserver" + path.replace("private-token", "[redacted]")
                assert events and all(expected in event for event in events)
                assert not any("private-" in event or "{source_id}" in event for event in events)
                if log_format == "json":
                    payloads = [json.loads(event) for event in events]
                    assert all(
                        item["request_id"] == response.headers["x-request-id"]
                        for item in payloads
                        if status != 500
                    )
                    assert payloads[-1]["status_code"] == status
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


@pytest.mark.parametrize("package", ["devfeed_api", "devfeed_admin_api"])
def test_non_http_scopes_are_forwarded_unchanged(package):
    middleware = RequestLoggingMiddleware
    seen = []

    async def app(scope, receive, send):
        seen.append(scope)

    scope = {"type": "websocket"}
    asyncio.run(
        middleware(app, service=package, logger=logging.getLogger(package))(scope, None, None)
    )
    assert seen == [scope]
