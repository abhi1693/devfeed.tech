"""Real SDK OAuth routes preserve public tools, resource challenges and CORS policy."""

from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, Mock

import httpx
import pytest
from devfeed_mcp import main, transport
from devfeed_mcp.config import Settings
from devfeed_mcp.oauth import RESOURCE_SCOPES
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.testclient import TestClient
from test_mcp_routes import rpc_request

RESOURCE = "https://mcp.example/mcp"


def configuration(**changes):
    return Settings(
        _env_file=None,
        **{
            "user_api_url": "http://user-api:8002",
            "oauth_issuer_url": "https://mcp.example",
            "public_url": RESOURCE,
            "web_url": "https://reader.example",
            "redis_url": "redis://redis.invalid/15",
            "allowed_hosts": ["mcp.example"],
            "allowed_origins": ["https://reader.example"],
            **changes,
        },
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("user_api_url", None),
        ("public_url", "https://user:private@mcp.example/mcp"),
        ("web_url", "http://remote-reader.example"),
        ("oauth_issuer_url", "https://mcp.example/oauth"),
        ("user_api_url", "http://user-api:8002/v1"),
        ("web_url", "https://reader.example/?token=private"),
        ("web_url", "https://reader.example/#private"),
    ],
)
def test_personal_transport_requires_complete_safe_origin_configuration(field, value):
    with pytest.raises(ValidationError) as rejected:
        configuration(**{field: value})
    assert "user:private" not in str(rejected.value)


def test_loopback_configuration_supports_local_consent_without_relaxing_remote_provider_https():
    local = configuration(
        user_api_url="http://user-api:8002",
        oauth_issuer_url="http://127.0.0.1:8003",
        public_url="http://127.0.0.1:8003/mcp",
        web_url="http://localhost:3000",
    )
    assert local.web_url == "http://localhost:3000" and local.public_url.endswith("/mcp")
    with pytest.raises(ValidationError, match="require HTTPS"):
        configuration(oauth_issuer_url="http://remote-issuer.example")


@pytest.fixture
def sdk(monkeypatch):
    redis = NS(
        get=AsyncMock(return_value=None),
        set=AsyncMock(),
        incr=AsyncMock(return_value=1),
        expire=AsyncMock(),
        ping=AsyncMock(return_value=True),
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(main.Redis, "from_url", lambda *_args, **_kwargs: redis)
    requests = []
    status = {"public": 200, "user": 200}

    def upstream(request):
        requests.append(request)
        user = request.url.host == "user-api"
        return httpx.Response(
            status["user" if user else "public"],
            json={"status": "ok"} if user else {"items": [], "total": 0, "limit": 1, "offset": 0},
        )

    app = main.create_app(configuration(), transport=httpx.MockTransport(upstream))
    with TestClient(app, base_url="https://mcp.example") as client:
        yield NS(client=client, app=app, redis=redis, requests=requests, status=status)
    redis.aclose.assert_awaited_once()


def test_sdk_metadata_announces_public_client_auth_and_exact_resource_scope(sdk):
    response = sdk.client.get("/.well-known/oauth-authorization-server")
    assert response.status_code == 200
    metadata = response.json()
    assert metadata["issuer"] == "https://mcp.example"
    assert metadata["token_endpoint_auth_methods_supported"] == ["none"]
    assert metadata["revocation_endpoint_auth_methods_supported"] == ["none"]
    resource = sdk.client.get("/.well-known/oauth-protected-resource/mcp")
    assert resource.status_code == 200 and resource.json()["resource"] == RESOURCE
    assert resource.json()["scopes_supported"] == RESOURCE_SCOPES
    sdk.redis.get.assert_not_awaited()
    assert sdk.requests == []


def test_tool_discovery_is_anonymous_but_account_calls_and_invalid_bearer_require_auth(sdk):
    response = sdk.client.post("/mcp", **rpc_request("2025-11-25"))
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()["result"]["tools"]}
    assert {"get_article", "get_my_feed", "set_bookmark"} <= names
    sdk.redis.get.assert_not_awaited()
    account = sdk.client.post(
        "/mcp", **rpc_request("2025-11-25", "tools/call", name="get_my_feed", arguments={})
    )
    assert account.status_code == 401
    request = rpc_request("2025-11-25")
    request["headers"].update(Authorization="Bearer expired-token", Origin="https://reader.example")
    rejected = sdk.client.post("/mcp", **request)
    assert rejected.status_code == 401
    assert "resource_metadata=" in rejected.headers["www-authenticate"]
    assert "/.well-known/oauth-protected-resource/mcp" in rejected.headers["www-authenticate"]
    assert rejected.headers["access-control-allow-origin"] == "https://reader.example"
    assert sdk.requests == []


