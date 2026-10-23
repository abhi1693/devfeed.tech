"""The registered service endpoints retain their exact body limits and gateway contracts."""

import asyncio
import re

import httpx
import pytest
from devfeed_admin_api.main import create_app as create_admin_app
from devfeed_api.main import create_app as create_public_app
from devfeed_http.body_limits import (
    REQUEST_BODY_POLICY_EXTENSION,
    BodyLimitedRoute,
    RequestBodyPolicy,
)
from devfeed_http.operation_policies import GATEWAY_POLICY_EXTENSION
from devfeed_partner_api.main import create_app as create_partner_app
from devfeed_user_api.main import create_app as create_user_app
from fastapi.routing import APIRoute, iter_route_contexts

APPLICATIONS = {
    "public": create_public_app,
    "user": create_user_app,
    "admin": create_admin_app,
    "partner": create_partner_app,
}
EXPECTED = {
    "public": {
        "successful_click_v1_search_analytics_click_post": (4096, 2000, "json"),
        "track_event_v1_partner_tracking_events_post": (2048, 2000, "json"),
        "placements_v1_partner_placements_query_post": (16384, 2000, "json"),
    },
    "user": {
        "user_auth_backchannel_logout": (16384, 2000, "json"),
        "upload_avatar_v1_user_settings_profile_avatar_post": (5_308_416, 10_000, "image"),
        "placements_v1_user_partner_placements_query_post": (16384, 2000, "json"),
    },
    "admin": {
        "admin_auth_backchannel_logout": (16384, 2000, "json"),
        "adminPartnerProductCreate": (65_536, 2000, "json"),
        "adminPartnerProductUpdate": (65_536, 2000, "json"),
        "adminPartnerProductAdImageUpload": (5_308_416, 10_000, "image"),
        "adminPartnerConnectionLogoUpload": (5_308_416, 10_000, "image"),
        "adminPartnerConnectionLogoUpdate": (65_536, 2000, "json"),
    },
    "partner": {
        "partner_auth_backchannel_logout": (16384, 2000, "json"),
        "partnerProductCreate": (65_536, 2000, "json"),
        "partnerProductUpdate": (65_536, 2000, "json"),
        "partnerProductAdImageUpload": (5_308_416, 10_000, "image"),
        "partnerConnectionLogoUpload": (5_308_416, 10_000, "image"),
        "partnerConnectionLogoUpdate": (65_536, 2000, "json"),
    },
}


@pytest.fixture(params=tuple(APPLICATIONS))
def application(request):
    return request.param, APPLICATIONS[request.param]()


def protected_routes(app):
    return [
        context
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        and REQUEST_BODY_POLICY_EXTENSION in (context.openapi_extra or {})
    ]


def test_registered_service_operations_retain_their_explicit_body_policies(application):
    service, app = application
    actual = {}
    for context in protected_routes(app):
        identity = context.operation_id or context.unique_id
        policy = RequestBodyPolicy.model_validate(
            context.openapi_extra[REQUEST_BODY_POLICY_EXTENSION]
        )
        assert identity not in actual
        actual[identity] = (policy.body_limit, policy.body_timeout_ms, policy.rejection)
        assert isinstance(context.original_route, BodyLimitedRoute)
        assert context.original_route.body_policy == policy
    assert actual == EXPECTED[service]


def test_gateway_defaults_do_not_turn_unselected_native_routes_into_bounded_endpoints(application):
    service, app = application
    gateway_defaults = 0
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        extra = context.openapi_extra or {}
        if REQUEST_BODY_POLICY_EXTENSION in extra:
            continue
        if GATEWAY_POLICY_EXTENSION in extra:
            gateway_defaults += 1
        if isinstance(context.original_route, BodyLimitedRoute):
            assert context.original_route.body_policy is None
    assert gateway_defaults > 0 if service != "public" else gateway_defaults == 0


def test_backend_body_limits_share_gateway_numbers_without_exposing_public_gateway_routes(
    application,
):
    service, app = application
    for context in protected_routes(app):
        extra = context.openapi_extra
        body = extra[REQUEST_BODY_POLICY_EXTENSION]
        if service == "public":
            assert GATEWAY_POLICY_EXTENSION not in extra
        else:
            gateway = extra[GATEWAY_POLICY_EXTENSION]
            assert body["bodyLimit"] == gateway["bodyLimit"]
            assert body["bodyTimeoutMs"] == gateway["bodyTimeoutMs"]


def test_real_service_routes_reject_oversized_requests_before_authentication_or_database_work(
    application,
):
    _, app = application

    def unexpected_dependency():
        pytest.fail("Oversized request reached authentication or a database dependency")

    selected = protected_routes(app)
    for context in selected:
        for dependency in context.dependant.dependencies:
            if dependency.call is not None:
                app.dependency_overrides[dependency.call] = unexpected_dependency

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://service.test"
        ) as client:
            for context in selected:
                policy = RequestBodyPolicy.model_validate(
                    context.openapi_extra[REQUEST_BODY_POLICY_EXTENSION]
                )
                path = re.sub(
                    r"\{[^}]+\}", "00000000-0000-4000-8000-000000000001", context.path_format
                )
                assert len(context.methods) == 1
                response = await client.request(
                    next(iter(context.methods)),
                    path,
                    headers={"Content-Length": str(policy.body_limit + 1)},
                    content=b"",
                )
                assert response.status_code == 413
                assert response.json() == {
                    "detail": (
                        "Choose an image under 5 MB"
                        if policy.rejection == "image"
                        else "Request too large"
                    )
                }
                assert response.headers["cache-control"] == "no-store"
                assert response.headers["x-request-id"]

    asyncio.run(exercise())
