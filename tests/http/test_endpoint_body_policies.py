"""Registered endpoint policies follow native routing ahead of parsers and dependencies."""

import asyncio
import json
import uuid
from typing import Annotated

import pytest
from devfeed_http.body_limits import BodyLimitedRoute, RequestBodyPolicy
from devfeed_http.operation_policies import GatewayPolicy, include_gateway_router
from fastapi import APIRouter, Depends, FastAPI, File, Request, UploadFile
from pydantic import BaseModel, ValidationError


def exchange(
    app,
    path,
    *,
    method="POST",
    chunks=(),
    headers=(),
    root_path="",
    forbid_read=False,
    receive_hook=None,
):
    events, reads = [], []
    messages = iter(chunks)

    async def receive():
        if forbid_read:
            pytest.fail("Native routing consumed an unrelated request body")
        reads.append(True)
        if receive_hook is not None:
            return await receive_hook()
        return next(messages, {"type": "http.disconnect"})

    async def send(message):
        events.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "http",
        "method": method,
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": root_path,
        "headers": list(headers),
        "server": ("testserver", 80),
        "client": ("127.0.0.1", 1234),
    }
    asyncio.run(app(scope, receive, send))
    return events, reads


def response(events):
    start = next(message for message in events if message["type"] == "http.response.start")
    body = b"".join(
        message.get("body", b"") for message in events if message["type"] == "http.response.body"
    )
    return start["status"], dict(start["headers"]), json.loads(body) if body else None


def chunk(body, *, more=False):
    return {"type": "http.request", "body": body, "more_body": more}


def catalog():
    app = FastAPI()
    leaf = APIRouter(prefix="/renamed", route_class=BodyLimitedRoute)

    @leaf.patch("/{resource_id}", openapi_extra=RequestBodyPolicy(body_limit=4).openapi_extra())
    async def renamed_endpoint(request: Request, resource_id: str):
        return {"body": (await request.body()).decode(), "resource_id": resource_id}

    parent = APIRouter(prefix="/workspace")
    parent.include_router(leaf, prefix="/nested")
    app.include_router(parent, prefix="/v9")
    app.include_router(leaf, prefix="/alternative")
    return app


@pytest.mark.parametrize("path", ["/v9/workspace/nested/renamed/tool", "/alternative/renamed/tool"])
def test_policy_follows_renamed_nested_and_reused_router_prefixes(path):
    app = catalog()
    events, reads = exchange(app, path, method="PATCH", chunks=[chunk(b"large")])
    status, headers, body = response(events)
    assert status == 413 and body == {"detail": "Request too large"}
    assert headers[b"cache-control"] == b"no-store"
    assert reads == [True]
    events, reads = exchange(
        app, path, method="PATCH", chunks=[chunk("é".encode(), more=True), chunk("é".encode())]
    )
    assert response(events)[0] == 200
    assert response(events)[2] == {"body": "éé", "resource_id": "tool"}
    assert len(reads) == 2


@pytest.mark.parametrize("root_path", ["", "/edge"])
def test_policy_follows_mounted_app_and_external_root_path(root_path):
    app = FastAPI()
    app.mount("/proxy", catalog())
    events, reads = exchange(
        app,
        root_path + "/proxy/alternative/renamed/tool",
        method="PATCH",
        root_path=root_path,
        headers=[(b"content-length", b"5")],
        forbid_read=True,
    )
    assert response(events)[0] == 413 and not reads


@pytest.mark.parametrize(
    "method,path,status",
    [
        ("POST", "/alternative/renamed/tool", 405),
        ("GET", "/alternative/renamed/tool", 405),
        ("DELETE", "/alternative/renamed/tool", 405),
        ("PATCH", "/unavailable", 404),
        ("PATCH", "/alternative/renamed/tool/", 307),
    ],
)
def test_native_method_not_found_and_slash_redirect_paths_do_not_read_body(method, path, status):
    events, reads = exchange(
        catalog(),
        path,
        method=method,
        headers=[(b"content-length", b"100000")],
        forbid_read=True,
    )
    start = next(message for message in events if message["type"] == "http.response.start")
    assert start["status"] == status and not reads
    if status == 405:
        assert dict(start["headers"])[b"allow"] == b"PATCH"
    if status == 307:
        assert dict(start["headers"])[b"location"].endswith(b"/alternative/renamed/tool")


def test_unselected_gateway_operations_and_get_routes_do_not_acquire_a_body_guard():
    app, router = FastAPI(), APIRouter(route_class=BodyLimitedRoute)

    @router.post("/ordinary", openapi_extra=GatewayPolicy(body_limit=4).openapi_extra())
    async def ordinary(request: Request):
        return {"bytes": len(await request.body())}

    @router.get("/status")
    async def status():
        return {"status": "ok"}

    include_gateway_router(app, router)
    events, reads = exchange(
        app,
        "/ordinary",
        headers=[(b"content-length", b"100000")],
        chunks=[chunk(b"larger than four bytes")],
    )
    assert response(events)[0] == 200 and response(events)[2] == {"bytes": 22}
    assert reads == [True]
    events, reads = exchange(
        app, "/status", method="GET", headers=[(b"content-length", b"100000")], forbid_read=True
    )
    assert response(events)[0] == 200 and not reads


class Payload(BaseModel):
    name: str


