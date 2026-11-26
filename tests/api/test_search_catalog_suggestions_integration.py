"""Public search results and moderated catalog suggestions remain available."""

import uuid
from types import SimpleNamespace

import pytest
from devfeed_core.models import Article, ArticleOrigin, ArticleTag, SearchQueryStat, Source, Tag
from devfeed_core.search_suggestions import query_hash

pytestmark = pytest.mark.integration


def test_search_results_and_public_tag_suggestions_survive_analytics_retirement(
    client, database, monkeypatch
):
    from devfeed_api import search

    with database.begin() as session:
        source = Source(
            name="Search fixture",
            source_type="publisher",
            feed_url="https://fixture.example/feed",
            approval_status="approved",
        )
        article = Article(
            title="C++ SQL example",
            canonical_url="https://fixture.example/article",
            url_hash=uuid.uuid4().hex,
            review_status="approved",
            publication_status="published",
        )
        session.add_all([source, article])
        session.flush()
        session.add(
            ArticleOrigin(
                article_id=article.id,
                source_id=source.id,
                entry_key="entry",
                original_url=article.canonical_url,
            )
        )
        for name, status, public in [
            ("C++ SQL", "approved", True),
            ("C++ pending", "pending", True),
            ("C++ rejected", "rejected", True),
            ("C++ private", "approved", False),
        ]:
            term = name.lower()
            tag = Tag(name=name, slug=term.replace("c++ ", "cpp-"))
            session.add(tag)
            session.flush()
            if public:
                session.add(ArticleTag(article_id=article.id, tag_id=tag.id))
            session.add(SearchQueryStat(query_hash=query_hash(term), query=term, status=status))
        article_id = article.id

    def index(*args, **kwargs):
        return {"articles": {"hits": [{"document": {"id": str(article_id)}}], "found": 1}}

    monkeypatch.setattr(search, "Typesense", lambda: SimpleNamespace(search=index))
    result = client.get("/v1/search", params={"q": "C++ SQL", "section": "articles"})
    assert result.status_code == 200
    item = result.json()["sections"]["articles"]["items"][0]
    assert item["id"] == str(article_id)
    assert "click_token" not in item
    assert client.post("/v1/search/analytics/click", json={}).status_code == 404
    assert client.get("/v1/search/suggestions", params={"q": "c++"}).json() == ["c++ sql"]
