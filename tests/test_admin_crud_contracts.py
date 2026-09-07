"""No services needed: validation and authentication cover every new resource."""

import uuid
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from devfeed_admin_api import articles as article_routes
from devfeed_admin_api import auth
from devfeed_admin_api.articles import AdminArticleCreate, AdminArticleUpdate, ClassifyArticle
from devfeed_admin_api.config import Settings
from devfeed_admin_api.dependencies import get_session
from devfeed_admin_api.main import create_app
from devfeed_core.models import Article
from devfeed_core.services import OperationConflict
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


@pytest.mark.parametrize("resource", ["sources", "articles", "topics", "tags", "topic-relations"])
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


def test_unified_analysis_list_requires_admin_auth(private_client):
    assert private_client.get("/v1/admin/jobs/ai-analysis").status_code == 401


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


@pytest.mark.parametrize("active", [True, False])
def test_article_edit_conflicts_with_active_page_enrichment(monkeypatch, active):
    identifier = uuid.uuid4()
    before = AdminArticleUpdate(
        title="Original title", summary="Original summary", expected_revision=3
    )
    article = Article(
        id=identifier,
        metadata_source_type="aggregator",
        editorial_revision=3,
        **before.model_dump(exclude={"expected_revision"}),
    )
    session = Mock()
    session.scalar.return_value = uuid.uuid4() if active else None
    lookup = Mock(return_value=article)
    monkeypatch.setattr(article_routes, "record", lookup)
    monkeypatch.setattr(article_routes, "article_view", lambda value: value)
    changes = before.model_copy(update={"title": "Human title", "summary": "Human summary"})
    admin = SimpleNamespace(subject="editor")
    if active:
        with pytest.raises(OperationConflict, match="enrichment is queued or running"):
            article_routes.update(identifier, changes, session, admin)
        assert article.title == "Original title" and article.summary == "Original summary"
        assert article.editorial_revision == 3
        session.add.assert_not_called()
        session.commit.assert_not_called()
    else:
        result = article_routes.update(identifier, changes, session, admin)
        assert result.title == "Human title" and result.summary == "Human summary"
        assert result.editorial_revision == 4
        session.commit.assert_called_once()
    lookup.assert_called_once_with(session, Article, identifier, lock=True)
    statement = session.scalar.call_args.args[0]
    parameters = statement.compile().params
    assert identifier in parameters.values()
    assert ["queued", "running"] in parameters.values()
    assert statement._for_update_arg is None  # Avoid the worker's job -> article lock inversion.


def test_unchanged_article_save_does_not_conflict_with_enrichment(monkeypatch):
    body = AdminArticleUpdate(title="Unchanged", expected_revision=2)
    article = Article(editorial_revision=2, **body.model_dump(exclude={"expected_revision"}))
    session = Mock()
    monkeypatch.setattr(article_routes, "record", lambda *args, **kwargs: article)
    monkeypatch.setattr(article_routes, "article_view", lambda value: value)
    assert (
        article_routes.update(uuid.uuid4(), body, session, SimpleNamespace(subject="editor"))
        is article
    )
    session.scalar.assert_not_called()
    session.add.assert_not_called()
    assert article.editorial_revision == 2
