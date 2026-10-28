"""Authenticated operator clients and editorial evidence fixtures."""

import logging
from contextlib import contextmanager
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import articles, automation, dependencies, sources
from devfeed_admin_api.auth import AdminIdentity, require_admin
from devfeed_core.schemas import SourceOut
from devfeed_http.errors import register_error_handlers
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.support.article_readiness import processing_case as processing_case
from tests.support.source_readiness import NOW, source


def identity():
    return AdminIdentity(
        subject="editor-id",
        issuer="https://identity.example",
        organization_id="editorial-org",
        roles=["superuser"],
        name="Editor",
        expires_at=4_102_444_800,
        csrf_token="session-token",
    )


@contextmanager
def operator_api(*modules):
    session, admin = Mock(spec=Session), identity()
    app = FastAPI()
    register_error_handlers(app, logging.getLogger(__name__), admin=True)

    @app.middleware("http")
    async def request_identity(request, call_next):
        request.state.request_id = "unit-operator-request"
        return await call_next(request)

    for module in modules:
        app.include_router(module.router)
    app.dependency_overrides[require_admin] = lambda: admin
    app.dependency_overrides[dependencies.get_session] = lambda: session

    @contextmanager
    def factory():
        yield session

    app.dependency_overrides[dependencies.get_session_factory] = lambda: factory
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, session=session, admin=admin, app=app)


@pytest.fixture
def editorial(processing_case, monkeypatch):
    article, evidence = processing_case
    article.origins[0].source.slug = "engineering-publisher"
    article.origins[0].source.managed_logo = {}
    for link in article.topic_links:
        link.evidence, link.origin = "Angular application routing", "manual"
    monkeypatch.setattr(articles, "record", lambda *_args, **_kwargs: article)
    with operator_api(articles, automation) as api:
        api.article, api.evidence = article, evidence
        api.session.scalar.return_value = None
        yield api


@pytest.fixture
def source_policy(monkeypatch):
    current = source(
        approval="approved",
        publication_policy="manual",
        publication_policy_revision=3,
        submission_channel="api",
        consecutive_failures=0,
        polling_mode="fixed",
        polling_state={},
        poll_interval_seconds=3600,
        next_fetch_at=NOW + timedelta(hours=1),
    )
    # Persisted source output, including a fixed creation timestamp, uses real schema validation.
    SourceOut.model_validate(current)
    monkeypatch.setattr(sources, "record", lambda *_args, **_kwargs: current)
    with operator_api(sources) as api:
        api.source = current
        yield api
