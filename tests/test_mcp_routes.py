"""Exercise policy ownership through relocated and mounted SDK route objects."""

import httpx
import pytest
from devfeed_mcp.config import Settings
from devfeed_mcp.main import create_app
from mcp.types import CLIENT_CAPABILITIES_META_KEY, HEADER_MISMATCH, PROTOCOL_VERSION_META_KEY
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, compile_path
from starlette.testclient import TestClient

PROTOCOLS = ["2025-11-25", "2026-07-28"]


def relocate(route: Route, path: str) -> None:
    """Change the routing fields while preserving the endpoint and its policies."""
    route.path = path
    route.path_regex, route.path_format, route.param_convertors = compile_path(path)


def rpc_request(version: str, method="tools/list", **params):
    headers = {
        "Accept": "application/json, text/event-stream",
        "Mcp-Protocol-Version": version,
    }
    if version == "2026-07-28":
        params["_meta"] = {
            PROTOCOL_VERSION_META_KEY: version,
            CLIENT_CAPABILITIES_META_KEY: {},
        }
        headers["Mcp-Method"] = method
        if method == "tools/call":
            headers["Mcp-Name"] = params["name"]
    return {
        "headers": headers,
        "json": {"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    }


async def ordinary_endpoint(request):
    return JSONResponse({"body_bytes": len(await request.body())})


@pytest.mark.parametrize("version", PROTOCOLS)
def test_sdk_transport_policies_follow_actual_route_through_rename_and_mount(version):
    requests = []

    def upstream(request):
        requests.append(request)
        return httpx.Response(200, json=[])

    app = create_app(
        Settings(web_url="https://reader.example"), transport=httpx.MockTransport(upstream)
    )
    relocate(app.state.mcp_routes.transport, "/agent")
    mounted = Starlette(
        routes=[
            Route("/mcp", ordinary_endpoint, methods=["GET", "POST"]),
            Mount("/gateway", app=app),
        ],
        lifespan=app.router.lifespan_context,
    )
    with TestClient(mounted, base_url="http://localhost") as client:
        redirect = client.get(
            "/gateway/agent?token=private",
            headers={"Accept": "text/html"},
            follow_redirects=False,
        )
        assert redirect.status_code == 302
        assert redirect.headers["location"] == "https://reader.example/mcp"
        assert redirect.headers["cache-control"] == "no-store"
        assert client.get("/mcp", headers={"Accept": "text/html"}).status_code == 200
        response = client.post("/gateway/agent", **rpc_request(version))
        assert response.status_code == 200, response.text
        assert len(response.json()["result"]["tools"]) == 6
        assert "mcp-session-id" not in response.headers
        assert client.post("/gateway/agent", content=b"x" * 32769).status_code == 413
        response = client.post("/mcp", content=b"x" * 32769)
        assert response.json() == {"body_bytes": 32769}
        assert client.post("/gateway/mcp", **rpc_request(version)).status_code == 404
        assert not requests


@pytest.mark.parametrize("version", PROTOCOLS)
def test_relocated_transport_preserves_sdk_host_origin_and_protocol_validation(version):
    app = create_app(Settings(web_url="https://reader.example"))
    relocate(app.state.mcp_routes.transport, "/agent")
    with TestClient(app, base_url="http://localhost") as client:
        request = rpc_request(version)
        request["headers"]["Origin"] = "https://attacker.example"
        assert client.post("/agent", **request).status_code == 403
        request = rpc_request(version)
        request["headers"]["Host"] = "attacker.example"
        assert client.post("/agent", **request).status_code == 421
        response = client.get(
            "/agent",
            headers={
                "Accept": "text/html, text/event-stream",
                "Origin": "https://attacker.example",
            },
            follow_redirects=False,
        )
        assert response.status_code == 403
        assert not response.is_redirect


def test_sdk_modern_header_body_mismatch_remains_sdk_validation():
    app = create_app()
    relocate(app.state.mcp_routes.transport, "/agent")
    with TestClient(app, base_url="http://localhost") as client:
        request = rpc_request("2026-07-28")
        request["headers"]["Mcp-Method"] = "tools/call"
        response = client.post("/agent", **request)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == HEADER_MISMATCH
