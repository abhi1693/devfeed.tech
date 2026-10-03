"""Admin user inspection: access control, scoped collections and fixed query budgets."""

import uuid

import pytest
from devfeed_admin_api.main import create_app
from devfeed_core.models import (
    Article,
    ArticleBookmark,
    ArticleLike,
    UserAccount,
    UserLink,
    UserReadingDay,
    UserReadingEvent,
    UserReadingStreak,
    UserRecommendationState,
    UserStackAssociation,
    utcnow,
)
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


def test_users_are_admin_only_and_not_in_public_api(inspected_user, monkeypatch):
    from devfeed_admin_api import auth
    from devfeed_admin_api.config import Settings

    settings = Settings(
        _env_file=None,
        admin_base_url="https://admin.example.test",
        oidc_issuer_url="https://issuer.example.test",
        oidc_client_id="admin-client",
        oidc_organization_id="test-org",
    )
    monkeypatch.setattr(auth, "get_settings", lambda: settings)
    user, _, _ = inspected_user
    with TestClient(create_app()) as client:
        for suffix in (
            "",
            f"/{user}",
            f"/{user}/topics",
            f"/{user}/likes",
            f"/{user}/bookmarks",
            f"/{user}/reads",
            f"/{user}/reading-days",
            f"/{user}/interests",
            f"/{user}/recommendations",
            f"/{user}/must-reads",
        ):
            assert client.get("/v1/admin/users" + suffix).status_code == 401
        assert client.post(f"/v1/admin/users/{user}/analysis").status_code == 401
    for path, operations in create_app().openapi()["paths"].items():
        if path.startswith("/v1/admin/users"):
            assert set(operations) == ({"post"} if path.endswith("/analysis") else {"get"})
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


def test_profile_dev_card_bookmarks_and_reading_history(inspected_user, admin_client, database):
    user, other, topics = inspected_user
    with database.begin() as session:
        article = session.scalar(select(Article.id).limit(1))
        assert article is not None
        account = session.get(UserAccount, user)
        account.username = "ada-dev"
        account.about = "Builds analytical engines"
        account.profile = {
            **account.profile,
            "bio": "Developer and writer",
            "location": "London",
            "visibility": {"public": False},
            "dev_card": {
                "theme": "terminal",
                "accent": "teal",
                "motion": "static",
                "technologies": [str(topics[0])],
                "stats": ["current_streak"],
            },
        }
        account.feed_settings = {"view": "compact", "languages": ["en"]}
        session.add(
            UserLink(user_id=user, url="https://example.test/ada", label="Website", position=0)
        )
        session.add(
            UserStackAssociation(user_id=user, topic_id=topics[0], section="primary", position=0)
        )
        session.add(ArticleBookmark(user_id=user, article_id=article))
        now = utcnow()
        session.add(
            UserReadingEvent(
                user_id=user, article_id=article, read_date=now.date(), occurred_at=now
            )
        )
        session.add(
            UserReadingDay(user_id=user, read_date=now.date(), article_count=1, last_read_at=now)
        )
        session.add(
            UserReadingStreak(
                user_id=user,
                current_days=1,
                longest_days=1,
                total_days=1,
                last_read_date=now.date(),
            )
        )
    listing = admin_client.get("/v1/admin/users", params={"q": "ada-dev"}).json()
    assert listing["items"][0]["bookmarks"] == 1
    assert listing["items"][0]["last_read_at"] is not None
    detail = admin_client.get(f"/v1/admin/users/{user}").json()
    assert detail["profile_bio"] == "Developer and writer"
    assert detail["profile_public"] is False
    assert detail["profile_links"][0]["label"] == "Website"
    assert detail["dev_card"]["theme"] == "terminal"
    assert detail["dev_card_technologies"][0]["id"] == str(topics[0])
    assert detail["reading_streak"]["current_days"] == 1
    assert detail["feed_preferences"]["view"] == "compact"
    assert detail["reads"] == detail["reading_days"] == 1
    assert "never-expose" not in str(detail)
    for section in ("bookmarks", "reads", "reading-days"):
        path = f"/v1/admin/users/{user}/{section}"
        page = admin_client.get(path, params={"limit": 1}).json()
        assert page["total"] == 1 and len(page["items"]) == 1
        assert admin_client.get(f"/v1/admin/users/{other}/{section}").json()["total"] == 0
        assert admin_client.get(path, params={"sort": "invalid"}).status_code == 422
    assert admin_client.get(f"/v1/admin/users/{user}/reads").json()["items"][0][
        "article_id"
    ] == str(article)
    day = admin_client.get(f"/v1/admin/users/{user}/reading-days").json()["items"][0]
    assert day["id"] == day["read_date"] and day["article_count"] == 1


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
    profile_request(admin_client, "/v1/admin/users?limit=100", 3)
    profile_request(admin_client, f"/v1/admin/users/{user}", 7)
    with database.begin() as session:
        session.execute(
            update(UserRecommendationState)
            .where(UserRecommendationState.user_id == user)
            .values(invalidated=True)
        )
    result = admin_client.get(f"/v1/admin/users/{user}").json()
    assert result["feed_status"] == "refreshing" and result["recommendations"] > 0