@pytest.mark.parametrize("rejection", ["json", "image"])
@pytest.mark.parametrize("body_kind", ["declared", "chunked", "stalled"])
def test_bound_endpoint_rejects_before_json_multipart_auth_database_or_rate_dependencies(
    monkeypatch, rejection, body_kind
):
    app, router = FastAPI(), APIRouter(route_class=BodyLimitedRoute)
    calls = []

    def dependency():
        calls.append("auth/database")
        pytest.fail("Oversized body reached authentication/database dependency")

    def rate_limit():
        calls.append("rate")
        pytest.fail("Oversized body consumed an actor's rate budget")

    async def parser(*args, **kwargs):
        calls.append("parser")
        pytest.fail("Oversized body reached a JSON/multipart parser")

    policy = RequestBodyPolicy(body_limit=4, body_timeout_ms=10, rejection=rejection)
    if rejection == "json":
        monkeypatch.setattr(Request, "json", parser)

        @router.post(
            "/changed-name",
            openapi_extra=policy.openapi_extra(),
            dependencies=[Depends(dependency), Depends(rate_limit)],
        )
        async def json_endpoint(payload: Payload):
            pytest.fail("Oversized JSON body reached the endpoint")
    else:
        monkeypatch.setattr(Request, "form", parser)

        @router.post(
            "/changed-name",
            openapi_extra=policy.openapi_extra(),
            dependencies=[Depends(dependency), Depends(rate_limit)],
        )
        async def multipart_endpoint(file: Annotated[UploadFile, File()]):
            pytest.fail("Oversized multipart body reached the endpoint")

    app.include_router(router, prefix="/other-prefix")
    headers = [
        (
            b"content-type",
            b"application/json"
            if rejection == "json"
            else b"multipart/form-data; boundary=fixture",
        )
    ]
    if body_kind == "declared":
        headers.append((b"content-length", b"5"))
    cancelled = []

    async def stalled_receive():
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)

    events, reads = exchange(
        app,
        "/other-prefix/changed-name",
        headers=headers,
        chunks=[chunk(b"abc", more=True), chunk(b"de")],
        forbid_read=body_kind == "declared",
        receive_hook=stalled_receive if body_kind == "stalled" else None,
    )
    status, headers, body = response(events)
    assert status == (408 if body_kind == "stalled" else 413) and not calls
    if body_kind == "stalled":
        assert body == {
            "detail": "Incomplete request" if rejection == "json" else "Image upload timed out"
        }
        assert cancelled == [True]
    else:
        assert body == {
            "detail": "Request too large" if rejection == "json" else "Choose an image under 5 MB"
        }
    assert headers.get(b"cache-control") == (b"no-store" if rejection == "json" else None)
    assert len(reads) == {"declared": 0, "chunked": 2, "stalled": 1}[body_kind]


def test_bounded_json_and_multipart_bytes_remain_usable_by_real_fastapi_parsers():
    app, router = FastAPI(), APIRouter(route_class=BodyLimitedRoute)
    calls = []

    def dependency():
        calls.append("dependency")

    @router.post(
        "/json",
        openapi_extra=RequestBodyPolicy(body_limit=128).openapi_extra(),
        dependencies=[Depends(dependency)],
    )
    async def json_endpoint(payload: Payload, request: Request):
        assert await request.body() == '{"name":"é"}'.encode()
        return payload

    @router.post(
        "/file",
        openapi_extra=RequestBodyPolicy(body_limit=512, rejection="image").openapi_extra(),
        dependencies=[Depends(dependency)],
    )
    async def file_endpoint(file: Annotated[UploadFile, File()]):
        return {"filename": file.filename, "body": (await file.read()).decode()}

    app.include_router(router)
    events, _ = exchange(
        app,
        "/json",
        headers=[(b"content-type", b"application/json")],
        chunks=[chunk(b'{"name":"\xc3', more=True), chunk(b'\xa9"}')],
    )
    assert response(events)[0] == 200 and response(events)[2] == {"name": "é"}
    multipart = (
        b'--fixture\r\nContent-Disposition: form-data; name="file"; filename="native.txt"\r\n'
        b"Content-Type: text/plain\r\n\r\nhello\r\n--fixture--\r\n"
    )
    events, _ = exchange(
        app,
        "/file",
        headers=[(b"content-type", b"multipart/form-data; boundary=fixture")],
        chunks=[chunk(multipart[:45], more=True), chunk(multipart[45:])],
    )
    assert response(events)[0] == 200 and response(events)[2] == {
        "filename": "native.txt",
        "body": "hello",
    }
    assert calls == ["dependency", "dependency"]


def test_typed_uuid_validation_and_uuid_path_converter_keep_native_match_precedence():
    app, router = FastAPI(), APIRouter(route_class=BodyLimitedRoute)
    policy = RequestBodyPolicy(body_limit=4).openapi_extra()

    async def endpoint(resource_id: uuid.UUID):
        return {"id": str(resource_id)}

    router.add_api_route("/typed/{resource_id}", endpoint, methods=["POST"], openapi_extra=policy)
    router.add_api_route(
        "/converted/{resource_id:uuid}", endpoint, methods=["POST"], openapi_extra=policy
    )
    app.include_router(router)
    events, reads = exchange(
        app, "/typed/not-a-uuid", headers=[(b"content-length", b"5")], forbid_read=True
    )
    assert response(events)[0] == 413 and not reads
    events, _ = exchange(app, "/typed/not-a-uuid", chunks=[chunk(b"{}")])
    assert response(events)[0] == 422
    events, reads = exchange(
        app, "/converted/not-a-uuid", headers=[(b"content-length", b"5")], forbid_read=True
    )
    assert response(events)[0] == 404 and not reads


@pytest.mark.parametrize("declaration", [{"bodyLimit": 0}, None])
def test_invalid_endpoint_policy_fails_registration_instead_of_silently_disabling_protection(
    declaration,
):
    router = APIRouter(route_class=BodyLimitedRoute)

    async def endpoint():
        return {"ok": True}

    with pytest.raises(ValidationError):
        router.add_api_route(
            "/invalid",
            endpoint,
            methods=["POST"],
            openapi_extra={"x-devfeed-request-body": declaration},
        )
