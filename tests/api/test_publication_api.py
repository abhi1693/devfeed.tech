import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest
from devfeed_api.dependencies import get_session
from devfeed_api.main import create_app
from devfeed_core.models import Article, ArticleOrigin, Source, Topic, utcnow
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.dialects import postgresql


def record(**changes):
    source = Source(
        id=uuid.uuid4(),
        name="Publisher",
        feed_url="https://example.com/feed",
        source_type="publisher",
        approval_status="approved",
    )
    origin = ArticleOrigin(
        id=uuid.uuid4(),
        source=source,
        source_id=source.id,
        entry_key="post",
        original_url="https://example.com/post",
        source_metadata={},
    )
    return Article(
        **{
            "id": uuid.uuid4(),
            "canonical_url": "https://example.com/post",
            "url_hash": uuid.uuid4().hex,
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

    def scalar(statement):
        scalars(statement)
        return uuid.UUID("00000000-0000-0000-0000-000000000001")

    session = SimpleNamespace(scalar=scalar, scalars=scalars)
    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as client:
        yield client, state


@pytest.mark.parametrize(
    "values",
    [
        {"publication_status": "unpublished"},
        {"review_status": "pending", "publication_status": "unpublished"},
        {"review_status": "rejected", "publication_status": "unpublished"},
        {"origins": []},
    ],
)
@pytest.mark.integration
def test_private_articles_are_not_disclosed_by_id(client, database, values):
    article = record(**values)
    with database.begin() as session:
        session.add(article)
    response = client.get(f"/v1/articles/{article.id}")
    assert response.status_code == 404
    assert "Angular" not in response.text


@pytest.mark.integration
def test_source_rejection_hides_detail_even_if_article_previously_published(client, database):
    article = record()
    article.origins[0].source.approval_status = "rejected"
    with database.begin() as session:
        session.add(article)
    assert client.get(f"/v1/articles/{article.id}").status_code == 404


@pytest.mark.integration
def test_public_detail_has_separate_source_and_ai_prose(client, database):
    article = record(ai_summary="AI generated prose")
    with database.begin() as session:
        session.add(article)
    response = client.get(f"/v1/articles/{article.id}")
    assert response.status_code == 200
    assert response.json()["summary"] == "Publisher provided text"
    assert response.json()["ai_summary"] == "AI generated prose"
    assert "classification_provenance" not in response.json()


@pytest.mark.parametrize("topic", ["angular", "00000000-0000-0000-0000-000000000001"])
def test_topic_query_filters_direct_context_and_never_expands_sibling_graph(reader, topic):
    client, state = reader
    response = client.get("/v1/feed", params={"topic": topic})
    assert response.status_code == 200
    sql = "\n".join(state.queries)
    assert "articles.publication_status = 'published'" in sql
    assert "articles.review_status = 'approved'" in sql
    assert "sources.approval_status = 'approved'" in sql
    column = "slug" if topic == "angular" else "id"
    assert f"topics.{column} = '{topic}'" in sql
    assert "topics.status = 'active'" in sql
    assert "'primary', 'supporting'" in sql
    assert "topic_relations" not in sql and "'react'" not in sql


def test_no_public_editorial_or_ai_execution_endpoints(reader):
    client, _ = reader
    paths = client.get("/openapi.json").json()["paths"]
    assert "/v1/topics" in paths
    assert not any("approve" in path or "publish" in path or "analyze" in path for path in paths)


@pytest.mark.integration
@pytest.mark.parametrize("sort", ["newest", "oldest", "most_liked"])
def test_topic_uuid_and_slug_match_across_pages_options_and_visibility(
    client, database, publish_for_read_test, sort
):
    articles = []
    for index in range(2):
        article = record(
            publication_status="unpublished",
            slug=f"routing-{index}",
            canonical_url=f"https://example.com/{index}",
            feed_at=utcnow() - timedelta(minutes=index),
        )
        article.origins[0].source.feed_url = f"https://example.com/feed/{index}"
        article.origins[0].source.slug = f"publisher-{index}"
        articles.append(article)
    article_ids = {str(article.id) for article in articles}
    with database.begin() as session:
        session.add_all(articles)
    publish_for_read_test()
    with database() as session:
        topic = session.scalar(select(Topic).where(Topic.slug == "fixture-engineering"))
        topic_id, slug = topic.id, topic.slug

    pages = []
    for identity in (slug, str(topic_id), str(topic_id).upper()):
        params = {"topic": identity, "sort": sort, "limit": 1, "languages": "en"}
        first = client.get("/v1/feed", params=params)
        assert first.status_code == 200
        cursor = first.json()["next_cursor"]
        assert cursor
        second = client.get("/v1/feed", params={**params, "cursor": cursor})
        assert second.status_code == 200
        assert second.json()["next_cursor"] is None
        ids = [item["id"] for page in (first, second) for item in page.json()["items"]]
        assert set(ids) == article_ids and len(ids) == 2
        pages.append(ids)
    assert pages[0] == pages[1] == pages[2]

    options = client.get("/v1/feed/options", params={"topic": slug})
    assert options.status_code == 200 and options.json()["content_types"] == ["tutorial"]
    assert client.get("/v1/feed/options", params={"topic": str(topic_id)}).json() == options.json()

    with database.begin() as session:
        session.get(Topic, topic_id).status = "rejected"
    for identity in (slug, str(topic_id), str(uuid.uuid4()), "missing-topic"):
        feed = client.get("/v1/feed", params={"topic": identity, "sort": sort})
        assert feed.status_code == 200 and feed.json()["items"] == []
        assert client.get("/v1/feed/options", params={"topic": identity}).json() == {
            "content_types": [],
            "sources": [],
        }


def test_tag_directory_filters_keep_publication_gate_and_escape_search(reader):
    client, state = reader
    response = client.get("/v1/tags", params={"q": "C%_", "letter": "C", "offset": 60})
    assert response.status_code == 200
    query = state.queries[-1]
    assert "publication_status = 'published'" in query
    assert "review_status = 'approved'" in query
    assert "C/%%/_" in query
    assert "upper(substr(tags.name, 1, 1)) = 'C'" in query
    assert "lower(tags.name), tags.slug" in query
    assert "OFFSET 60" in query


def test_tag_directory_other_initials_and_validation(reader):
    client, state = reader
    assert client.get("/v1/tags", params={"letter": "#"}).status_code == 200
    assert "NOT BETWEEN 'A' AND 'Z'" in state.queries[-1]
    assert client.get("/v1/tags", params={"letter": "AB"}).status_code == 422
    assert client.get("/v1/tags", params={"q": "x" * 201}).status_code == 422


@pytest.mark.integration
def test_tag_directory_search_and_initials_on_published_articles(client, database):
    from devfeed_core.models import Tag

    public = record()
    public.tags = [
        Tag(name=name, slug=slug)
        for name, slug in [
            ("C++", "c++"),
            ("CSS", "css"),
            ("C%_", "c-percent"),
            (".NET", ".net"),
            ("数据库", "数据库"),
        ]
    ]
    private = record(
        publication_status="unpublished",
        canonical_url="https://example.com/private",
    )
    private.origins[0].source = public.origins[0].source
    private.origins[0].entry_key = "private"
    private.tags = [Tag(name="Confidential", slug="confidential")]
    with database.begin() as session:
        session.add_all([public, private, Tag(name="Catalog only", slug="catalog-only")])

    def names(**params):
        response = client.get("/v1/tags", params=params)
        assert response.status_code == 200
        return [tag["name"] for tag in response.json()]

    assert names(letter="C") == ["C%_", "C++", "CSS"]
    assert names(q="c++") == ["C++"]
    assert names(q="%_") == ["C%_"]
    assert names(letter="#") == [".NET", "数据库"]
    assert names(letter="C", q="ss") == ["CSS"]
    assert names(letter="C", offset=1, limit=1) == ["C++"]
    assert names(q="Confidential") == []
    assert names(q="Catalog only") == []
