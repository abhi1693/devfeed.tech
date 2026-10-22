"""Reader forwarding policy follows the user API, including its private inbox transport."""

import importlib.util
import json
from collections import Counter
from pathlib import Path

from devfeed_http.agent_permissions import AGENT_POLICY_EXTENSION
from devfeed_http.inbox import INBOX_OPERATIONS, MAX_BODY
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
    assert len(actual) == len(operations) == 72
    assert len({operation["operationId"] for operation in operations}) == len(operations)
    assert len({operation["path"] for operation in operations}) == 60
    assert Counter(operation["method"] for operation in operations) == {
        "GET": 33,
        "POST": 22,
        "PUT": 15,
        "DELETE": 2,
    }
    for key, operation in documented.items():
        assert actual[key]["operationId"] == operation["operationId"]
        assert actual[key]["policy"] == operation[GATEWAY_POLICY_EXTENSION]
    hidden_operations = actual.keys() - documented.keys()
    assert len(hidden_operations) == 17
    for operation in INBOX_OPERATIONS:
        key = ("/v1/user/notifications/chimely/v1/inbox" + operation.path, operation.method)
        assert key in hidden_operations
        hidden = actual[key]
        assert hidden["operationId"] == "userInbox" + operation.name
        if operation.parameter is None:
            assert hidden["parameters"] == []
        else:
            parameter = hidden["parameters"][0]
            assert parameter["name"] == operation.parameter
            assert parameter["converter"] == "str"
            assert parameter["schema"]["minLength"] == 32
            assert parameter["schema"]["maxLength"] == 32
            prefix = "notif" if operation.parameter == "notification_id" else "bcast"
            assert parameter["schema"]["pattern"] == "^" + prefix + r"_[a-z0-9]{26}$"
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
        "extension": {
            "credentials": "include",
            "cacheControl": None,
            "requestHeaders": ["Content-Type", "X-CSRF-Token", "If-None-Match"],
            "timeoutMs": 15_000,
        },
    }
    overrides = {
        **{
            "userInbox" + operation.name: {**default, "bodyLimit": MAX_BODY}
            for operation in INBOX_OPERATIONS
        },
        "user_auth_login": {**default, "cookiePolicy": "reader-attribution", "extension": None},
        "user_auth_callback": {**default, "extension": None},
        "user_auth_sso_callback": {**default, "extension": None},
        "user_auth_backchannel_logout": {
            **default,
            "authentication": "logout-token",
            "bodyLimit": 16384,
            "cookiePolicy": "omit",
            "extension": None,
        },
        "upload_avatar_v1_user_settings_profile_avatar_post": {
            **default,
            "bodyLimit": 5 * 1024 * 1024 + 65536,
            "bodyTimeoutMs": 10_000,
            "extension": {**default["extension"], "timeoutMs": 45_000},
        },
        "remove_avatar_v1_user_settings_profile_avatar_delete": {
            **default,
            "extension": {**default["extension"], "timeoutMs": 45_000},
        },
        "placements_v1_user_partner_placements_query_post": {
            **default,
            "bodyLimit": 16384,
            "handler": "placement-query",
        },
    }
    for operation in operations:
        assert operation["policy"] == overrides.get(operation["operationId"], default)
    assert {operation["operationId"] for operation in operations} >= overrides.keys()


def test_extension_auth_grants_keep_oidc_navigation_out_of_fetch_transport():
    operations = exporter.build_contract()[GATEWAY_ROUTES_EXTENSION]
    denied = {
        operation["operationId"]
        for operation in operations
        if operation["policy"]["extension"] is None
    }
    assert denied == {
        "user_auth_login",
        "user_auth_callback",
        "user_auth_sso_callback",
        "user_auth_backchannel_logout",
    }
    auth = [operation for operation in operations if operation["path"].startswith("/v1/user/auth/")]
    assert [operation["operationId"] for operation in operations[:5]] == [
        "user_auth_config",
        "user_auth_login",
        "user_auth_callback",
        "user_auth_me",
        "user_auth_logout",
    ]
    assert {
        (operation["operationId"], operation["method"])
        for operation in auth
        if operation["policy"]["extension"] is not None
    } == {
        ("user_auth_config", "GET"),
        ("user_auth_me", "GET"),
        ("user_auth_logout", "POST"),
        ("user_auth_sso_logout", "POST"),
    }
    assert sum(operation["policy"]["extension"] is not None for operation in operations) == 68


