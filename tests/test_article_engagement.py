"""Article interactions are durable, deduplicated, private and visibility-gated."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from devfeed_core.db import get_engine
from devfeed_core.engagement import prune_article_opens
from devfeed_core.models import Article, ArticleEngagement, ArticleLike, ArticleOpen, utcnow
from devfeed_user_api import engagement
from devfeed_user_api.config import Settings
from sqlalchemy import event, func, insert, select, update
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


@pytest.fixture
def interactions(user_data, database, monkeypatch):
    client, current, first, second, topics = user_data
    settings = Settings(_env_file=None, base_url="http://testserver", cookie_secure=False)
    monkeypatch.setattr(engagement, "get_settings", lambda: settings)
    client.app.dependency_overrides[engagement.optional_user] = lambda: None
    with database() as session:
        ids = {row.title: row.id for row in session.execute(select(Article.title, Article.id))}
    return client, current, first, second, ids


def test_likes_are_idempotent_and_owned(interactions, database):
    client, current, first, second, ids = interactions
    article_id = ids["Article 000"]
    url = f"/v1/user/articles/{article_id}/like"
    for _ in range(2):
        result = client.put(url, json={"liked": True})
        assert result.status_code == 200 and result.json()["likes"] == 1
        assert result.json()["liked"] is True
    current.user_id, current.subject = str(second), "user-b"
    assert client.put(url, json={"liked": True}).json()["likes"] == 2
    current.user_id, current.subject = str(first), "user-a"
    assert client.put(url, json={"liked": False}).json()["likes"] == 1
    assert client.put(url, json={"liked": False}).json()["likes"] == 1
    with database() as session:
        assert list(
            session.scalars(select(ArticleLike.user_id).where(ArticleLike.article_id == article_id))
        ) == [second]
    for title in ["Article 110", "Article 111", "Article 112", "Article 113"]:
        assert (
            client.put(f"/v1/user/articles/{ids[title]}/like", json={"liked": True}).status_code
            == 404
        )


def test_anonymous_opens_deduplicate_and_retain_only_opaque_viewer_keys(interactions, database):
    client, _, _, _, ids = interactions
    article_id = ids["Article 000"]
    url = f"/v1/user/articles/{article_id}/open"
    assert client.post(url).status_code == 403
    assert client.post(url, headers={"Origin": "https://elsewhere.example"}).status_code == 403
    response = client.post(url, headers={"Origin": "http://testserver"})
    assert response.status_code == 200 and response.json()["opens"] == 1
    assert "HttpOnly" in response.headers["set-cookie"]
    assert "SameSite=lax" in response.headers["set-cookie"]
    assert response.headers["cache-control"] == "no-store"
    token = client.cookies.get("devfeed_user_visitor")
    assert client.post(url, headers={"Origin": "http://testserver"}).json()["opens"] == 1
    with database() as session:
        row = session.scalar(select(ArticleOpen).where(ArticleOpen.article_id == article_id))
        assert len(row.viewer_key) == 64 and token not in row.viewer_key
    client.cookies.clear()
    assert client.post(url, headers={"Origin": "http://testserver"}).json()["opens"] == 2
    assert (
        client.post(
            f"/v1/user/articles/{ids['Article 113']}/open",
            headers={"Origin": "http://testserver"},
        ).status_code
        == 404
    )


def test_user_opens_deduplicate_across_browsers_and_concurrent_calls(interactions, database):
    client, current, _, _, ids = interactions
    client.app.dependency_overrides[engagement.optional_user] = lambda: current
    article_id = ids["Article 000"]
    url = f"/v1/user/articles/{article_id}/open"
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(
            pool.map(lambda _: client.post(url, headers={"Origin": "http://testserver"}), range(4))
        )
    assert all(response.status_code == 200 for response in responses)
    with database() as session:
        assert (
            session.scalar(
                select(ArticleEngagement.opens).where(ArticleEngagement.article_id == article_id)
            )
            == 1
        )
        assert session.scalar(select(func.count()).select_from(ArticleOpen)) == 1


@pytest.mark.parametrize("limit", [1, 100])
def test_bulk_engagement_is_one_query_and_trending_excludes_unpublished(
    interactions, database, limit
):
    client, _, first, _, ids = interactions
    with database.begin() as session:
        session.execute(insert(ArticleLike).values(article_id=ids["Article 000"], user_id=first))
        session.execute(insert(ArticleLike).values(article_id=ids["Article 110"], user_id=first))
        session.execute(
            insert(ArticleOpen).values(
                article_id=ids["Article 001"], viewer_key="x" * 64, opened_hour=utcnow()
            )
        )
        session.execute(insert(ArticleEngagement).values(article_id=ids["Article 001"], opens=1))
    queries = []

    def count(*args):
        queries.append(args[2])

    event.listen(get_engine(), "before_cursor_execute", count)
    try:
        response = client.get(
            "/v1/user/engagement",
            params=[("article_id", str(value)) for value in list(ids.values())[:limit]],
        )
    finally:
        event.remove(get_engine(), "before_cursor_execute", count)
    assert response.status_code == 200 and len(queries) == 1
    assert all(item["liked"] is False for item in response.json())
    trending = client.get("/v1/user/trending").json()["items"]
    assert [item["id"] for item in trending] == [str(ids["Article 000"]), str(ids["Article 001"])]
    with database.begin() as session:
        session.execute(update(ArticleLike).values(created_at=utcnow() - timedelta(days=8)))
    assert [item["id"] for item in client.get("/v1/user/trending").json()["items"]] == [
        str(ids["Article 001"])
    ]


def test_retention_prunes_in_batches_without_losing_lifetime_totals(interactions, database):
    _, _, _, _, ids = interactions
    article_id = ids["Article 000"]
    with database.begin() as session:
        session.execute(insert(ArticleEngagement).values(article_id=article_id, opens=3))
        for offset in (0, 31, 32):
            session.execute(
                insert(ArticleOpen).values(
                    article_id=article_id,
                    viewer_key="x" * 64,
                    opened_hour=utcnow() - timedelta(days=offset),
                )
            )
    prune_article_opens(database, batch=1)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleOpen)) == 2
        assert session.get(ArticleEngagement, article_id).opens == 3
    prune_article_opens(database, batch=100)
    with database() as session:
        assert session.scalar(select(func.count()).select_from(ArticleOpen)) == 1
