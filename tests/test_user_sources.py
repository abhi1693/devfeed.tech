"""Source subscriptions remain owned, bounded and part of prepared discovery."""

import uuid

import pytest
from devfeed_core.models import (
    ArticleTopic,
    RecommendationSourceEvent,
    Source,
    UserRecommendationState,
    UserSource,
    UserTopic,
    utcnow,
)
from devfeed_core.recommendations import expand_recommendation_events, refresh_recommendations
from sqlalchemy import delete, func, insert, select, update
from test_user_personalization import user_data as user_data

pytestmark = pytest.mark.integration
PATH = "/v1/user/preferences/sources"


def approved(database):
    with database() as session:
        return session.scalar(select(Source.id).where(Source.approval_status == "approved"))


def test_source_follows_are_idempotent_owned_and_independent_of_topics(user_data, database):
    client, current, first, second, topics = user_data
    source = approved(database)
    assert client.get(PATH).json() == {"source_ids": []}
    client.put("/v1/user/preferences", json={"topic_ids": [str(topics[0])]})
    for _ in range(2):
        assert client.put(f"{PATH}/{source}", json={"followed": True}).json() == {"followed": True}
    assert client.get(PATH).json() == {"source_ids": [str(source)]}
    with database() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(UserSource).where(UserSource.user_id == first)
            )
            == 1
        )
        assert (
            session.scalar(select(UserTopic.topic_id).where(UserTopic.user_id == first))
            == topics[0]
        )
    current.user_id, current.subject = str(second), "user-b"
    assert client.get(PATH).json() == {"source_ids": []}
    assert client.put(PATH, json={"source_ids": []}).status_code == 200
    current.user_id, current.subject = str(first), "user-a"
    assert client.get(PATH).json()["source_ids"] == [str(source)]
    assert client.put(PATH, json={"source_ids": []}).json() == {"source_ids": []}
    assert client.get("/v1/user/preferences").json()["topic_ids"] == [str(topics[0])]


@pytest.mark.parametrize("field", ["issuer", "subject", "organization_id"])
def test_source_preferences_bind_all_identity_fields(user_data, field):
    client, current, *_ = user_data
    setattr(current, field, "wrong")
    assert client.get(PATH).status_code == 401
    assert client.put(PATH, json={"source_ids": []}).status_code == 401


def test_source_validation_and_limit(user_data, database):
    client = user_data[0]
    with database() as session:
        rejected = session.scalar(select(Source.id).where(Source.approval_status == "rejected"))
    for source in [rejected, uuid.uuid4()]:
        assert client.put(f"{PATH}/{source}", json={"followed": True}).status_code == 422
    assert (
        client.put(PATH, json={"source_ids": [], "user_id": str(user_data[3])}).status_code == 422
    )
    ids = [uuid.uuid4() for _ in range(101)]
    with database.begin() as session:
        session.execute(
            insert(Source),
            [
                dict(
                    id=id,
                    name=str(id),
                    source_type="publisher",
                    feed_url=f"https://example.test/{id}",
                    approval_status="approved",
                )
                for id in ids
            ],
        )
    assert client.put(PATH, json={"source_ids": list(map(str, ids[:100]))}).status_code == 200
    assert client.put(f"{PATH}/{ids[100]}", json={"followed": True}).status_code == 422
    assert client.put(f"{PATH}/{ids[0]}", json={"followed": True}).status_code == 200
    assert client.put(f"{PATH}/{ids[0]}", json={"followed": False}).status_code == 200
    assert client.put(f"{PATH}/{ids[100]}", json={"followed": True}).status_code == 200


def test_sources_recommend_without_topics_and_unfollow_invalidates(user_data, database):
    client, _, user, *_ = user_data
    source = approved(database)
    with database.begin() as session:
        session.execute(delete(ArticleTopic))
    client.put(f"{PATH}/{source}", json={"followed": True})
    assert client.get("/v1/user/feed").json()["status"] == "refreshing"
    assert refresh_recommendations(database, user) == 113
    page = client.get("/v1/user/feed?limit=100").json()
    assert page["has_interests"] and len(page["items"]) == 100
    assert all(
        reason["kind"] == "followed_source"
        and reason["source_id"] == str(source)
        and reason["topic_id"] is None
        for reason in page["reasons"].values()
    )
    assert (
        len(
            client.get(
                "/v1/user/feed", params={"cursor": page["next_cursor"], "limit": 100}
            ).json()["items"]
        )
        == 13
    )
    client.put(f"{PATH}/{source}", json={"followed": False})
    assert client.get("/v1/user/feed").json()["items"] == []
    assert refresh_recommendations(database, user) == 0
    assert not client.get("/v1/user/feed").json()["has_interests"]


def test_source_changes_queue_followers_and_withdrawals_are_hidden_immediately(user_data, database):
    client, _, user, *_ = user_data
    source = approved(database)
    client.put(f"{PATH}/{source}", json={"followed": True})
    refresh_recommendations(database, user)
    with database.begin() as session:
        session.execute(delete(RecommendationSourceEvent))
        session.execute(
            update(Source).where(Source.id == source).values(approval_status="rejected")
        )
    assert client.get("/v1/user/feed").json()["items"] == []
    expand_recommendation_events(database)
    with database() as session:
        assert session.get(UserRecommendationState, user).next_refresh_at <= utcnow()
    assert refresh_recommendations(database, user) == 0
    with database.begin() as session:
        session.execute(
            update(Source).where(Source.id == source).values(approval_status="approved")
        )
    expand_recommendation_events(database)
    assert refresh_recommendations(database, user) == 113


def test_admin_source_inspection_and_graph_edges(user_data, database, admin_client):
    client, _, user, other, _ = user_data
    source = approved(database)
    client.put(f"{PATH}/{source}", json={"followed": True})
    refresh_recommendations(database, user)
    detail = admin_client.get(f"/v1/admin/users/{user}").json()
    assert detail["followed_sources"] == 1
    page = admin_client.get(f"/v1/admin/users/{user}/sources").json()
    assert page["total"] == 1 and page["items"][0]["id"] == str(source)
    assert admin_client.get(f"/v1/admin/users/{other}/sources").json()["items"] == []
    recommendations = admin_client.get(f"/v1/admin/users/{user}/recommendations").json()
    assert recommendations["items"] and all(
        row["source_id"] == str(source) for row in recommendations["items"]
    )
    response = admin_client.get(
        "/v1/admin/knowledge/graph", params={"focus": f"user:{user}", "layers": ["user", "source"]}
    )
    assert response.status_code == 200
    assert any(
        edge["source"] == f"user:{user}"
        and edge["target"] == f"source:{source}"
        and edge["kind"] == "follows"
        for edge in response.json()["edges"]
    )
