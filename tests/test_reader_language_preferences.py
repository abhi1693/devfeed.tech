"""Language preferences constrain discovery and prepared feeds before pagination."""

import pytest
from devfeed_core.models import Article, ArticleLike, Source, UserRecommendationState, utcnow
from devfeed_core.recommendations import refresh_recommendations
from sqlalchemy import select, update
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("languages", [[], ["zz"], ["en-US"], None, ["en"] * 76])
def test_reject_invalid_languages(user_data, languages):
    client = user_data[0]
    assert client.put("/v1/user/settings/feed", json={"languages": languages}).status_code == 422
    assert client.get("/v1/user/settings/feed").json()["languages"] == ["en"]


def test_languages_are_saved_per_account_and_rebuild_recommendations(user_data, database):
    client, identity, user, other, topics = user_data
    with database.begin() as session:
        articles = list(session.scalars(select(Article).order_by(Article.title)))
        for index, article in enumerate(articles):
            article.language = "fr" if index < 30 else "ja" if index < 50 else "en"
    client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]})
    assert refresh_recommendations(database, user) == 60
    old = client.get("/v1/user/feed?limit=7").json()
    response = client.put("/v1/user/settings/feed", json={"languages": ["ja", "fr", "fr"]})
    assert response.status_code == 200 and response.json()["languages"] == ["fr", "ja"]
    assert client.get("/v1/user/feed", params={"cursor": old["next_cursor"]}).status_code == 409
    with database() as session:
        state = session.get(UserRecommendationState, user)
        assert state.invalidated and state.next_refresh_at <= utcnow()
    assert refresh_recommendations(database, user) == 50
    page = client.get("/v1/user/feed?limit=100").json()
    assert len(page["items"]) == 50
    assert {item["language"] for item in page["items"]} == {"fr", "ja"}
    identity.user_id, identity.subject = str(other), "user-b"
    assert client.get("/v1/user/settings/feed").json()["languages"] == ["en"]


@pytest.mark.parametrize("sort", ["newest", "oldest", "most_liked"])
def test_public_sort_and_language_pagination(user_data, database, client, sort):
    _, _, user, other, _ = user_data
    with database.begin() as session:
        articles = list(session.scalars(select(Article).order_by(Article.title)))
        for index, article in enumerate(articles):
            article.language = "fr" if index < 24 else "ja" if index < 48 else "en"
        session.add_all(
            [
                ArticleLike(article_id=articles[2].id, user_id=user),
                ArticleLike(article_id=articles[2].id, user_id=other),
                ArticleLike(article_id=articles[6].id, user_id=user),
            ]
        )
        most_liked = str(articles[2].id)
    seen = []
    cursor = None
    for _ in range(10):
        params = [("languages", "fr"), ("languages", "ja"), ("sort", sort), ("limit", "11")]
        response = client.get("/v1/feed", params=params + ([("cursor", cursor)] if cursor else []))
        assert response.status_code == 200, response.text
        page = response.json()
        assert {item["language"] for item in page["items"]} <= {"fr", "ja"}
        if not seen and sort == "most_liked":
            assert page["items"][0]["id"] == most_liked
        seen.extend(page["items"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    assert len(seen) == len({item["id"] for item in seen}) == 48
    if sort != "most_liked":
        keys = [(item["feed_at"], item["id"]) for item in seen]
        assert keys == sorted(keys, reverse=sort == "newest")
    sources = client.get("/v1/sources?languages=fr").json()
    assert len(sources) == 1
    assert client.get("/v1/sources?languages=de").json() == []
    assert client.get("/v1/topics?languages=de").json() == []
    options = client.get("/v1/feed/options?languages=de").json()
    assert options == {"content_types": [], "sources": []}
    assert "languages" not in client.get("/v1/feed/options?languages=fr").json()


@pytest.mark.parametrize("sort", ["recommended", "newest", "most_liked"])
def test_personal_filters_and_sorts_survive_pagination(user_data, database, sort):
    client, _, user, other, topics = user_data
    with database.begin() as session:
        articles = list(session.scalars(select(Article).order_by(Article.title)))
        ids = [article.id for article in articles[:35]]
        session.execute(update(Article).where(Article.id.in_(ids)).values(content_type="news"))
        session.add_all([ArticleLike(article_id=ids[3], user_id=owner) for owner in (user, other)])
        source = session.scalar(select(Source.id).where(Source.approval_status == "approved"))
    client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]})
    refresh_recommendations(database, user)
    params = {"sort": sort, "source_id": str(source), "content_type": "news", "limit": 7}
    seen = []
    for _ in range(10):
        response = client.get("/v1/user/feed", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        assert all(item["content_type"] == "news" for item in page["items"])
        if not seen and sort == "most_liked":
            assert page["items"][0]["id"] == str(ids[3])
        seen.extend(page["items"])
        if not page["next_cursor"]:
            break
        params["cursor"] = page["next_cursor"]
    assert len(seen) == len({item["id"] for item in seen}) == 35
    if sort == "newest":
        keys = [(item["feed_at"], item["id"]) for item in seen]
        assert keys == sorted(keys, reverse=True)
