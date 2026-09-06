import uuid
from types import SimpleNamespace

import pytest
from devfeed_api.dependencies import get_session
from devfeed_api.main import create_app
from devfeed_core.models import Article, ArticleOrigin, Source, utcnow
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql


def record(**changes):
    source = Source(
        id=uuid.uuid4(), name="Publisher", source_type="publisher", approval_status="approved"
    )
    origin = ArticleOrigin(
        id=uuid.uuid4(),
        source=source,
        source_id=source.id,
        original_url="https://example.com/post",
        source_metadata={},
    )
    return Article(
        **{
            "id": uuid.uuid4(),
            "canonical_url": "https://example.com/post",
            "title": "Angular routing",
            "summary": "Publisher provided text",
            "language": "en",
            "content_type": "tutorial",
            "content_format": "article",
            "feed_at": utcnow(),
            "discovered_at": utcnow(),
            "review_status": "approved",
            "publication_status": "published",
            "origins": [origin],
            **changes,
        }
    )


@pytest.fixture
def reader():
    app = create_app()
    state = SimpleNamespace(article=None, queries=[])

    def scalars(statement):
        state.queries.append(
            str(
                statement.compile(
                    dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
                )
            )
        )
        return SimpleNamespace(all=lambda: [])

    session = SimpleNamespace(get=lambda *_: state.article, scalars=scalars)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client, state


@pytest.mark.parametrize(
    "values",
    [
        {"publication_status": "unpublished"},
        {"review_status": "pending"},
        {"review_status": "rejected"},
        {"origins": []},
    ],
)
def test_private_articles_are_not_disclosed_by_id(reader, values):
    client, state = reader
    state.article = record(**values)
    response = client.get(f"/v1/articles/{state.article.id}")
    assert response.status_code == 404
    assert "Angular" not in response.text


def test_source_rejection_hides_detail_even_if_article_previously_published(reader):
    client, state = reader
    state.article = record()
    state.article.origins[0].source.approval_status = "rejected"
    assert client.get(f"/v1/articles/{state.article.id}").status_code == 404


def test_public_detail_has_separate_source_and_ai_prose(reader):
    client, state = reader
    state.article = record(ai_summary="AI generated prose")
    response = client.get(f"/v1/articles/{state.article.id}")
    assert response.status_code == 200
    assert response.json()["summary"] == "Publisher provided text"
    assert response.json()["ai_summary"] == "AI generated prose"
    assert "classification_provenance" not in response.json()


def test_topic_query_filters_direct_context_and_never_expands_sibling_graph(reader):
    client, state = reader
    response = client.get("/v1/feed?topic=angular")
    assert response.status_code == 200
    sql = state.queries[-1]
    assert "articles.publication_status = 'published'" in sql
    assert "articles.review_status = 'approved'" in sql
    assert "sources.approval_status = 'approved'" in sql
    assert "topics.slug = 'angular'" in sql
    assert "'primary', 'supporting'" in sql
    assert "topic_relations" not in sql and "'react'" not in sql


def test_no_public_editorial_or_ai_execution_endpoints(reader):
    client, _ = reader
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/topics" in paths
    assert not any("approve" in path or "publish" in path or "analyze" in path for path in paths)