def test_reader_workspace_and_layout_operations_keep_private_extension_transport():
    document = exporter.build_contract()
    expected = {
        ("/v1/user/settings/columns", "GET"): "columns_settings_v1_user_settings_columns_get",
        ("/v1/user/settings/columns", "PUT"): "save_columns_settings_v1_user_settings_columns_put",
        (
            "/v1/user/settings/columns/starters",
            "GET",
        ): "columns_starters_v1_user_settings_columns_starters_get",
        ("/v1/user/settings/feed-view", "GET"): "feed_view_settings_v1_user_settings_feed_view_get",
        (
            "/v1/user/settings/feed-view",
            "PUT",
        ): "save_feed_view_settings_v1_user_settings_feed_view_put",
    }
    operations = {
        (operation["path"], operation["method"]): operation
        for operation in document[GATEWAY_ROUTES_EXTENSION]
        if operation["path"] in {path for path, _ in expected}
    }
    assert {
        identity: operation["operationId"] for identity, operation in operations.items()
    } == expected
    for (path, method), operation in operations.items():
        assert operation["parameters"] == []
        assert operation["policy"] == {
            "bodyLimit": 1_000_000,
            "bodyTimeoutMs": 2000,
            "upstreamTimeoutMs": 45_000,
            "cookiePolicy": "reader",
            "handler": "forward",
            "extension": {
                "credentials": "include",
                "cacheControl": None,
                "requestHeaders": ["Content-Type", "X-CSRF-Token", "If-None-Match"],
                "timeoutMs": 15_000,
            },
        }
        assert AGENT_POLICY_EXTENSION not in document["paths"][path][method.lower()]
        if method == "PUT":
            schema = document["paths"][path]["put"]["requestBody"]
            assert schema["required"] is True
            assert set(schema["content"]) == {"application/json"}


def test_tag_preference_transport_exports_reads_bulk_saves_and_single_follows():
    operations = exporter.build_contract()[GATEWAY_ROUTES_EXTENSION]
    tags = {
        (operation["path"], operation["method"]): operation
        for operation in operations
        if operation["path"].startswith("/v1/user/preferences/tags")
    }
    assert {key: operation["operationId"] for key, operation in tags.items()} == {
        ("/v1/user/preferences/tags", "GET"): "tag_preferences_v1_user_preferences_tags_get",
        ("/v1/user/preferences/tags", "PUT"): "save_tag_preferences_v1_user_preferences_tags_put",
        (
            "/v1/user/preferences/tags/{tag_id}",
            "PUT",
        ): "follow_tag_v1_user_preferences_tags__tag_id__put",
    }
    for operation in tags.values():
        policy = operation["policy"]
        assert policy["cookiePolicy"] == "reader"
        assert policy["handler"] == "forward"
        assert policy["extension"] == {
            "credentials": "include",
            "cacheControl": None,
            "requestHeaders": ["Content-Type", "X-CSRF-Token", "If-None-Match"],
            "timeoutMs": 15_000,
        }


def test_public_operation_provenance_catalog_is_complete_unique_and_deterministic():
    document = exporter.build_contract()
    operations = document["x-devfeed-public-operations"]
    native = exporter.create_public_app().openapi()
    documented = {
        (path, method.upper()): operation["operationId"]
        for path, methods in native["paths"].items()
        for method, operation in methods.items()
    }
    actual = {
        (operation["path"], operation["method"]): operation["operationId"]
        for operation in operations
    }
    assert actual == documented
    assert len(actual) == len(operations) == 20
    assert len({operation["operationId"] for operation in operations}) == len(operations)
    assert all(set(operation) == {"operationId", "method", "path"} for operation in operations)
    assert operations == sorted(
        operations,
        key=lambda operation: (operation["path"], operation["method"], operation["operationId"]),
    )
    assert (
        actual[("/v1/articles/{article_id}", "GET")]
        == "article_detail_v1_articles__article_id__get"
    )
    assert actual[("/v1/sources/{source_id}", "GET")] == "source_detail_v1_sources__source_id__get"
    assert (
        actual[("/v1/partner-placements/query", "POST")]
        == "placements_v1_partner_placements_query_post"
    )
    assert (
        actual[("/v1/partner-tracking/events", "POST")]
        == "track_event_v1_partner_tracking_events_post"
    )


def test_reader_export_keeps_uuid_validation_and_strict_placement_input():
    document = exporter.build_contract()
    for collection, parameter in (
        ("topics", "topic_id"),
        ("sources", "source_id"),
        ("tags", "tag_id"),
    ):
        follow = next(
            operation
            for operation in document[GATEWAY_ROUTES_EXTENSION]
            if operation["path"] == f"/v1/user/preferences/{collection}/{{{parameter}}}"
        )
        assert follow["parameters"] == [
            {
                "name": parameter,
                "schema": {
                    "type": "string",
                    "format": "uuid",
                    "title": parameter.replace("_", " ").title(),
                },
                "converter": "str",
            }
        ]
    placement = document["components"]["schemas"]["PlacementQuery"]
    assert placement["additionalProperties"] is False
    assert placement["required"] == ["surface"]
    assert placement["properties"]["article_ids"]["maxItems"] == 24
    assert placement["properties"]["exclude_product_ids"]["maxItems"] == 100
