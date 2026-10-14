"""Exported gateway policy follows registered operations, including hidden transport routes."""

import uuid

import pytest
from devfeed_admin_api.main import create_app
from devfeed_http.operation_policies import (
    DEFAULT_GATEWAY_POLICY,
    GATEWAY_POLICY_EXTENSION,
    GATEWAY_ROUTES_EXTENSION,
    ExtensionTransportPolicy,
    GatewayPolicy,
    ReaderGatewayPolicy,
    export_gateway_document,
    include_gateway_router,
)
from fastapi import APIRouter, FastAPI, Path
from pydantic import ValidationError


@pytest.mark.parametrize("field", ["body_limit", "body_timeout_ms", "upstream_timeout_ms"])
@pytest.mark.parametrize("value", [0, -1, True, "2000"])
def test_gateway_policy_rejects_invalid_limits(field, value):
    with pytest.raises(ValidationError):
        GatewayPolicy(**{field: value})


def test_gateway_policy_has_fixed_defaults_and_rejects_unknown_fields():
    assert DEFAULT_GATEWAY_POLICY.openapi_extra() == {
        GATEWAY_POLICY_EXTENSION: {
            "bodyLimit": 1_000_000,
            "bodyTimeoutMs": 2000,
            "upstreamTimeoutMs": 45_000,
        }
    }
    with pytest.raises(ValidationError):
        GatewayPolicy(path="/special")
    with pytest.raises(ValidationError):
        DEFAULT_GATEWAY_POLICY.body_limit = 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("cookie_policy", "all"),
        ("cookie_policy", None),
        ("handler", "arbitrary-script"),
        ("handler", None),
    ],
)
def test_reader_policy_rejects_unknown_cookie_or_handler_modes(field, value):
    with pytest.raises(ValidationError):
        ReaderGatewayPolicy(**{field: value})


@pytest.mark.parametrize(
    "field,value",
    [
        ("credentials", "same-origin"),
        ("cache_control", "max-age=600"),
        ("request_headers", ("Cookie",)),
        ("request_headers", ("Authorization",)),
        ("request_headers", ["Content-Type"]),
        ("timeout_ms", 0),
        ("timeout_ms", True),
        ("timeout_ms", "15000"),
    ],
)
def test_extension_policy_rejects_unsupported_capabilities_and_non_strict_values(field, value):
    with pytest.raises(ValidationError):
        ExtensionTransportPolicy(**{field: value})


def test_extension_policy_is_frozen_and_reader_policy_denies_access_without_an_explicit_grant():
    policy = ExtensionTransportPolicy()
    assert policy.model_dump(by_alias=True, mode="json") == {
        "credentials": "include",
        "cacheControl": None,
        "requestHeaders": ["Content-Type", "X-CSRF-Token", "If-None-Match"],
        "timeoutMs": 15_000,
    }
    assert ReaderGatewayPolicy().openapi_extra()[GATEWAY_POLICY_EXTENSION]["extension"] is None
    with pytest.raises(ValidationError):
        policy.timeout_ms = 1
    with pytest.raises(ValidationError):
        ExtensionTransportPolicy(allowed=True)


def test_reader_router_uses_its_policy_subclass_for_defaults_and_hidden_overrides():
    app, router = FastAPI(), APIRouter(prefix="/renamed")

    @router.put("/settings", operation_id="ordinaryWrite")
    def ordinary():
        return {}

    @router.api_route(
        "/inbox/{path:path}",
        methods=["GET", "POST"],
        include_in_schema=False,
        openapi_extra=ReaderGatewayPolicy(cookie_policy="omit").openapi_extra(),
    )
    def hidden(path: str):
        return {}

    include_gateway_router(app, router, default_policy=ReaderGatewayPolicy())
    document = export_gateway_document(app, policy_type=ReaderGatewayPolicy)
    policies = [operation["policy"] for operation in document[GATEWAY_ROUTES_EXTENSION]]
    assert policies == [
        ReaderGatewayPolicy().model_dump(by_alias=True),
        ReaderGatewayPolicy(cookie_policy="omit").model_dump(by_alias=True),
        ReaderGatewayPolicy(cookie_policy="omit").model_dump(by_alias=True),
    ]
    assert document["paths"]["/renamed/settings"]["put"]["operationId"] == "ordinaryWrite"
    with pytest.raises(ValidationError):
        export_gateway_document(app)


def specimen():
    app = FastAPI()
    router = APIRouter(prefix="/changed-prefix")

    @app.get("/health/live")
    def live():
        return {}

    @router.get("/static", operation_id="unchangedStatic")
    def static():
        return {}

    @router.patch(
        "/products/{product_id:uuid}",
        operation_id="unchangedUpdate",
        openapi_extra={
            "x-unrelated": {"keep": True},
            **GatewayPolicy(body_limit=65536, upstream_timeout_ms=120_000).openapi_extra(),
        },
    )
    def update(product_id: uuid.UUID):
        return {}

    @router.get("/workers/{worker_name}", operation_id="unchangedWorker")
    def worker(worker_name: str = Path(min_length=1, max_length=256)):
        return {}

    @router.api_route("/inbox/{path:path}", methods=["GET", "PUT", "POST"], include_in_schema=False)
    def inbox(path: str):
        return {}

    include_gateway_router(app, router)
    return app


