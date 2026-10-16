"""Runtime behavior belongs to registered operations, independent of URL spelling."""

import importlib

import pytest
from devfeed_http.inbox import INBOX_OPERATIONS
from devfeed_http.route_policies import RouteRuntimePolicy, install_runtime_policies
from fastapi import APIRouter, FastAPI, Request
from fastapi.routing import APIRoute, iter_route_contexts
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.responses import JSONResponse
from starlette.routing import Route


@pytest.mark.parametrize(
    "field,value",
    [
        ("admission", "all"),
        ("admission", None),
        ("admission", True),
        ("telemetry_excluded", "true"),
        ("telemetry_excluded", 1),
        ("telemetry_excluded", None),
        ("logging_level", "warning"),
        ("logging_level", 10),
        ("logging_level", None),
    ],
)
def test_runtime_policy_rejects_unknown_modes_and_coerced_values(field, value):
    with pytest.raises(ValidationError):
        RouteRuntimePolicy(**{field: value})


def test_runtime_policy_is_frozen_and_rejects_unregistered_fields():
    policy = RouteRuntimePolicy(admission="stream")
    with pytest.raises(ValidationError):
        policy.admission = "exempt"
    with pytest.raises(ValidationError):
        RouteRuntimePolicy(path="/stream")


@pytest.mark.parametrize(
    "path",
    ["/health/live", "/health/ready", "/version", "/v1/user/notifications/chimely/v1/inbox/stream"],
)
def test_special_url_spelling_does_not_supply_a_runtime_policy(path):
    assert RouteRuntimePolicy.from_scope({"type": "http", "method": "GET", "path": path}) == (
        RouteRuntimePolicy()
    )


def test_policy_survives_nested_router_prefixes_and_preserves_unrelated_metadata():
    policy = RouteRuntimePolicy(admission="exempt", telemetry_excluded=True, logging_level="debug")
    app = FastAPI()
    outer, inner = APIRouter(prefix="/changed"), APIRouter(prefix="/nested")

    @inner.get(
        "/status/{identifier}",
        openapi_extra={"x-unrelated": {"keep": True}, **policy.openapi_extra()},
    )
    def endpoint(request: Request, identifier: str):
        assert RouteRuntimePolicy.from_scope(request.scope) == policy
        return {"identifier": identifier}

    outer.include_router(inner)
    app.include_router(outer)
    install_runtime_policies(app)
    # Installing twice must not wrap or charge an operation twice.
    install_runtime_policies(app)
    with TestClient(app) as client:
        response = client.get("/changed/nested/status/private-identifier")
    assert response.status_code == 200
    assert response.json() == {"identifier": "private-identifier"}
    operation = app.openapi()["paths"]["/changed/nested/status/{identifier}"]["get"]
    assert operation["x-unrelated"] == {"keep": True}
    for name, value in policy.openapi_extra().items():
        assert operation[name] == value


@pytest.mark.parametrize("included", [False, True])
def test_starlette_operation_policy_survives_native_router_inclusion(included):
    app = FastAPI()
    policy = RouteRuntimePolicy(admission="stream", logging_level="debug")

    async def endpoint(request):
        assert RouteRuntimePolicy.from_scope(request.scope) == policy
        return JSONResponse({"ok": True})

    route = Route("/events/{identifier}", endpoint=endpoint, methods=["GET"])
    route.openapi_extra = policy.openapi_extra()
    if included:
        router = APIRouter(routes=[route])
        app.include_router(router, prefix="/nested")
    else:
        app.routes.append(route)
    install_runtime_policies(app)
    path = "/nested/events/private-id" if included else "/events/private-id"
    with TestClient(app) as client:
        assert client.get(path).status_code == 200
        assert client.post(path).status_code == 405


@pytest.mark.parametrize(
    "package", ["devfeed_api", "devfeed_user_api", "devfeed_admin_api", "devfeed_partner_api"]
)
def test_service_health_operations_declare_distinct_runtime_policies(package):
    app = importlib.import_module(package + ".main").create_app()
    metadata = {
        context.path_format: context.openapi_extra or {}
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
    }
    expected = {
        "/health/live": RouteRuntimePolicy(
            admission="exempt", telemetry_excluded=True, logging_level="debug"
        ),
        "/health/ready": RouteRuntimePolicy(telemetry_excluded=True, logging_level="debug"),
    }
    if package == "devfeed_api":
        expected["/version"] = RouteRuntimePolicy(telemetry_excluded=True)
    for path, policy in expected.items():
        for name, value in policy.openapi_extra().items():
            assert metadata[path][name] == value


@pytest.mark.parametrize("package", ["devfeed_user_api", "devfeed_admin_api"])
def test_only_event_stream_inbox_operation_declares_stream_admission(package):
    app = importlib.import_module(package + ".main").create_app()
    prefix = "userInbox" if package == "devfeed_user_api" else "adminInbox"
    operations = {
        context.original_route.operation_id: context.openapi_extra or {}
        for context in iter_route_contexts(app.routes)
        if (getattr(context.original_route, "operation_id", None) or "").startswith(prefix)
    }
    assert len(operations) == len(INBOX_OPERATIONS)
    for operation in INBOX_OPERATIONS:
        expected = RouteRuntimePolicy(
            admission="stream" if operation.response_mode == "events" else "request"
        )
        for name, value in expected.openapi_extra().items():
            assert (
                operations[prefix + operation.name].get(
                    name, RouteRuntimePolicy().openapi_extra()[name]
                )
                == value
            )
