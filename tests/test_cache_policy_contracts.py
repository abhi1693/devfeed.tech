"""Public caching is an explicit registered-operation contract."""

import uuid

import pytest
from devfeed_api.cache import CACHE_POLICY_EXTENSION, CachedReadRoute, CachePolicy
from devfeed_api.main import create_app
from fastapi import APIRouter
from fastapi.routing import APIRoute, iter_route_contexts
from pydantic import ValidationError
from starlette.requests import Request

EXPECTED = {
    "/v1/feed": ("feed", "content"),
    "/v1/feed/options": ("feed-options", "content"),
    "/v1/articles/{article_id}": ("article", "content"),
    "/v1/search": ("search", "content"),
    "/v1/search/suggestions": ("search-suggestions", "content"),
    "/v1/sources": ("sources", "metadata"),
    "/v1/sources/{source_id}": ("source", "metadata"),
    "/v1/tags": ("tags", "metadata"),
    "/v1/tags/{slug}": ("tag", "metadata"),
    "/v1/topics": ("topics", "metadata"),
    "/v1/topics/{slug}": ("topic", "metadata"),
    "/v1/topics/{slug}/relations": ("topic-relations", "metadata"),
}


def test_public_cache_inventory_marks_only_the_twelve_shared_read_operations():
    app = create_app()
    actual = {}
    namespaces = set()
    for context in iter_route_contexts(app.routes):
        if not isinstance(context.original_route, APIRoute):
            continue
        extra = context.openapi_extra or {}
        if CACHE_POLICY_EXTENSION not in extra:
            continue
        assert isinstance(context.original_route, CachedReadRoute)
        assert context.methods == {"GET"}
        policy = CachePolicy.model_validate(extra[CACHE_POLICY_EXTENSION])
        assert policy.namespace not in namespaces
        namespaces.add(policy.namespace)
        actual[context.path] = (policy.namespace, policy.ttl)
        assert policy.revision == 1
        exported = app.openapi()["paths"][context.path]["get"][CACHE_POLICY_EXTENSION]
        assert exported == policy.model_dump()
    assert actual == EXPECTED


@pytest.mark.parametrize(
    "changes",
    [
        {"namespace": ""},
        {"namespace": "UPPERCASE"},
        {"namespace": "contains/slash"},
        {"namespace": "a" * 65},
        {"namespace": 123},
        {"revision": 0},
        {"revision": -1},
        {"revision": True},
        {"revision": "1"},
        {"ttl": "prefix"},
        {"ttl": 60},
        {"path": "/legacy"},
    ],
)
def test_invalid_cache_declarations_fail_during_route_registration(changes):
    policy = {"namespace": "example", "revision": 1, "ttl": "content", **changes}
    router = APIRouter(route_class=CachedReadRoute)
    with pytest.raises(ValidationError):
        router.add_api_route(
            "/anything",
            lambda: {},
            methods=["GET"],
            openapi_extra={CACHE_POLICY_EXTENSION: policy},
        )


def test_cache_policy_is_frozen_and_cannot_silently_change_registered_behavior():
    policy = CachePolicy(namespace="example", revision=1, ttl="content")
    with pytest.raises(ValidationError):
        policy.revision = 2


def test_structured_identity_keeps_path_parameters_separate_from_query_and_ignores_raw_url():
    policy = CachePolicy(namespace="record", revision=1, ttl="content")
    identity = uuid.uuid4()

    def request(parameters, query=b"", path="/old"):
        return Request(
            {
                "type": "http",
                "path": path,
                "root_path": "/mount",
                "path_params": parameters,
                "query_string": query,
            }
        )

    first = policy.identity(request({"tenant": "one", "record": identity}, b"b=2&a=1"))
    assert first == policy.identity(
        request({"record": str(identity), "tenant": "one"}, b"a=1&b=2", path="/renamed")
    )
    for parameters, query in (
        ({"tenant": "two", "record": identity}, b"b=2&a=1"),
        ({"tenant": "one", "record": uuid.uuid4()}, b"b=2&a=1"),
        ({"record": identity}, b"tenant=one&b=2&a=1"),
        ({"tenant": "one", "record": identity}, b"b=2&a=1&a=2"),
    ):
        assert policy.identity(request(parameters, query)) != first
    assert policy.identity(request({}, b"a=1&a=2")) != policy.identity(request({}, b"a=2&a=1"))
