"""Every owned HTTP endpoint must have a usable, validated API contract."""

import importlib
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, get_args
from unittest.mock import Mock

import pytest
from devfeed_http.schemas import ErrorResponse, UnhealthyResponse
from fastapi import Response
from fastapi.responses import RedirectResponse
from fastapi.testclient import TestClient
from pydantic import BaseModel
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.exc import OperationalError
from test_api_async_safety import SERVICES, api_routes


def assert_typed(annotation, seen):
    assert annotation not in (Any, dict, list), f"Unstructured contract: {annotation}"
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        for field in annotation.model_fields.values():
            assert_typed(field.annotation, seen)
    for argument in get_args(annotation):
        assert_typed(argument, seen)


@pytest.mark.parametrize("package", SERVICES)
def test_all_routes_declare_typed_bodies_and_responses(package):
    app = importlib.import_module(f"{package}.main").create_app()
    seen = set()
    for route in api_routes(app.routes):
        if route.endpoint.__name__ == "inbox_proxy":
            # Upstream-owned JSON/SSE protocol, forwarded byte-for-byte after bounds checks.
            assert not route.include_in_schema
            assert route.response_class is Response
            continue
        if route.status_code == 204:
            assert route.response_model is None and route.response_class is Response
        elif route.response_class is RedirectResponse:
            assert route.status_code == 302 and route.response_model is None
        else:
            assert route.response_model is not None, route.path
            assert_typed(route.response_model, seen)
        if route.body_field:
            assert not route.methods.intersection({"GET", "HEAD"}), route.path
            body = route.body_field.field_info.annotation
            assert isinstance(body, type) and issubclass(body, BaseModel), route.path
            assert_typed(body, seen)


@pytest.mark.parametrize("package", SERVICES)
def test_openapi_has_no_empty_request_or_response_schemas(package):
    app = importlib.import_module(f"{package}.main").create_app()
    document = app.openapi()
    for path, methods in document["paths"].items():
        for operation in methods.values():
            for parameter in operation.get("parameters", []):
                assert {"type", "$ref", "anyOf", "allOf"}.intersection(parameter["schema"]), (
                    path,
                    parameter,
                )
            for response in operation["responses"].values():
                for media in response.get("content", {}).values():
                    assert media["schema"], (path, response)
            assert "4XX" in operation["responses"] and "5XX" in operation["responses"]
            for status in ("204", "302"):
                if status in operation["responses"]:
                    assert "content" not in operation["responses"][status]
    if package != "devfeed_api":
        audience = "admin" if package == "devfeed_admin_api" else "user"
        callback = document["paths"][f"/v1/{audience}/auth/callback"]["get"]
        assert {p["name"] for p in callback["parameters"]} == {"state", "code", "error", "iss"}
    for name, schema in document["components"]["schemas"].items():
        assert schema, f"Empty schema: {name}"


@pytest.mark.parametrize("package", SERVICES)
@pytest.mark.parametrize("failure", [None, "migration", "database", "redis"])
def test_health_responses_match_success_and_failure_contracts(package, failure, monkeypatch):
    module = importlib.import_module(f"{package}.main")
    dependencies = importlib.import_module(f"{package}.dependencies")
    app = module.create_app()
    app.dependency_overrides[dependencies.get_session] = lambda: object()

    def revision(_session):
        if failure == "database":
            raise OperationalError("test", {}, RuntimeError("unavailable"))
        return "old" if failure == "migration" else module.SCHEMA_REVISION

    def ping():
        if failure == "redis":
            raise RedisConnectionError("unavailable")

    monkeypatch.setattr(module, "database_revision", revision)
    monkeypatch.setattr(module, "get_redis", lambda: SimpleNamespace(ping=ping))
    monkeypatch.setattr(module, "close_clients", lambda: None)
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        response = client.get("/health/ready")
    assert response.status_code == (503 if failure else 200)
    if failure:
        assert UnhealthyResponse.model_validate(response.json()).status == (
            "migration_required" if failure == "migration" else "unavailable"
        )
    else:
        assert response.json() == {"status": "ok"}


