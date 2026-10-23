import asyncio
import json
import logging

import pytest
from devfeed_core.http_logging import request_log_fields
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
                b"tag=react&tag=typescript&diverse=true&languages=en&languages=fr&limit=25"
                b"&q=private-query&token=private-token"
            ),
        }
    )
    assert fields["route"] == "/v1/topics/%E6%97%A5%E6%9C%AC%E8%AA%9E/with%2Fslash"
    assert fields["request_url"] == (
        "https://admin.example:8443"
        + fields["route"]
        + "?tag=react&tag=typescript&diverse=true&languages=en&languages=fr&limit=25"
        + "&q=private-query&token=[REDACTED]"
    )


@pytest.mark.parametrize("formatter", [JsonFormatter, TextFormatter])
def test_both_formats_redact_authentication_in_request_urls(formatter):
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
    assert "private-password" not in output
    assert "private-code" not in output
    assert "private-state" not in output
    assert "private-token" not in output
    assert "private-secret" not in output
    assert "unknown=private-value" in output
    assert "limit=30" in output
    assert "[REDACTED]" in output


@pytest.mark.parametrize("formatter", [JsonFormatter, TextFormatter])
def test_full_urls_survive_request_fields_messages_and_exceptions(formatter):
    query = "q=" + "original-value" * 1000 + "&token=last-value"
    fields = request_log_fields(
        {
            "method": "GET",
            "scheme": "https",
            "headers": [(b"host", b"example.test")],
            "path": "/search",
            "query_string": query.encode(),
        }
    )
    original_url = "https://example.test/search?" + query
    url = original_url.replace("last-value", "[REDACTED]")
    assert fields["request_url"] == url
    record = logging.makeLogRecord(
        {"name": "devfeed_api.logging", "msg": "request_completed", "levelname": "INFO", **fields}
    )
    assert url in formatter("api").format(record)
    record = logging.makeLogRecord(
        {"name": "httpx", "msg": "GET %s", "args": (original_url,), "levelname": "WARNING"}
    )
    assert url in formatter("api").format(record)
    error = RuntimeError(original_url)
    record.exc_info = (RuntimeError, error, None)
    assert url in formatter("api").format(record)
    assert json.loads(JsonFormatter("api").format(record))["exception"][0]["message"] == url


@pytest.mark.parametrize("service", ["api", "user-api", "admin-api"])
@pytest.mark.parametrize("log_format", ["text", "json"])
def test_callback_logs_are_redacted_without_changing_authentication_inputs(service, log_format):
    app = FastAPI()
    logger = logging.getLogger(f"devfeed_{service.replace('-', '_')}.logging")
    app.add_middleware(RequestLoggingMiddleware, service=service, logger=logger)
    formatter = JsonFormatter(service) if log_format == "json" else TextFormatter(service)
    events = []

    class Capture(logging.Handler):
        def emit(self, record):
            events.append(formatter.format(record))

    handler = Capture()
    previous_level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    @app.get("/auth/callback")
    def callback(code: str, state: str):
        assert code == "private-code" and state == "private-state"
        logger.info("callback_checked")
        return {"accepted": True}

    try:
        with TestClient(app) as client:
            response = client.get("/auth/callback?code=private-code&state=private-state&limit=25")
        assert response.json() == {"accepted": True}
        assert events and all(
            "private-code" not in item and "private-state" not in item for item in events
        )
        assert all("code=[REDACTED]&state=[REDACTED]&limit=25" in item for item in events)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


@pytest.mark.parametrize("formatter", [JsonFormatter, TextFormatter])
def test_request_url_controls_are_escaped_without_removing_values(formatter):
    url = "https://example.test/a\x00b?limit=1\nFORGED&token=private"
    record = logging.makeLogRecord(
        {
            "name": "devfeed_api.logging",
            "msg": "request_completed",
            "levelname": "INFO",
            "request_url": url,
        }
    )
    output = formatter("api").format(record)
    assert len(output.splitlines()) == 1
    assert "FORGED&token=[REDACTED]" in output
    assert json.loads(JsonFormatter("api").format(record))["request_url"] == url.replace(
        "token=private", "token=[REDACTED]"
    )


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
    assert fields["request_url"] == "http://[::1]:8001/missing?unknown=\ufffd"


@pytest.mark.parametrize("package", ["devfeed_api", "devfeed_admin_api", "devfeed_user_api"])
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
                expected = "http://testserver" + path.replace("private-token", "[REDACTED]")
                assert events and all(expected in event for event in events)
                assert not any(
                    "private-token" in event or "{source_id}" in event for event in events
                )
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
