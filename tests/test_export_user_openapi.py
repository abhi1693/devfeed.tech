"""Reader forwarding policy follows the user API, including its private inbox transport."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

from devfeed_http.operation_policies import GATEWAY_POLICY_EXTENSION, GATEWAY_ROUTES_EXTENSION

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "export_user_openapi", ROOT / "scripts/export_user_openapi.py"
)
assert spec is not None and spec.loader is not None
exporter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(exporter)


def test_reader_contract_export_is_current_and_needs_no_service_connections(monkeypatch):
    monkeypatch.setenv(
        "DEVFEED_DATABASE_URL", "postgresql+psycopg://unused:unused@127.0.0.1:1/contracts"
    )
    monkeypatch.setenv("DEVFEED_REDIS_URL", "redis://127.0.0.1:1/0")

    def reject_connection(*args, **kwargs):
        raise AssertionError("Contract export attempted a network connection")

    monkeypatch.setattr("socket.socket.connect", reject_connection)
    document = exporter.build_contract()
    assert document == json.loads((ROOT / "apps/web/openapi.json").read_text())
    assert exporter.build_contract() == document


def test_reader_gateway_covers_user_operations_and_hidden_inbox_without_exposing_health():
    document = exporter.build_contract()
    operations = document[GATEWAY_ROUTES_EXTENSION]
    actual = {(operation["path"], operation["method"]): operation for operation in operations}
    documented = {
        (path, method.upper()): operation
        for path, methods in document["paths"].items()
        for method, operation in methods.items()
        if path.startswith("/v1/user/")
    }
    assert len(actual) == len(operations) == 47
    assert len({operation["operationId"] for operation in operations}) == len(operations)
    assert len({operation["path"] for operation in operations}) == 37
    assert Counter(operation["method"] for operation in operations) == {
        "GET": 25,
        "POST": 9,
        "PUT": 11,
        "DELETE": 2,
    }
    for key, operation in documented.items():
        assert actual[key]["operationId"] == operation["operationId"]
        assert actual[key]["policy"] == operation[GATEWAY_POLICY_EXTENSION]
    hidden_path = "/v1/user/notifications/chimely/v1/inbox/{path}"
    assert actual.keys() - documented.keys() == {
        (hidden_path, method) for method in ("GET", "POST", "PUT")
    }
    for method in ("GET", "POST", "PUT"):
        assert actual[(hidden_path, method)]["parameters"] == [
            {"name": "path", "schema": {"type": "string"}, "converter": "path"}
        ]
    for path in ("/health/live", "/health/ready"):
        assert GATEWAY_POLICY_EXTENSION not in document["paths"][path]["get"]
    assert all(operation["path"].startswith("/v1/user/") for operation in operations)


def test_reader_cookie_and_body_policy_is_explicit_for_every_operation():
    operations = exporter.build_contract()[GATEWAY_ROUTES_EXTENSION]
    default = {
        "bodyLimit": 1_000_000,
        "bodyTimeoutMs": 2000,
        "upstreamTimeoutMs": 45_000,
        "cookiePolicy": "reader",
        "handler": "forward",
    }
    overrides = {
        "user_auth_login": {**default, "cookiePolicy": "reader-attribution"},
        "upload_avatar_v1_user_settings_profile_avatar_post": {
            **default,
            "bodyLimit": 5 * 1024 * 1024 + 65536,
            "bodyTimeoutMs": 10_000,
        },
        "placements_v1_user_partner_placements_query_post": {
            **default,
            "bodyLimit": 8192,
            "handler": "placement-query",
        },
    }
    for operation in operations:
        assert operation["policy"] == overrides.get(operation["operationId"], default)
    assert {operation["operationId"] for operation in operations} >= overrides.keys()


def test_reader_export_keeps_uuid_validation_and_strict_placement_input():
    document = exporter.build_contract()
    follow = next(
        operation
        for operation in document[GATEWAY_ROUTES_EXTENSION]
        if operation["path"] == "/v1/user/preferences/topics/{topic_id}"
    )
    assert follow["parameters"] == [
        {
            "name": "topic_id",
            "schema": {"type": "string", "format": "uuid", "title": "Topic Id"},
            "converter": "str",
        }
    ]
    placement = document["components"]["schemas"]["PlacementQuery"]
    assert placement["additionalProperties"] is False
    assert placement["required"] == ["surface"]
    assert placement["properties"]["article_ids"]["maxItems"] == 24
    assert placement["properties"]["exclude_product_ids"]["maxItems"] == 100