def test_policy_export_preserves_order_schemas_ids_and_explicit_exposure():
    app = specimen()
    native = app.openapi()
    document = export_gateway_document(app)
    operations = document[GATEWAY_ROUTES_EXTENSION]
    assert [operation["path"] for operation in operations] == [
        "/changed-prefix/static",
        "/changed-prefix/products/{product_id}",
        "/changed-prefix/workers/{worker_name}",
        "/changed-prefix/inbox/{path}",
        "/changed-prefix/inbox/{path}",
        "/changed-prefix/inbox/{path}",
    ]
    assert [operation["method"] for operation in operations] == [
        "GET",
        "PATCH",
        "GET",
        "GET",
        "POST",
        "PUT",
    ]
    assert document["paths"] == native["paths"]
    assert document["components"] == native["components"]
    assert GATEWAY_ROUTES_EXTENSION not in native
    assert GATEWAY_POLICY_EXTENSION not in native["paths"]["/health/live"]["get"]
    patch = document["paths"]["/changed-prefix/products/{product_id}"]["patch"]
    assert patch["operationId"] == "unchangedUpdate"
    assert patch["x-unrelated"] == {"keep": True}
    assert operations[1]["policy"] == {
        "bodyLimit": 65536,
        "bodyTimeoutMs": 2000,
        "upstreamTimeoutMs": 120_000,
    }
    assert operations[1]["parameters"][0]["converter"] == "uuid"
    assert operations[1]["parameters"][0]["schema"]["format"] == "uuid"
    worker = operations[2]["parameters"][0]
    assert worker["converter"] == "str"
    assert worker["schema"]["minLength"] == 1 and worker["schema"]["maxLength"] == 256


def test_hidden_routes_export_deterministic_identity_and_path_converter():
    first = export_gateway_document(specimen())
    second = export_gateway_document(specimen())
    hidden = first[GATEWAY_ROUTES_EXTENSION][-3:]
    assert first[GATEWAY_ROUTES_EXTENSION] == second[GATEWAY_ROUTES_EXTENSION]
    assert len({operation["operationId"] for operation in hidden}) == 3
    assert "/changed-prefix/inbox/{path}" not in first["paths"]
    for operation in hidden:
        assert operation["parameters"] == [
            {"name": "path", "schema": {"type": "string"}, "converter": "path"}
        ]


def test_nested_router_uses_effective_prefix_and_retains_endpoint_policy():
    app, parent, child = FastAPI(), APIRouter(prefix="/parent"), APIRouter(prefix="/child")

    @child.post(
        "/{identifier}",
        operation_id="nestedOperation",
        openapi_extra=GatewayPolicy(upstream_timeout_ms=150_000).openapi_extra(),
    )
    def endpoint(identifier: uuid.UUID):
        return {}

    parent.include_router(child)
    include_gateway_router(app, parent)
    operation = export_gateway_document(app)[GATEWAY_ROUTES_EXTENSION][0]
    assert operation["path"] == "/parent/child/{identifier}"
    assert operation["policy"]["upstreamTimeoutMs"] == 150_000
    assert operation["parameters"][0]["schema"]["format"] == "uuid"


def test_admin_export_covers_auth_and_hidden_inbox_without_exposing_health():
    document = export_gateway_document(create_app())
    operations = document[GATEWAY_ROUTES_EXTENSION]
    documented = {
        (path, method.upper()): operation
        for path, methods in document["paths"].items()
        for method, operation in methods.items()
        if GATEWAY_POLICY_EXTENSION in operation
    }
    assert documented
    actual = {(operation["path"], operation["method"]): operation for operation in operations}
    assert len(actual) == len(operations)
    for key, operation in documented.items():
        assert actual[key]["operationId"] == operation["operationId"]
        assert actual[key]["policy"] == operation[GATEWAY_POLICY_EXTENSION]
    assert ("/v1/admin/auth/callback", "GET") in actual
    assert ("/v1/admin/auth/logout", "POST") in actual
    assert not any(operation["path"].startswith("/health/") for operation in operations)
    for method in ("GET", "POST", "PUT"):
        hidden = actual[("/v1/admin/notifications/chimely/v1/inbox/{path}", method)]
        assert hidden["parameters"][0]["converter"] == "path"
    expected = {
        "admin_source_create": GatewayPolicy(upstream_timeout_ms=215_000),
        "admin_source_preview": GatewayPolicy(upstream_timeout_ms=305_000),
        "admin_source_import_submit": GatewayPolicy(
            upstream_timeout_ms=150_000, body_limit=6_100_000, body_timeout_ms=10_000
        ),
        "admin_source_import_review": GatewayPolicy(upstream_timeout_ms=150_000),
        "admin_topic_github_pull": GatewayPolicy(upstream_timeout_ms=120_000),
        "adminPartnerProductCreate": GatewayPolicy(body_limit=65536),
        "adminPartnerProductUpdate": GatewayPolicy(body_limit=65536),
        "adminPartnerProductAdImageUpload": GatewayPolicy(
            body_limit=5 * 1024 * 1024 + 65536, body_timeout_ms=10_000
        ),
    }
    by_id = {operation["operationId"]: operation for operation in operations}
    for identifier, policy in expected.items():
        assert by_id[identifier]["policy"] == policy.model_dump(by_alias=True)
