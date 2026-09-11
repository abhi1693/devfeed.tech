"""Admin user inspection: access control, scoped collections and fixed query budgets."""

import uuid

import pytest
from devfeed_admin_api.main import create_app
from devfeed_core.models import Article, ArticleLike, UserAccount, UserRecommendationState
from devfeed_core.recommendations import refresh_recommendations
from fastapi.testclient import TestClient
from sqlalchemy import insert, select, update
from test_api_query_budgets import profile_request
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration


@pytest.fixture
def inspected_user(user_data, database):
    client, _, user, other, topics = user_data
    with database.begin() as session:
        session.execute(
            update(UserAccount)
            .where(UserAccount.id == user)
            .values(
                profile={
                    "display_name": "Ada Lovelace",
                    "avatar_url": "https://example.test/ada.png",
                    "private": "never-expose",
                }
            )
        )
        articles = list(session.scalars(select(Article.id).order_by(Article.id).limit(110)))
        session.execute(
            insert(ArticleLike), [dict(user_id=user, article_id=article) for article in articles]
        )
    client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]})
    refresh_recommendations(database, user)
    return user, other, topics


def test_users_are_admin_only_read_only_and_not_in_public_api(inspected_user):
    user, _, _ = inspected_user
    with TestClient(create_app()) as client:
        for suffix in (
            "",
            f"/{user}",
            f"/{user}/topics",
            f"/{user}/likes",
            f"/{user}/interests",
            f"/{user}/recommendations",
        ):
            assert client.get("/v1/admin/users" + suffix).status_code == 401
    for path, operations in create_app().openapi()["paths"].items():
        if path.startswith("/v1/admin/users"):
            assert set(operations) == {"get"}
    from devfeed_api.main import create_app as public_app

    assert not any(path.startswith("/v1/admin/users") for path in public_app().openapi()["paths"])


def test_user_search_filters_details_and_private_field_allowlist(inspected_user, admin_client):
    user, other, topics = inspected_user
    for query in ("Ada", "same@example.test", str(user)):
        result = admin_client.get("/v1/admin/users", params={"q": query}).json()
        assert str(user) in [item["id"] for item in result["items"]]
    assert admin_client.get("/v1/admin/users?q=%25_").json()["total"] == 0
    for value in ("following", "liked"):
        page = admin_client.get("/v1/admin/users", params={"interests": value}).json()
        assert [item["id"] for item in page["items"]] == [str(user)]
    assert admin_client.get("/v1/admin/users?interests=none").json()["items"][0]["id"] == str(other)
    detail = admin_client.get(f"/v1/admin/users/{user}").json()
    assert detail["name"] == "Ada Lovelace" and detail["sign_in_name"] == "User"
    assert detail["followed_topics"] == 1 and detail["liked_articles"] == 110
    assert detail["feed_status"] == "ready" and detail["recommendations"] > 0
    assert set(detail).isdisjoint({"issuer", "subject", "organization_id", "profile", "private"})
    assert "never-expose" not in str(detail)
    assert admin_client.get(f"/v1/admin/users/{uuid.uuid4()}").status_code == 404
    assert admin_client.get("/v1/admin/users?sort=subject").status_code == 422
    assert admin_client.get("/v1/admin/users?interests=invalid").status_code == 422


@pytest.mark.parametrize(
    "section,budget", [("topics", 4), ("likes", 4), ("interests", 4), ("recommendations", 5)]
)
def test_nested_collections_are_scoped_bounded_and_searchable(
    inspected_user, admin_client, section, budget
):
    user, other, _ = inspected_user
    path = f"/v1/admin/users/{user}/{section}"
    for limit in (1, 100):
        _, page = profile_request(admin_client, f"{path}?limit={limit}", budget)
        assert len(page["items"]) <= limit and page["total"] > 0
        assert len({item["id"] for item in page["items"]}) == len(page["items"])
    first = admin_client.get(path, params={"limit": 1}).json()
    name = first["items"][0].get("name") or first["items"][0]["title"]
    matched = admin_client.get(path, params={"q": name}).json()
    assert first["items"][0]["id"] in [item["id"] for item in matched["items"]]
    assert admin_client.get(path, params={"q": "not-present-anywhere"}).json()["total"] == 0
    assert admin_client.get(f"/v1/admin/users/{other}/{section}").json()["total"] == 0
    assert admin_client.get(f"/v1/admin/users/{uuid.uuid4()}/{section}").status_code == 404
    assert admin_client.get(path, params={"limit": 101}).status_code == 422
    assert admin_client.get(path, params={"offset": -1}).status_code == 422
    assert admin_client.get(path, params={"sort": "invalid"}).status_code == 422
    if first["total"] > 1:
        second = admin_client.get(path, params={"limit": 1, "offset": 1}).json()
        assert first["items"][0]["id"] != second["items"][0]["id"]


def test_user_query_budget_and_stale_recommendation_status(inspected_user, admin_client, database):
    user, _, _ = inspected_user
    profile_request(admin_client, "/v1/admin/users?limit=100", 2)
    profile_request(admin_client, f"/v1/admin/users/{user}", 3)
    with database.begin() as session:
        session.execute(
            update(UserRecommendationState)
            .where(UserRecommendationState.user_id == user)
            .values(invalidated=True)
        )
    result = admin_client.get(f"/v1/admin/users/{user}").json()
    assert result["feed_status"] == "refreshing" and result["recommendations"] > 0