def test_admin_can_queue_user_analysis_and_recover_failures(
    inspected_user, admin_client, user_data, database
):
    from datetime import timedelta

    from devfeed_core.models import UserRecommendationState, utcnow
    from devfeed_core.recommendations import dispatch_recommendations

    user, other, _ = inspected_user
    with database() as session:
        original = session.get(UserRecommendationState, user).generation
        other_due = session.get(UserRecommendationState, other).next_refresh_at
    previous = user_data[0].get("/v1/user/feed").json()
    response = admin_client.post(f"/v1/admin/users/{user}/analysis")
    assert response.status_code == 202 and response.json()["feed_status"] == "refreshing"
    refreshing = user_data[0].get("/v1/user/feed").json()
    assert refreshing["status"] == "refreshing"
    assert refreshing["items"] == previous["items"]
    assert refreshing["generation"] == str(original)
    with database.begin() as session:
        state = session.get(UserRecommendationState, user)
        assert (
            state.generation == original and state.invalidated and state.next_refresh_at <= utcnow()
        )
        assert session.get(UserRecommendationState, other).next_refresh_at == other_due
        state.dispatched_at = utcnow()
        dispatched = state.dispatched_at
    # Repeated clicks do not discard a dispatch already accepted by the queue.
    assert admin_client.post(f"/v1/admin/users/{user}/analysis").status_code == 202
    with database.begin() as session:
        state = session.get(UserRecommendationState, user)
        assert state.dispatched_at == dispatched
        state.attempts = 3
        state.next_refresh_at = utcnow() + timedelta(minutes=15)
    assert admin_client.post(f"/v1/admin/users/{user}/analysis").json()["refresh_attempts"] == 0
    queued = []

    class Queue:
        def enqueue(self, function, user_id, **kwargs):
            queued.append((function, user_id))

    dispatch_recommendations(database, Queue())
    assert ("devfeed_aggregator.recommendation_tasks.refresh", str(user)) in queued
    assert refresh_recommendations(database, user) > 0
    detail = admin_client.get(f"/v1/admin/users/{user}").json()
    assert detail["feed_status"] == "ready"
    with database() as session:
        assert session.get(UserRecommendationState, user).generation != original
    assert admin_client.post(f"/v1/admin/users/{uuid.uuid4()}/analysis").status_code == 404


def test_analysis_repairs_missing_state(inspected_user, admin_client, database):
    from devfeed_core.models import UserRecommendationState
    from sqlalchemy import delete

    user = inspected_user[0]
    with database.begin() as session:
        session.execute(
            delete(UserRecommendationState).where(UserRecommendationState.user_id == user)
        )
    assert admin_client.post(f"/v1/admin/users/{user}/analysis").status_code == 202
    assert refresh_recommendations(database, user) > 0


def test_empty_user_analysis_is_not_queued(user_data, admin_client, database):
    user_id = user_data[2]
    response = admin_client.post(f"/v1/admin/users/{user_id}/analysis")
    assert response.status_code == 409
    assert (
        response.json()["detail"] == "No follows, likes, interests, or recommendations to analyze"
    )
    with database() as session:
        state = session.get(UserRecommendationState, user_id)
        assert state.dispatched_at is None and state.computed_at is None


def test_daily_must_reads_are_the_saved_reader_selection_and_admin_reads_do_not_mutate(
    inspected_user, user_data, admin_client, database, monkeypatch
):
    from datetime import timedelta

    from devfeed_admin_api import users as admin_users
    from devfeed_core.models import UserMustRead, UserRecommendation

    user, other, _ = inspected_user
    reader = user_data[0]
    path = f"/v1/admin/users/{user}/must-reads"
    empty = admin_client.get(path).json()
    assert empty["generated"] is False and empty["items"] == []
    with database() as session:
        assert session.scalar(select(UserMustRead).where(UserMustRead.user_id == user)) is None
    daily = reader.get("/v1/user/must-reads?timezone=Asia/Kolkata").json()
    assert len(daily["items"]) == 5
    first = uuid.UUID(daily["items"][0]["id"])
    with database.begin() as session:
        session.add(
            UserReadingEvent(
                user_id=user, article_id=first, read_date=utcnow().date(), occurred_at=utcnow()
            )
        )
        session.execute(
            update(UserRecommendation)
            .where(UserRecommendation.user_id == user)
            .values(position=-UserRecommendation.position)
        )
        session.execute(
            update(UserRecommendation)
            .where(UserRecommendation.user_id == user)
            .values(position=111 + UserRecommendation.position)
        )
    _, result = profile_request(admin_client, path, 5)
    assert [item["id"] for item in result["items"]] == [item["id"] for item in daily["items"]]
    assert [item["position"] for item in result["items"]] == [1, 2, 3, 4, 5]
    assert result["selection_date"] == daily["date"] and result["timezone"] == "Asia/Kolkata"
    assert result["generated"] and result["presented_at"] is None
    assert result["items"][0]["read"]
    assert result["items"][0]["reason"] == daily["reasons"][str(first)]
    assert admin_client.get(f"/v1/admin/users/{other}/must-reads").json()["items"] == []
    assert admin_client.get(path + "?timezone=invalid/zone").status_code == 422
    assert admin_client.get(f"/v1/admin/users/{uuid.uuid4()}/must-reads").status_code == 404
    assert reader.get("/v1/user/must-reads?timezone=Asia/Kolkata").json()["presented"] is False
    with database.begin() as session:
        session.get(Article, first).publication_status = "unpublished"
    hidden = admin_client.get(path).json()
    assert len(hidden["items"]) == 4 and hidden["items"][0]["position"] == 2
    assert str(first) not in str(hidden)
    with database.begin() as session:
        session.get(UserAccount, user).feed_settings = {"languages": ["fr"]}
    assert admin_client.get(path).json()["items"] == []
    assert reader.get("/v1/user/must-reads?timezone=Asia/Kolkata").json()["items"] == []
    tomorrow = utcnow() + timedelta(days=1)
    monkeypatch.setattr(admin_users, "utcnow", lambda: tomorrow)
    tomorrow_selection = admin_client.get(path).json()
    assert tomorrow_selection["generated"] is False
    assert tomorrow_selection["items"] == []
