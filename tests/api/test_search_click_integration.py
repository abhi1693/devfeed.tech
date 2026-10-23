"""Receipts, visibility and shared Redis budgets protect real database writes."""

import uuid
from types import SimpleNamespace

import pytest
from devfeed_core.config import get_settings
from devfeed_core.models import Article, ArticleOrigin, SearchQueryStat, Source
from devfeed_core.search_suggestions import approved_suggestions, query_hash
from pydantic import SecretStr
from sqlalchemy import func, select

pytestmark = pytest.mark.integration


def test_verified_search_clicks_are_bounded_and_still_require_moderation(
    client, database, monkeypatch
):
    from devfeed_api import search

    get_settings().search_query_key = SecretStr("disposable-query-key")
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
        article_id = article.id

    def index(*args, **kwargs):
        return {"articles": {"hits": [{"document": {"id": str(article_id)}}], "found": 1}}

    monkeypatch.setattr(search, "Typesense", lambda: SimpleNamespace(search=index))
    result = client.get("/v1/search", params={"q": "C++ SQL", "section": "articles"})
    assert result.status_code == 200
    item = result.json()["sections"]["articles"]["items"][0]
    event = {
        "query": "C++ SQL",
        "result_kind": "articles",
        "result_id": item["id"],
        "click_token": item["click_token"],
    }
    assert (
        client.post(
            "/v1/search/analytics/click", json={**event, "query": "no-results arbitrary"}
        ).status_code
        == 403
    )
    for _ in range(20):
        assert client.post("/v1/search/analytics/click", json=event).status_code == 204
    limited = client.post("/v1/search/analytics/click", json=event)
    assert limited.status_code == 429 and 1 <= int(limited.headers["retry-after"]) <= 60
    with database.begin() as session:
        row = session.get(SearchQueryStat, query_hash("c++ sql"))
        assert row.query == "c++ sql" and row.successful_count == 20
        assert row.status == "pending"
        assert session.scalar(select(func.count()).select_from(SearchQueryStat)) == 1
        assert approved_suggestions(session, "c++", limit=5, minimum=1) == []
        # Even an unexpired receipt loses permission when the article is unpublished.
        session.get(Article, article_id).publication_status = "unpublished"
    # A different canonical query has a fresh per-query budget, so the visibility
    # check is reached instead of simply repeating the exhausted budget.
    event = {
        **event,
        "query": "SQL C++",
        "click_token": search.click_tokens().issue("SQL C++", "articles", article_id),
    }
    assert client.post("/v1/search/analytics/click", json=event).status_code == 403
    with database() as session:
        assert session.scalar(select(func.count()).select_from(SearchQueryStat)) == 1
