"""No services needed: validation and authentication cover every new resource."""

import uuid

import pytest
from devfeed_admin_api import auth
from devfeed_admin_api.articles import AdminArticleCreate, AdminArticleUpdate, ClassifyArticle
from devfeed_admin_api.config import Settings
from devfeed_admin_api.dependencies import get_session
from devfeed_admin_api.main import create_app
from fastapi.testclient import TestClient
from pydantic import ValidationError


@pytest.fixture
def private_client(monkeypatch):
    settings = Settings(
        _env_file=None,
        admin_base_url="https://admin.example",
        oidc_issuer_url="https://identity.example",
        oidc_client_id="test",
        oidc_organization_id="org",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    app = create_app()
    app.dependency_overrides[get_session] = lambda: pytest.fail("Unauthenticated DB access")
    return TestClient(app)


@pytest.mark.parametrize(
    "resource", ["sources", "articles", "topics", "categories", "tags", "topic-relations"]
)
def test_crud_routes_never_bypass_admin_auth(private_client, resource):
    path = f"/v1/admin/{resource}"
    for method, url in [
        ("get", path),
        ("post", path),
        ("get", path + f"/{uuid.uuid4()}"),
        ("delete", path + f"/{uuid.uuid4()}"),
    ]:
        if resource == "topic-relations" and url != path:
            url += f"/{uuid.uuid4()}/related_to"
        assert getattr(private_client, method)(url).status_code == 401


@pytest.mark.parametrize(
    "body",
    [
        {"title": " "},
        {"title": "Title", "image_url": "http://127.0.0.1/private"},
        {"title": "Title", "published_at": "2026-01-01T00:00:00"},
        {"title": "Title", "review_status": "approved"},
        {"title": "Title", "ai_summary": "Impersonated AI output"},
    ],
)
def test_article_editor_cannot_forge_generated_or_editorial_metadata(body):
    with pytest.raises(ValidationError):
        AdminArticleUpdate.model_validate({"expected_revision": 0, **body})


def test_article_editor_requires_revision_and_source():
    with pytest.raises(ValidationError):
        AdminArticleUpdate.model_validate({"title": "Title"})
    with pytest.raises(ValidationError):
        AdminArticleCreate.model_validate(
            {"title": "Title", "canonical_url": "https://example.com/article"}
        )
    assert "expected_revision" in ClassifyArticle.model_fields
