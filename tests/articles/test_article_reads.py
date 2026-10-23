"""Public article projections must serialize disclosures without loading private state."""

import pytest
from devfeed_core import cache
from devfeed_core.article_reads import PUBLIC_ARTICLE_OPTIONS
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleOrigin, Source
from devfeed_core.schemas import ArticleOut
from devfeed_core.urls import fingerprint
from sqlalchemy import event, inspect, select
from sqlalchemy.exc import InvalidRequestError

pytestmark = pytest.mark.integration


@pytest.fixture
def disclosed_articles(database):
    with database.begin() as session:
        source = Source(
            name="Example publisher",
            feed_url="https://publisher.example/feed",
            source_type="publisher",
            approval_status="approved",
        )
        session.add(source)
        session.flush()
        rows = []
        for index, names in enumerate((["Example Partner"], [])):
            url = f"https://publisher.example/article-{index}"
            article = Article(
                canonical_url=url,
                url_hash=fingerprint(url),
                title=f"Engineering article {index}",
                summary="A useful engineering article.",
                sponsor_names=names,
                content_type="article",
                language="en",
                publication_status="published",
                review_status="approved",
                classification_provenance={"private": "editorial evidence"},
            )
            session.add(article)
            session.flush()
            session.add(
                ArticleOrigin(
                    article_id=article.id,
                    source_id=source.id,
                    original_url=url,
                    entry_key=str(index),
                )
            )
            rows.append((article.id, article.slug, names))
    return rows


def test_public_projection_serializes_disclosures_without_lazy_loading(
    database, disclosed_articles
):
    with database() as session:
        articles = session.scalars(
            select(Article)
            .options(*PUBLIC_ARTICLE_OPTIONS)
            .where(Article.id.in_([row[0] for row in disclosed_articles]))
        ).all()
        statements = []

        def capture(_connection, _cursor, statement, *_args):
            statements.append(statement)

        engine = session.get_bind()
        event.listen(engine, "before_cursor_execute", capture)
        try:
            actual = {
                article.id: ArticleOut.from_article(article).model_dump(mode="json")
                for article in articles
            }
            for article in articles:
                assert "classification_provenance" in inspect(article).unloaded
                with pytest.raises(InvalidRequestError, match="raiseload=True"):
                    _ = article.classification_provenance
                source = article.origins[0].source
                assert source.website_url is None
                assert source.feed_url == "https://publisher.example/feed"
                assert actual[article.id]["publisher"]["id"] == str(source.id)
                assert "feed_url" not in actual[article.id]["publisher"]
                assert "last_error" in inspect(source).unloaded
                with pytest.raises(InvalidRequestError, match="raiseload=True"):
                    _ = source.last_error
        finally:
            event.remove(engine, "before_cursor_execute", capture)
    assert not statements
    for identifier, _slug, names in disclosed_articles:
        assert actual[identifier]["sponsor_names"] == names
        assert "classification_provenance" not in actual[identifier]


@pytest.mark.parametrize(
    "path",
    [
        "/v1/feed?content_type=article&diverse=true&limit=24&languages=en",
        "/v1/feed?limit=24",
        "/v1/feed?sort=oldest&limit=24",
        "/v1/articles/{slug}",
    ],
)
def test_cache_miss_and_hit_preserve_article_disclosures(
    client, disclosed_articles, monkeypatch, path
):
    monkeypatch.setattr(get_settings(), "cache_enabled", True)
    cache.close_cache()
    path = path.format(slug=disclosed_articles[0][1])
    try:
        miss = client.get(path)
        assert miss.status_code == 200, miss.text
        assert miss.headers["x-cache"] == "MISS"
        body = miss.json()
        items = body.get("items", [body])
        expected = {str(row[0]): row[2] for row in disclosed_articles}
        assert items
        for item in items:
            assert item["sponsor_names"] == expected[item["id"]]
            assert "classification_provenance" not in item
        hit = client.get(path)
        assert hit.status_code == 200, hit.text
        assert hit.headers["x-cache"] == "HIT"
        assert hit.json() == body
    finally:
        cache.close_cache()
