"""Native route selection retains WebSocket and raw ASGI mount contracts."""

import pytest
from devfeed_http.operation_policies import (
    GATEWAY_ROUTES_EXTENSION,
    export_gateway_document,
    include_gateway_router,
)
from devfeed_http.route_policies import (
    CURRENT_ROUTE_SCOPE,
    RUNTIME_POLICY_SCOPE_KEY,
    RouteRuntimePolicy,
    install_runtime_policies,
)
from fastapi import APIRouter, FastAPI, Request, WebSocket
from fastapi.testclient import TestClient


def test_websocket_handshake_and_messages_remain_outside_http_admission_and_gateway_operations():
    app, router = FastAPI(), APIRouter(prefix="/native")

    @router.websocket("/events/{channel}")
    async def events(socket: WebSocket, channel: str):
        assert socket.scope["type"] == "websocket"
        assert RUNTIME_POLICY_SCOPE_KEY not in socket.scope
        assert CURRENT_ROUTE_SCOPE.get() is None
        await socket.accept()
        await socket.send_json({"channel": channel, "echo": await socket.receive_text()})
        await socket.close(code=1000)

    include_gateway_router(app, router)
    install_runtime_policies(app)
    install_runtime_policies(app)
    assert export_gateway_document(app)[GATEWAY_ROUTES_EXTENSION] == []
    with TestClient(app) as client, client.websocket_connect("/native/events/releases") as socket:
        socket.send_text("ready")
        assert socket.receive_json() == {"channel": "releases", "echo": "ready"}
        assert socket.receive() == {"type": "websocket.close", "code": 1000, "reason": ""}
    assert CURRENT_ROUTE_SCOPE.get() is None


def test_reinstalling_nested_app_policies_keeps_one_selected_operation_and_its_declared_policy():
    app, child = FastAPI(), FastAPI()
    policy = RouteRuntimePolicy(admission="stream", logging_level="debug")
    calls = []

    @child.get("/logo.png", openapi_extra=policy.openapi_extra())
    def files(request: Request):
        calls.append(request.scope["path"])
        assert RouteRuntimePolicy.from_scope(request.scope) == policy
        assert CURRENT_ROUTE_SCOPE.get() is request.scope
        return {"path": request.scope["path"], "root_path": request.scope["root_path"]}

    app.mount("/assets", child)
    install_runtime_policies(app)
    install_runtime_policies(app)
    with TestClient(app) as client:
        response = client.get("/assets/logo.png")
        assert response.status_code == 200
        assert response.json() == {"path": "/assets/logo.png", "root_path": "/assets"}
    assert calls == ["/assets/logo.png"] and CURRENT_ROUTE_SCOPE.get() is None


@pytest.mark.parametrize("documented", [False, True])
def test_gateway_export_rejects_an_unvalidated_path_variable_instead_of_exporting_unchecked_input(
    documented,
):
    app, router = FastAPI(), APIRouter()

    @router.get("/collections/{unchecked_identity}", include_in_schema=documented)
    def ignored_identity():
        return {"items": []}

    include_gateway_router(app, router)
    with pytest.raises(
        ValueError, match="Gateway path parameter unchecked_identity has no validation schema"
    ):
        export_gateway_document(app)
