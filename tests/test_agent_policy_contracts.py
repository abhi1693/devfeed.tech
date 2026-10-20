"""Only the declared personal MCP operations grant agent access in the user API."""

from devfeed_http.agent_permissions import AGENT_POLICY_EXTENSION
from devfeed_http.operation_policies import (
    GATEWAY_POLICY_EXTENSION,
    GATEWAY_ROUTES_EXTENSION,
    ReaderGatewayPolicy,
    export_gateway_document,
)
from devfeed_user_api.auth import require_browser_user, require_user
from devfeed_user_api.main import create_app
from fastapi.routing import APIRoute, iter_route_contexts

EXPECTED = {
    "feed_v1_user_feed_get": ("GET", "devfeed:read"),
    "daily_v1_user_must_reads_get": ("GET", "devfeed:read"),
    "bookmarks_v1_user_bookmarks_get": ("GET", "devfeed:read"),
    "preferences_v1_user_preferences_get": ("GET", "devfeed:read"),
    "source_preferences_v1_user_preferences_sources_get": ("GET", "devfeed:read"),
    "tag_preferences_v1_user_preferences_tags_get": ("GET", "devfeed:read"),
    "bookmark_v1_user_articles__article_id__bookmark_put": ("PUT", "devfeed:write"),
    "like_v1_user_articles__article_id__like_put": ("PUT", "devfeed:write"),
    "follow_topic_v1_user_preferences_topics__topic_id__put": ("PUT", "devfeed:write"),
    "follow_source_v1_user_preferences_sources__source_id__put": ("PUT", "devfeed:write"),
    "follow_tag_v1_user_preferences_tags__tag_id__put": ("PUT", "devfeed:write"),
}


def dependency_calls(dependant):
    if dependant.call is not None:
        yield dependant.call
    for dependency in dependant.dependencies:
        yield from dependency_calls(dependency)


def test_only_the_declared_personal_operations_grant_an_agent_permission():
    actual = {}
    for context in iter_route_contexts(create_app().routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        extra = context.openapi_extra or {}
        if AGENT_POLICY_EXTENSION not in extra:
            continue
        identity = context.operation_id or context.unique_id
        assert identity not in actual
        assert len(context.methods) == 1
        policy = extra[AGENT_POLICY_EXTENSION]
        assert set(policy) == {"scope"}
        actual[identity] = (next(iter(context.methods)), policy["scope"])
        calls = set(dependency_calls(context.dependant))
        assert require_user in calls
        assert require_browser_user not in calls
    assert actual == EXPECTED


def test_agent_permissions_are_exported_on_operations_without_entering_gateway_transport_policy():
    document = export_gateway_document(create_app(), policy_type=ReaderGatewayPolicy)
    actual = {}
    for path_item in document["paths"].values():
        for method, operation in path_item.items():
            if not isinstance(operation, dict) or AGENT_POLICY_EXTENSION not in operation:
                continue
            policy = operation[AGENT_POLICY_EXTENSION]
            assert set(policy) == {"scope"}
            identity = operation["operationId"]
            assert identity not in actual
            actual[identity] = (method.upper(), policy["scope"])
            assert AGENT_POLICY_EXTENSION not in operation[GATEWAY_POLICY_EXTENSION]
    assert actual == EXPECTED
    operations = document[GATEWAY_ROUTES_EXTENSION]
    assert len(operations) == 67
    for operation in operations:
        assert set(operation) == {"operationId", "method", "path", "parameters", "policy"}
        expected_fields = {
            "bodyLimit",
            "bodyTimeoutMs",
            "upstreamTimeoutMs",
            "cookiePolicy",
            "handler",
            "extension",
        }
        if operation["operationId"] == "user_auth_backchannel_logout":
            expected_fields.add("authentication")
            assert operation["policy"]["authentication"] == "logout-token"
            assert operation["policy"]["extension"] is None
        assert set(operation["policy"]) == expected_fields
    assert set(EXPECTED) <= {operation["operationId"] for operation in operations}


def test_tag_preferences_grant_agents_reads_and_single_follows_without_bulk_replacement():
    document = export_gateway_document(create_app(), policy_type=ReaderGatewayPolicy)
    preferences = document["paths"]["/v1/user/preferences/tags"]
    follow = document["paths"]["/v1/user/preferences/tags/{tag_id}"]["put"]
    assert preferences["get"][AGENT_POLICY_EXTENSION] == {"scope": "devfeed:read"}
    assert follow[AGENT_POLICY_EXTENSION] == {"scope": "devfeed:write"}
    assert AGENT_POLICY_EXTENSION not in preferences["put"]


def test_browser_only_dependencies_do_not_accept_endpoint_agent_permissions():
    browser_only = []
    unmarked_user_operations = []
    for context in iter_route_contexts(create_app().routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        calls = set(dependency_calls(context.dependant))
        extra = context.openapi_extra or {}
        if require_browser_user in calls:
            browser_only.append(context)
            assert AGENT_POLICY_EXTENSION not in extra
        if require_user in calls and AGENT_POLICY_EXTENSION not in extra:
            unmarked_user_operations.append(context)
    assert len(browser_only) >= 2
    assert unmarked_user_operations
