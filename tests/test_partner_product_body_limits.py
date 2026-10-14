"""Malformed identifiers cannot bypass registered product and upload body policies."""

import asyncio

import httpx
import pytest
from devfeed_admin_api import partner_accounts
from devfeed_admin_api.main import create_app as create_admin_app
from devfeed_http import body_limits
from devfeed_partner_api import portal
from devfeed_partner_api.main import create_app as create_partner_app
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.requests import Request

VALID_PRODUCT_ID = "00000000-0000-4000-8000-000000000001"


def unexpected_work(*_args, **_kwargs):
    pytest.fail("Body rejection reached a parser, authentication, database or rate-limit check")


@pytest.fixture(params=["partner", "admin"])
def service(request, monkeypatch):
    factory, module, prefix = (
        (create_partner_app, portal, "/v1/partner/accounts")
        if request.param == "partner"
        else (create_admin_app, partner_accounts, "/v1/admin/partner-accounts")
    )
    app = factory()
    for context in iter_route_contexts(app.routes):
        if isinstance(context.original_route, APIRoute):
            for dependency in context.dependant.dependencies:
                if dependency.call is not None:
                    app.dependency_overrides[dependency.call] = unexpected_work
    for name in ("get_redis", "limit_product_write", "limit_ad_image_upload"):
        monkeypatch.setattr(module, name, unexpected_work)
    monkeypatch.setattr(Request, "json", unexpected_work)
    monkeypatch.setattr(Request, "form", unexpected_work)
    return app, prefix


def exchange(app, path, method, *, body_kind, limit, content_type="application/json"):
    reads, cancellations = [], []

    async def content():
        if body_kind == "stalled":
            reads.append(True)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancellations.append(True)
                raise
        if body_kind != "chunked":
            pytest.fail("A declared oversized or unmatched request consumed its body")
        for chunk in (b"x" * limit, b"x"):
            reads.append(True)
            yield chunk

    headers = {"Content-Type": content_type}
    if body_kind == "declared":
        headers["Content-Length"] = str(limit + 1)

    async def request():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://service.test"
        ) as client:
            return await client.request(method, path, headers=headers, content=content())

    return asyncio.run(request()), len(reads), cancellations


@pytest.mark.parametrize("body_kind", ["declared", "chunked", "stalled"])
@pytest.mark.parametrize(
    "method,suffix,limit,deadline,rejection",
    [
        pytest.param("POST", "/not-a-uuid/products", 65_536, 2, "json", id="create"),
        pytest.param(
            "PATCH",
            f"/not-a-uuid/products/{VALID_PRODUCT_ID}",
            65_536,
            2,
            "json",
            id="update",
        ),
        pytest.param(
            "POST",
            "/not-a-uuid/products/not-a-uuid/ad-image",
            5_308_416,
            10,
            "image",
            id="ad-image",
        ),
    ],
)
def test_invalid_ids_retain_body_limits_before_parsing_and_dependencies(
    service, monkeypatch, body_kind, method, suffix, limit, deadline, rejection
):
    app, prefix = service
    requested_deadlines = []
    original_fail_after = body_limits.anyio.fail_after

    def short_deadline(delay, *, shield=False):
        requested_deadlines.append(delay)
        return original_fail_after(0.01 if body_kind == "stalled" else delay, shield=shield)

    monkeypatch.setattr(body_limits.anyio, "fail_after", short_deadline)
    response, reads, cancellations = exchange(
        app,
        prefix + suffix,
        method,
        body_kind=body_kind,
        limit=limit,
        content_type=(
            "multipart/form-data; boundary=fixture" if rejection == "image" else "application/json"
        ),
    )
    timed_out = body_kind == "stalled"
    assert response.status_code == (408 if timed_out else 413)
    expected_detail = (
        ("Image upload timed out" if timed_out else "Choose an image under 5 MB")
        if rejection == "image"
        else ("Incomplete request" if timed_out else "Request too large")
    )
    assert response.json() == {"detail": expected_detail}
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-request-id"]
    assert ("content-length" in response.request.headers) == (body_kind == "declared")
    assert reads == {"declared": 0, "chunked": 2, "stalled": 1}[body_kind]
    assert cancellations == ([True] if timed_out else [])
    assert requested_deadlines == ([] if body_kind == "declared" else [deadline])


@pytest.mark.parametrize("account_id", ["not-a-uuid", VALID_PRODUCT_ID])
def test_converter_invalid_product_preserves_native_404_without_body_reads(service, account_id):
    app, prefix = service
    response, reads, cancellations = exchange(
        app,
        f"{prefix}/{account_id}/products/not-a-uuid",
        "PATCH",
        body_kind="declared",
        limit=65_536,
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}
    assert reads == 0
    assert cancellations == []
