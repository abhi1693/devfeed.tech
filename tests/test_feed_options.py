import uuid

import pytest
from devfeed_core.models import Article, ArticleOrigin, Source

pytestmark = pytest.mark.integration


def test_options_use_all_visible_records_and_filter_each_facet_independently(client, database):
    with database.begin() as session:
        sources = [
            Source(
                name=name,
                feed_url=f"https://example.com/{name}",
                source_type="publisher",
                approval_status="approved",
                enabled=True,
            )
            for name in ["Python", "Rust", "Empty"]
        ]
        session.add_all(sources)
        session.flush()
        ids = [str(source.id) for source in sources]
        for title, kind, language, source, status in [
            ("Python news", "news", "en", sources[0], "published"),
            ("Python guide", "tutorial", "fr", sources[0], "published"),
            ("Rust release", "release", "en", sources[1], "published"),
            ("Hidden opinion", "opinion", "ja", sources[2], "unpublished"),
        ]:
            url = f"https://example.com/{uuid.uuid4()}"
            article = Article(
                canonical_url=url,
                url_hash=uuid.uuid4().hex,
                title=title,
                summary=title,
                content_type=kind,
                language=language,
                publication_status=status,
                review_status="approved",
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleOrigin(
                    article_id=article.id, source_id=source.id, original_url=url, entry_key=url
                )
            )
    result = client.get("/v1/feed/options").json()
    assert set(result["content_types"]) == {"news", "tutorial", "release"}
    assert {source["id"] for source in result["sources"]} == set(ids[:2])
    result = client.get("/v1/feed/options", params={"q": "Python", "content_type": "news"}).json()
    assert set(result["content_types"]) == {"news", "tutorial"}
    assert [source["id"] for source in result["sources"]] == [ids[0]]
    result = client.get("/v1/feed/options", params={"source_id": ids[1]}).json()
    assert result["content_types"] == ["release"]
    assert len(result["sources"]) == 2
    assert client.get("/v1/feed/options?q=unmatched").json() == {
        "content_types": [],
        "sources": [],
    }
    assert client.get("/v1/feed/options?topic=missing").json() == {
        "content_types": [],
        "sources": [],
    }


def test_language_filter_is_only_a_preference_list_in_api_schema(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/v1/feed", "/v1/feed/options"):
        names = {parameter["name"] for parameter in paths[path]["get"]["parameters"]}
        assert "language" not in names
        assert "languages" in names


def test_source_facet_uses_only_approved_enabled_origins_with_shared_articles(client, database):
    with database.begin() as session:
        sources = [
            Source(
                name=f"Source {index}",
                feed_url=f"https://example.test/{index}/rss",
                source_type="publisher",
                approval_status="rejected" if index == 1 else "approved",
                enabled=index != 2,
            )
            for index in range(3)
        ]
        session.add_all(sources)
        article = Article(
            title="Shared article",
            canonical_url="https://example.test/shared",
            url_hash=uuid.uuid4().hex,
            language="en",
            content_type="article",
            review_status="approved",
            publication_status="published",
        )
        session.add(article)
        session.flush()
        for index, source in enumerate(sources):
            session.add(
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    entry_key=str(index),
                    original_url=article.canonical_url,
                )
            )
        allowed = str(sources[0].id)
    for params in ({"languages": "en"}, {"languages": "en", "content_type": "article"}):
        response = client.get("/v1/feed/options", params=params)
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["sources"]] == [allowed]
    assert client.get("/v1/feed/options", params={"languages": "fr"}).json()["sources"] == []