def test_sitemap_models_preserve_cache_headers_legacy_parts_and_optional_lastmod(monkeypatch):
    from devfeed_api.main import create_app
    from devfeed_api.sitemaps import sitemaps

    part = {"paths": ["/articles/test"], "entries": [{"path": "/articles/test"}]}
    monkeypatch.setattr(sitemaps, "part", lambda *args: part)
    with TestClient(create_app()) as client:
        response = client.get("/v1/sitemaps/articles/1")
        assert response.json() == part
        assert response.headers["cache-control"] == "public, max-age=300, s-maxage=300"
        part["entries"][0]["lastmod"] = "2026-09-13T00:00:00+00:00"
        assert client.get("/v1/sitemaps/articles/1").json() == part
        monkeypatch.setattr(sitemaps, "part", lambda *args: ["/articles/test"])
        assert client.get("/v1/sitemaps/articles/1").json() == {"paths": ["/articles/test"]}


def test_missing_or_invalid_output_is_a_server_error_and_extra_fields_are_filtered(monkeypatch):
    from devfeed_api.main import create_app
    from devfeed_api.sitemaps import sitemaps

    monkeypatch.setattr(sitemaps, "part", lambda *args: {"paths": ["/articles/test"], "secret": 1})
    with TestClient(create_app(), raise_server_exceptions=False) as client:
        assert client.get("/v1/sitemaps/articles/1").json() == {"paths": ["/articles/test"]}
        monkeypatch.setattr(sitemaps, "part", lambda *args: {"paths": [123]})
        result = client.get("/v1/sitemaps/articles/1")
        assert result.status_code == 500
        assert result.json() == {"detail": "Internal server error"}


@pytest.mark.parametrize("redis_available", [True, False])
def test_ingestion_status_validates_nullable_fields_and_timestamp_output(
    monkeypatch, redis_available
):
    from devfeed_admin_api import ingestion
    from devfeed_admin_api.auth import require_admin
    from devfeed_admin_api.dependencies import get_session
    from devfeed_admin_api.main import create_app

    app = create_app()
    session = Mock()
    session.execute.side_effect = [[("queued", 5)], [], [], [("succeeded", 2)]]
    session.scalar.side_effect = [datetime(2026, 9, 13, tzinfo=UTC), None]
    app.dependency_overrides[get_session] = lambda: session
    app.dependency_overrides[require_admin] = lambda: None
    redis = Mock()
    redis.get.return_value = b"2026-09-13T00:00:00+00:00"
    redis.llen.return_value = 5
    if not redis_available:
        redis.get.side_effect = RedisConnectionError("offline")
    monkeypatch.setattr(ingestion, "get_redis", lambda: redis)
    with TestClient(app) as client:
        response = client.get("/v1/admin/ingestion/status")
    assert response.status_code == 200
    body = ingestion.IngestionStatus.model_validate(response.json())
    assert body.jobs == {"queued": 5} and body.image_jobs == {}
    assert body.oldest_active_job_at == datetime(2026, 9, 13, tzinfo=UTC)
    assert body.oldest_active_image_job_at is None
    assert body.queue_depth == (5 if redis_available else None)
    assert body.redis_available is redis_available


def test_validation_contract_never_echoes_rejected_values():
    from devfeed_api.main import create_app

    with TestClient(create_app()) as client:
        result = client.get("/v1/sitemaps/articles/1?v=do-not-echo-this-value")
    assert result.status_code == 422
    errors = ErrorResponse.model_validate(result.json()).detail
    assert isinstance(errors, list) and errors[0].loc == ["query", "v"]
    assert "do-not-echo-this-value" not in result.text


def test_dispatched_job_metadata_is_normalized_to_json_before_validation():
    from devfeed_admin_api.jobs import job_view

    now = datetime(2026, 9, 13, tzinfo=UTC)
    job = SimpleNamespace(
        id=uuid.uuid4(),
        status="succeeded",
        attempts=1,
        created_at=now,
        available_at=now,
        dispatched_at=now,
        finished_at=now,
        error=None,
        source_id=uuid.uuid4(),
        http_status=200,
        entries_seen=3,
        articles_created=2,
        entries_skipped=1,
    )
    result = job_view(job, "ingestion").model_dump(mode="json")
    assert result["details"]["dispatched_at"] == now.isoformat()
    assert result["details"]["articles_created"] == 2
    assert result["source_id"] == str(job.source_id)