@pytest.mark.parametrize("case", ["resource", "encoding", "oversized"])
def test_oauth_endpoint_policy_errors_keep_cors_and_never_reach_credential_storage(sdk, case):
    response = sdk.client.post(
        "/token" if case != "oversized" else "/register",
        content="resource=https%3A%2F%2Fattacker.example%2Fmcp"
        if case == "resource"
        else b"x" * 32769
        if case == "oversized"
        else b"{}",
        headers={
            "Origin": "https://agent.example",
            "Content-Type": "application/json"
            if case == "encoding"
            else "application/x-www-form-urlencoded",
        },
    )
    assert response.status_code == (413 if case == "oversized" else 400)
    assert response.headers["access-control-allow-origin"] == "*"
    assert response.headers["cache-control"] == "no-store"
    sdk.redis.get.assert_not_awaited()
    sdk.redis.set.assert_not_awaited()


def test_sdk_registration_supports_public_pkce_clients_without_returning_a_client_secret(sdk):
    response = sdk.client.post(
        "/register",
        json={
            "client_name": "Reader agent",
            "redirect_uris": ["https://agent.example/callback"],
            "token_endpoint_auth_method": "none",
        },
    )
    assert response.status_code == 201
    registered = response.json()
    assert registered["client_id"] and registered["token_endpoint_auth_method"] == "none"
    assert not registered.get("client_secret")
    assert registered["redirect_uris"] == ["https://agent.example/callback"]
    assert sdk.redis.set.await_count == 1
    assert sdk.requests == []


@pytest.mark.parametrize("dependency", [None, "public", "redis", "user"])
def test_personal_readiness_checks_all_owned_dependencies_without_contacting_identity_provider(
    sdk, dependency
):
    if dependency in {"public", "user"}:
        sdk.status[dependency] = 503
    elif dependency == "redis":
        from redis.exceptions import ConnectionError

        sdk.redis.ping.side_effect = ConnectionError("private redis credentials")
    response = sdk.client.get("/health/ready")
    assert response.status_code == (200 if dependency is None else 503)
    assert response.json() == {"status": "ok" if dependency is None else "unavailable"}
    assert all(request.url.host in {"127.0.0.1", "user-api"} for request in sdk.requests)
    if dependency is None:
        assert {request.url.path for request in sdk.requests} == {"/v1/topics", "/health/ready"}
        sdk.redis.ping.assert_awaited_once()


@pytest.mark.parametrize("shape", ["none", "multiple", "mount"])
def test_transport_fails_explicitly_when_sdk_route_shape_cannot_bind_security_policy(shape):
    async def endpoint(request):
        raise AssertionError("Unexpected route invocation")

    routes = (
        []
        if shape == "none"
        else [Route("/one", endpoint), Route("/two", endpoint)]
        if shape == "multiple"
        else [Mount("/nested", app=Starlette())]
    )
    server = NS(streamable_http_app=Mock(return_value=Starlette(routes=routes)))
    with pytest.raises(RuntimeError, match="Expected one SDK MCP transport route"):
        transport.MCPRoutes(server, Settings(_env_file=None))
